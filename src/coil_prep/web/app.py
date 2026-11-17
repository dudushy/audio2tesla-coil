"""API web do coil-prep: recebe link ou arquivo, gera a melodia em onda quadrada e serve o áudio."""

import hashlib
import json
import os
import re
import threading
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import feedback
from .. import notes as notes_mod
from ..calibrate import DEFAULT_FREQS, square_tones, write_mono_wav
from ..fetch import is_url
from ..ffmpeg import wav_to_mp3
from ..midi import MIDI_EXTS, RATE, render
from ..pipeline import PEAK_DB, STEM_CHOICES, apply_edit
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


class NoteIn(BaseModel):
    id: int | None = None
    start: float
    end: float
    pitch: int


class NotesIn(BaseModel):
    notes: list[NoteIn]


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
    edit_lock = threading.Lock()
    app = FastAPI(title="coil-prep", docs_url=None, redoc_url=None)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")

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

    def render_speed(job, wav: Path, speed: float) -> Path:
        """Re-renderiza as notas atuais com os tempos esticados (mantém o tom; sem time-stretch)."""
        out = wav.with_name(f"{wav.stem}.x{speed:g}.wav")
        r = job.result
        with edit_lock:
            current = notes_mod.load(r.notes_file)["current"]
            segs = [(n.start / speed, n.end / speed, (n.pitch,)) for n in current]
            write_mono_wav(out, render(segs, r.min_hz, r.max_hz, PEAK_DB), RATE)
        return out

    @app.get("/api/jobs/{job_id}/audio/{which}")
    def job_audio(
        job_id: str, which: str, download: bool = False, format: str = "wav", speed: float = 1.0
    ):
        job = jobs.get(job_id)
        if job is None or job.result is None:
            raise HTTPException(404, "áudio indisponível")
        path = {"result": job.result.wav, "original": job.result.original}.get(which)
        if path is None or not path.is_file():
            raise HTTPException(404, "áudio indisponível")
        if format not in ("wav", "mp3"):
            raise HTTPException(422, "format deve ser wav ou mp3")
        if format == "mp3":
            if which != "result":
                raise HTTPException(422, "MP3 só para o resultado")
            mp3 = path.with_suffix(".mp3")  # regenerado a cada pedido: o WAV pode ter sido editado
            with edit_lock:
                wav_to_mp3(path, mp3)
            if download:
                record_edit(job)
            return FileResponse(
                mp3, media_type="audio/mpeg", filename=mp3.name if download else None
            )
        if which == "result" and speed != 1.0:
            if not 0.5 <= speed <= 2.0:
                raise HTTPException(422, "speed deve estar entre 0.5 e 2")
            path = render_speed(job, path, speed)
        if download and which == "result":
            record_edit(job)
        return FileResponse(path, filename=path.name if download else None)

    def record_edit(job) -> bool:
        """Registra no feedback o diff gerado x final (uma vez por estado das notas)."""
        r = job.result
        data = notes_mod.load(r.notes_file)
        diff = feedback.diff_notes(data["generated"], data["current"])
        if not feedback.has_changes(diff):
            return False
        key = hashlib.sha1(json.dumps([asdict(n) for n in data["current"]]).encode()).hexdigest()
        if job.last_edit == key:
            return False
        job.last_edit = key
        sidecar = data_dir / "feedback" / f"{job.id}.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps(
                {
                    "job_id": job.id,
                    "source": job.source,
                    "stem": r.stem,
                    "min_hz": r.min_hz,
                    "max_hz": r.max_hz,
                    "generated": [asdict(n) for n in data["generated"]],
                    "final": [asdict(n) for n in data["current"]],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        feedback.record(
            data_dir,
            {
                "kind": "edit",
                "job_id": job.id,
                "source": job.source,
                "stem": r.stem,
                "min_hz": r.min_hz,
                "max_hz": r.max_hz,
                **diff,
                "notes_file": f"feedback/{job.id}.json",
            },
        )
        return True

    def notes_payload(job, generated, current):
        r = job.result
        low, high = notes_mod.pitch_range(r.min_hz, r.max_hz)
        return {
            "min_hz": r.min_hz,
            "max_hz": r.max_hz,
            "min_pitch": low,
            "max_pitch": high,
            "generated": [asdict(n) for n in generated],
            "notes": [asdict(n) for n in current],
            "peak_db": round(r.peak_db, 1),
            "ok": r.ok,
        }

    @app.get("/api/jobs/{job_id}/notes")
    def get_notes(job_id: str):
        job = jobs.get(job_id)
        if job is None or job.result is None:
            raise HTTPException(404, "notas indisponíveis")
        data = notes_mod.load(job.result.notes_file)
        return notes_payload(job, data["generated"], data["current"])

    @app.put("/api/jobs/{job_id}/notes")
    def put_notes(job_id: str, body: NotesIn):
        job = jobs.get(job_id)
        if job is None or job.result is None:
            raise HTTPException(404, "notas indisponíveis")
        with edit_lock:
            try:
                job.result, cleaned = apply_edit(job.result, [n.model_dump() for n in body.notes])
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
            generated = notes_mod.load(job.result.notes_file)["generated"]
        return notes_payload(job, generated, cleaned)

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
        edited = record_edit(job)
        feedback.record(
            data_dir,
            {
                "kind": "rating",
                "job_id": job.id,
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
        return {"ok": True, "edit_recorded": edited}

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
            "ratings": feedback.count_ratings(history),
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

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def create_default_app() -> FastAPI:
    """Fábrica para o uvicorn (`--factory`): COIL_DATA define a pasta de dados."""
    return create_app(Path(os.environ.get("COIL_DATA", "data")))
