"""API web do coil-prep: recebe link ou arquivo, gera a melodia em onda quadrada e serve o áudio."""

import hashlib
import os
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..fetch import is_url
from ..midi import MIDI_EXTS
from ..pipeline import STEM_CHOICES
from .jobs import JobManager

STATIC = Path(__file__).parent / "static"
UPLOAD_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a", ".webm", *MIDI_EXTS}
MAX_UPLOAD = 100 * 1024 * 1024  # 100 MB


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

    return app


def create_default_app() -> FastAPI:
    """Fábrica para o uvicorn (`--factory`): COIL_DATA define a pasta de dados."""
    return create_app(Path(os.environ.get("COIL_DATA", "data")))
