"""Fila de jobs em memória com 1 worker: o Demucs em CPU é pesado, paralelizar só travaria a máquina."""

import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..fetch import FetchError, is_url
from ..ffmpeg import FfmpegError
from ..pipeline import (
    FAST_NOTE_MS,
    MOVED_WARN,
    OUT_OF_RANGE_WARN,
    OVERLAP_WARN,
    Result,
    process,
)
from ..profile import load_profile

MAX_JOBS = 50  # histórico em memória; os arquivos ficam no volume


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | done | error
    message: str = "Na fila..."
    error: str | None = None
    result: Result | None = None
    source: str = ""  # link ou nome do arquivo, para o histórico de avaliações
    rated: bool = False
    last_edit: str = ""  # hash das notas já registradas como edição no feedback
    created: float = field(default_factory=lambda: __import__("time").time())

    def to_dict(self) -> dict:
        data = {"id": self.id, "status": self.status, "message": self.message, "error": self.error}
        r = self.result
        if r is not None:
            data["result"] = {
                "stem": r.stem,
                "min_hz": r.min_hz,
                "max_hz": r.max_hz,
                "notes": r.notes,
                "segments": r.segments,
                "overlap": round(r.overlap, 3),
                "overlap_warning": r.overlap > OVERLAP_WARN,
                "shift": r.shift,
                "out_of_range": round(r.out_of_range, 3),
                "median_note_ms": round(r.median_note_ms),
                "moved": round(r.moved, 3),
                "moved_warning": r.moved > MOVED_WARN,
                "range_warning": r.out_of_range > OUT_OF_RANGE_WARN,
                "fast_warning": r.median_note_ms < FAST_NOTE_MS,
                "peak_db": round(r.peak_db, 1),
                "ok": r.ok,
                "has_original": r.original is not None,
                "filename": r.wav.name,
                "parts": [{**p, "chosen": p["id"] == r.part} for p in r.parts],
                "part": r.part,
            }
        return data


class JobManager:
    def __init__(self, data_dir: Path, process_fn: Callable[..., Result] = process) -> None:
        self.data_dir = data_dir
        self.process_fn = process_fn
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, source: str, stem: str, part: int | None = None) -> Job:
        label = source if is_url(source) else Path(source).name
        job = Job(id=uuid.uuid4().hex, source=label)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                self._jobs.popitem(last=False)
        self._pool.submit(self._run, job, source, stem, part)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job, source: str, stem: str, part: int | None) -> None:
        job.status = "running"
        try:
            try:
                profile = load_profile(self.data_dir / "device.json")
            except ValueError:
                profile = None  # sem calibragem: faixa padrão
            # process_fn de teste pode não ter o argumento `part`
            extra = {} if part is None else {"part": part}
            job.result = self.process_fn(
                source,
                self.data_dir,
                profile,
                stem,
                progress=lambda m: setattr(job, "message", m),
                **extra,
            )
        except (FetchError, FfmpegError, ValueError, RuntimeError) as e:
            job.error, job.status = str(e), "error"
            job.message = "Falhou"
            return
        except Exception as e:  # noqa: BLE001 - erro inesperado não pode deixar o job preso em "running"
            job.error, job.status = f"erro inesperado: {e!r}", "error"
            job.message = "Falhou"
            return
        job.status, job.message = "done", "Pronto"
