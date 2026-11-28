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

MAX_JOBS = 200  # histórico em memória (um lote grande tem dezenas); os arquivos ficam no volume


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | done | error | canceled
    message: str = "Na fila..."
    error: str | None = None
    result: Result | None = None
    source: str = ""  # link ou nome do arquivo, para o histórico de avaliações
    rated: bool = False
    last_edit: str = ""  # hash das notas já registradas como edição no feedback
    batch: str = ""  # id do lote (várias variações da mesma fonte gerados de uma vez)
    variant: str = ""  # rótulo da variação dentro do lote (ex.: stem "other")
    stem: str = ""  # stem pedido (o do resultado só existe depois de pronto)
    origin: str = ""  # link ou caminho completo da fonte, para refazer com outra trilha
    start: float = 0.0
    end: float | None = None
    method: str = "melody"  # forma de conversão (ver pipeline.METHOD_CHOICES)
    separator: str = "demucs"  # separador das partes (ver separate.SEPARATOR_CHOICES)
    created: float = field(default_factory=lambda: __import__("time").time())

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "batch": self.batch or None,
            "variant": self.variant,
            "source": self.source,
            "stem": self.stem,
        }
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
                "style": r.style,
                "duty": r.duty,
                "gate": r.gate,
                "fidelity": r.fidelity,
                "bpm": round(r.bpm, 1) if r.bpm else None,
            }
        return data


class JobManager:
    def __init__(self, data_dir: Path, process_fn: Callable[..., Result] = process) -> None:
        self.data_dir = data_dir
        self.process_fn = process_fn
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def submit(
        self,
        source: str,
        stem: str,
        part: int | None = None,
        start: float = 0.0,
        end: float | None = None,
        batch: str = "",
        variant: str = "",
        method: str = "melody",
        separator: str = "demucs",
    ) -> Job:
        label = source if is_url(source) else Path(source).name
        job = Job(
            id=uuid.uuid4().hex,
            source=label,
            batch=batch,
            variant=variant,
            stem=stem,
            origin=source,
            start=start,
            end=end,
            method=method,
            separator=separator,
        )
        with self._lock:
            self._jobs[job.id] = job
            # esquece primeiro os mais antigos já terminados: um lote em andamento nunca some
            finished = [
                k for k, j in self._jobs.items() if j.status in ("done", "error", "canceled")
            ]
            for key in finished[: max(0, len(self._jobs) - MAX_JOBS)]:
                del self._jobs[key]
        self._pool.submit(self._run, job, source, stem, part, start, end)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent_batches(self, limit: int = 10) -> list[dict]:
        """Lotes mais recentes primeiro: id, fonte, quando e quantos jobs em cada estado."""
        with self._lock:
            groups: dict[str, list[Job]] = {}
            for j in self._jobs.values():
                if j.batch:
                    groups.setdefault(j.batch, []).append(j)
        out = []
        for bid, js in groups.items():
            count: dict[str, int] = {}
            for j in js:
                count[j.status] = count.get(j.status, 0) + 1
            out.append({"id": bid, "source": js[0].source, "created": min(j.created for j in js),
                        "jobs": len(js), "status": count})  # fmt: skip
        return sorted(out, key=lambda b: -b["created"])[:limit]

    def cancel_batch(self, batch_id: str) -> int:
        """Tira da fila as variações do lote que ainda não começaram (a que está rodando termina)."""
        n = 0
        with self._lock:
            for j in self._jobs.values():
                if j.batch == batch_id and j.status == "queued":
                    j.status, j.message = "canceled", "Cancelada"
                    n += 1
        return n

    def batch_jobs(self, batch_id: str) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.batch == batch_id]

    def _run(
        self,
        job: Job,
        source: str,
        stem: str,
        part: int | None,
        start: float = 0.0,
        end: float | None = None,
    ) -> None:
        with self._lock:
            if job.status == "canceled":
                return
            job.status = "running"
        try:
            try:
                profile = load_profile(self.data_dir / "device.json")
            except ValueError:
                profile = None  # sem calibragem: faixa padrão
            # process_fn de teste pode não ter os argumentos opcionais
            extra = {} if part is None else {"part": part}
            if start > 0 or end is not None:
                extra |= {"start": start, "end": end}
            if job.method != "melody":
                extra |= {"method": job.method}
            if job.separator != "demucs":
                extra |= {"separator": job.separator}
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
