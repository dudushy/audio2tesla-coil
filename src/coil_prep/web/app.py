"""API web do coil-prep: recebe link ou arquivo, gera a melodia em onda quadrada e serve o áudio."""

import hashlib
import os
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .. import feedback
from ..calibrate import DEFAULT_FREQS, square_tones
from ..fetch import is_url
from ..midi import MIDI_EXTS, RATE
from ..pipeline import STEM_CHOICES
from ..profile import Profile, load_profile, save_profile
from .jobs import JobManager

STATIC = Path(__file__).parent / "static"
UPLOAD_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a", ".webm", *MIDI_EXTS}
MAX_UPLOAD = 100 * 1024 * 1024  # 100 MB
TONE_SECONDS, TONE_GAP = 1.5, 0.5
TONE_PEAK_DB = -8.0  # mesmo nível da saída do app


class FeedbackIn(BaseModel):
    rating: int
    tag: str | None = None


class ProfileIn(BaseModel):
    min_hz: float
    max_hz: float


def tones_wav() -> bytes:
    import io
    import wave

    import numpy as np

    samples = square_tones(DEFAULT_FREQS, TONE_SECONDS, TONE_GAP, TONE_PEAK_DB)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def safe_name(filename: str) -> tuple[str, str]:
    """(nome limpo, extensão minúscula): remove diretórios e caracteres fora de [\\w.-]."""
    path = Path(filename.replace("\\", "/")).name
    stem = re.sub(r"[^\w.-]+", "_", Path(path).stem).strip("._")[:60] or "audio"
    return stem, Path(path).suffix.lower()


def create_app(data_dir: Path, manager: JobManager | None = None) -> FastAPI:
    data_dir.mkdir(parents=True, exist_ok=True)
    jobs = manager or JobManager(data_dir)
    app = FastAPI(title="coil-prep", docs_url=None, redoc_url=None)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/jobs")
    async def create_job(
        stem: str = Form("other"),
        url: str = Form(""),
        file: UploadFile | None = File(None),  # noqa: B008
    ):
        if stem not in STEM_CHOICES:
            raise HTTPException(422, f"stem inválido: {stem}")
        url = url.strip()
        if bool(url) == (file is not None and bool(file.filename)):
            raise HTTPException(422, "envie um link OU um arquivo")
        if url:
            if not is_url(url):
                raise HTTPException(422, "o link precisa começar com http:// ou https://")
            source = url
        else:
            name, ext = safe_name(file.filename)
            if ext not in UPLOAD_EXTS:
                raise HTTPException(422, f"formato não suportado: {ext or '(sem extensão)'}")
            data = await file.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "arquivo maior que 100 MB")
            # hash no nome: o mesmo arquivo reaproveita o cache do Demucs ao testar outro stem
            dest = data_dir / "input" / f"{name}-{hashlib.sha1(data).hexdigest()[:8]}{ext}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            source = str(dest)
        return jobs.submit(source, stem).to_dict()

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job não encontrado")
        return job.to_dict()

    @app.get("/api/jobs/{job_id}/audio/{which}")
    def job_audio(job_id: str, which: str, download: bool = False):
        job = jobs.get(job_id)
        if job is None or job.result is None:
            raise HTTPException(404, "áudio indisponível")
        path = {"result": job.result.wav, "original": job.result.original}.get(which)
        if path is None or not path.is_file():
            raise HTTPException(404, "áudio indisponível")
        return FileResponse(path, filename=path.name if download else None)

    @app.post("/api/jobs/{job_id}/feedback")
    def job_feedback(job_id: str, body: FeedbackIn):
        job = jobs.get(job_id)
        if job is None or job.result is None:
            raise HTTPException(404, "job não encontrado ou ainda sem resultado")
        if body.rating not in (1, -1):
            raise HTTPException(422, "rating deve ser 1 ou -1")
        if body.tag is not None and body.tag not in feedback.TAGS:
            raise HTTPException(422, "tag inválida")
        if job.rated:
            raise HTTPException(409, "este resultado já foi avaliado")
        job.rated = True
        r = job.result
        feedback.record(
            data_dir,
            {
                "source": job.source,
                "stem": r.stem,
                "min_hz": r.min_hz,
                "max_hz": r.max_hz,
                "notes": r.notes,
                "overlap": round(r.overlap, 3),
                "rating": body.rating,
                "tag": body.tag if body.rating == -1 else None,
            },
        )
        return {"ok": True}

    @app.get("/api/state")
    def state():
        try:
            p = load_profile(data_dir / "device.json")
            profile = {"min_hz": p.min_hz, "max_hz": p.max_hz}
        except ValueError:
            profile = None
        history = feedback.load(data_dir)
        return {
            "profile": profile,
            "suggestion": feedback.suggest_stem(history),
            "ratings": len(history),
            "tags": list(feedback.TAGS),
        }

    @app.get("/api/calibrate")
    def calibrate_info():
        step = TONE_SECONDS + TONE_GAP
        return {"tones": [{"start": i * step, "hz": f} for i, f in enumerate(DEFAULT_FREQS)]}

    @app.get("/api/calibrate/tones.wav")
    def calibrate_tones():
        return Response(tones_wav(), media_type="audio/wav")

    @app.put("/api/profile")
    def save_profile_route(body: ProfileIn):
        try:
            profile = Profile(body.min_hz, body.max_hz)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        if profile.max_hz < 2 * profile.min_hz:
            raise HTTPException(422, "a faixa precisa ter pelo menos 1 oitava (máx >= 2x mín)")
        save_profile(profile, data_dir / "device.json")
        return {"min_hz": profile.min_hz, "max_hz": profile.max_hz}

    return app


def create_default_app() -> FastAPI:
    """Fábrica para o uvicorn (`--factory`): COIL_DATA define a pasta de dados."""
    return create_app(Path(os.environ.get("COIL_DATA", "data")))
