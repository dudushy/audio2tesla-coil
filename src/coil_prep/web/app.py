"""API web do coil-prep: recebe link ou arquivo, gera a melodia em onda quadrada e serve o áudio."""

import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import bitmidi, feedback, recording, scores
from .. import notes as notes_mod
from ..calibrate import write_mono_wav
from ..fetch import is_url
from ..ffmpeg import FfmpegError, wav_to_mp3
from ..midi import MIDI_EXTS, RATE, render, write_midi
from ..pipeline import FALLBACK_RANGE, PEAK_DB, STEM_CHOICES, STYLE_CHOICES, apply_edit, restyle
from ..profile import (
    COIL_HIGH,
    COIL_LOW,
    Profile,
    load_profile,
    profile_from_notes,
    save_profile,
)
from .jobs import JobManager

STATIC = Path(__file__).parent / "static"
UPLOAD_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a", ".webm", *MIDI_EXTS}
# formatos que gravadores de navegador/celular produzem (aceitos só como gravação)
RECORDING_EXTS = (UPLOAD_EXTS - MIDI_EXTS) | {".mp4", ".aac", ".3gp", ".amr"}
MAX_UPLOAD = 100 * 1024 * 1024  # 100 MB


class FeedbackIn(BaseModel):
    rating: int
    tag: str | None = None


class NoteIn(BaseModel):
    id: int | None = None
    start: float
    end: float
    pitch: float  # fracionária = afinação exata (gravação de voz)


class NotesIn(BaseModel):
    notes: list[NoteIn]


class PartIn(BaseModel):
    part: int | None = None  # None = automático; -1 = todas as trilhas


class StyleIn(BaseModel):
    style: str


class LiveIn(BaseModel):
    format: str  # wav | mid | mp3
    notes: list[NoteIn]
    fit: bool = True  # True = dobra por oitavas para a faixa calibrada (o que a bobina toca)


class ProfileIn(BaseModel):
    min_hz: float | None = None
    max_hz: float | None = None
    notes: list[int] | None = None  # notas ouvidas na calibragem (dispensa min/max)


class ScoreIn(BaseModel):
    file: str  # caminho no Mutopia: .mid ou -mids.zip
    title: str = ""
    member: str | None = None  # .mid escolhido dentro do .zip
    part: int | None = None  # trilha da melodia (peças em destaque)


def safe_name(filename: str) -> tuple[str, str]:
    """(nome limpo, extensão minúscula): remove diretórios e caracteres fora de [\\w.-]."""
    path = Path(filename.replace("\\", "/")).name
    stem = re.sub(r"[^\w.-]+", "_", Path(path).stem).strip("._")[:60] or "audio"
    return stem, Path(path).suffix.lower()


def profile_payload(p: Profile) -> dict:
    data = {"min_hz": p.min_hz, "max_hz": p.max_hz}
    return data | {"notes": list(p.notes)} if p.notes else data


def create_app(data_dir: Path, manager: JobManager | None = None) -> FastAPI:
    data_dir.mkdir(parents=True, exist_ok=True)
    jobs = manager or JobManager(data_dir)
    edit_lock = threading.Lock()
    app = FastAPI(title="coil-prep", docs_url=None, redoc_url=None)

    def page(name: str):
        return lambda: FileResponse(STATIC / name)

    # páginas: gerar (+ variações), editor da faixa, teclado ao vivo e calibragem
    app.get("/", include_in_schema=False)(page("index.html"))
    app.get("/track/{job_id}", include_in_schema=False)(page("faixa.html"))
    app.get("/soundpad", include_in_schema=False)(page("teclado.html"))
    app.get("/calibrate", include_in_schema=False)(page("calibrar.html"))
    app.get("/scores", include_in_schema=False)(page("partituras.html"))

    # endereços antigos (favoritos salvos) levam aos novos
    def moved(new: str):
        return lambda: RedirectResponse(new, status_code=308)

    for old, new in (("teclado", "soundpad"), ("calibrar", "calibrate"), ("partituras", "scores")):
        app.get(f"/{old}", include_in_schema=False)(moved(f"/{new}"))

    @app.get("/faixa/{job_id}", include_in_schema=False)
    def old_track(job_id: str):
        return RedirectResponse(f"/track/{quote(job_id)}", status_code=308)

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/bitmidi/search")
    def bitmidi_search(q: str, page: int = 0):
        try:
            return bitmidi.search(q, page)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except OSError:
            raise HTTPException(502, "não consegui consultar o BitMidi agora") from None

    async def save_source(
        url: str, file: UploadFile | None, is_recording: bool = False
    ) -> tuple[str, bool]:
        """Valida link OU arquivo e devolve (fonte, é .mid). O arquivo vai para data/input."""
        url = url.strip()
        if bool(url) == (file is not None and bool(file.filename)):
            raise HTTPException(422, "envie um link OU um arquivo")
        if url:
            if not is_url(url):
                raise HTTPException(422, "o link precisa começar com http:// ou https://")
            return url, False
        name, ext = safe_name(file.filename)
        if ext not in (RECORDING_EXTS if is_recording else UPLOAD_EXTS):
            raise HTTPException(422, f"formato não suportado: {ext or '(sem extensão)'}")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "arquivo maior que 100 MB")
        # hash no nome: o mesmo arquivo reaproveita o cache do Demucs ao testar outro stem
        dest = data_dir / "input" / f"{name}-{hashlib.sha1(data).hexdigest()[:8]}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if is_recording:  # vira WAV, como o áudio baixado de um link
            try:
                dest = recording.to_wav(dest, dest.with_name(f"{dest.stem}.rec.wav"))
            except FfmpegError:
                raise HTTPException(422, "não consegui ler o áudio gravado") from None
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
        return str(dest), ext in MIDI_EXTS

    def check_span(start: float, end: float | None) -> None:
        if start < 0 or (end is not None and end - start < 1):
            raise HTTPException(422, "trecho inválido: o fim precisa ser depois do início")

    @app.post("/api/jobs")
    async def create_job(
        stem: str = Form("other"),
        url: str = Form(""),
        part: int | None = Form(None),  # .mid: trilha escolhida (-1 = todas); vazio = automático
        start: float = Form(0.0),  # trecho da música, em segundos; sem `end` = até o fim
        end: float | None = Form(None),
        file: UploadFile | None = File(None),  # noqa: B008
    ):
        if stem not in STEM_CHOICES:
            raise HTTPException(422, f"stem inválido: {stem}")
        check_span(start, end)
        source, _ = await save_source(url, file)
        return jobs.submit(source, stem, part, start, end).to_dict()

    @app.post("/api/batches")
    async def create_batch(
        stems: str = Form(
            ",".join(STEM_CHOICES)
        ),  # áudio: quais stems gerar, separados por vírgula
        url: str = Form(""),
        start: float = Form(0.0),
        end: float | None = Form(None),
        is_recording: bool = Form(False, alias="recording"),  # o arquivo veio do microfone
        voice: bool = Form(False),  # uma voz só (cantarolar): frequência exata, sem variações
        file: UploadFile | None = File(None),  # noqa: B008
    ):
        """Gera todas as variações de uma vez: um job por stem (áudio) ou por modo de trilha (.mid)."""
        wanted = list(dict.fromkeys(s.strip() for s in stems.split(",") if s.strip()))
        if any(s not in STEM_CHOICES for s in wanted) or (not wanted):
            raise HTTPException(422, f"stems inválidos: {stems}")
        check_span(start, end)
        source, is_midi = await save_source(url, file, is_recording)
        batch_id = uuid.uuid4().hex
        # .mid não tem stem: as variações são o modo de trilha (automático x todas misturadas)
        plan = (
            [("mix", None, "trilhas automáticas"), ("mix", -1, "todas as trilhas")]
            if is_midi
            else [(s, None, s) for s in wanted]
        )
        if voice:
            if is_midi:
                raise HTTPException(422, "o modo voz é para áudio, não .mid")
            plan = [("mix", None, "voz (afinação exata)")]
        made = [
            jobs.submit(source, s, p, start, end, batch_id, label, voice=voice)
            for s, p, label in plan
        ]
        return {"id": batch_id, "jobs": [j.to_dict() for j in made]}

    @app.get("/api/batches/{batch_id}")
    def batch_status(batch_id: str):
        made = jobs.batch_jobs(batch_id)
        if not made:
            raise HTTPException(404, "lote não encontrado")
        return {"id": batch_id, "source": made[0].source, "jobs": [j.to_dict() for j in made]}

    @app.post("/api/jobs/{job_id}/part")
    def redo_with_part(job_id: str, body: PartIn):
        """Refaz um .mid com outra trilha (novo job no mesmo lote; o original fica para comparar)."""
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job não encontrado")
        label = "trilhas automáticas" if body.part is None else f"trilha {body.part}"
        if body.part == -1:
            label = "todas as trilhas"
        return jobs.submit(
            job.origin, job.stem, body.part, job.start, job.end, job.batch, label
        ).to_dict()

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
            write_mono_wav(out, render(segs, r.min_hz, r.max_hz, PEAK_DB, r.duty, r.gate), RATE)
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

    @app.get("/api/jobs/{job_id}/midi")
    def job_midi(job_id: str):
        """.mid das notas atuais (1 voz, já na faixa da bobina): regravado a cada edição."""
        job = jobs.get(job_id)
        if job is None or job.result is None or not job.result.midi.is_file():
            raise HTTPException(404, "MIDI indisponível")
        record_edit(job)
        midi = job.result.midi
        return FileResponse(midi, media_type="audio/midi", filename=midi.name)

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

    @app.put("/api/jobs/{job_id}/style")
    def put_style(job_id: str, body: StyleIn):
        job = jobs.get(job_id)
        if job is None or job.result is None:
            raise HTTPException(404, "job não encontrado ou ainda sem resultado")
        if body.style not in STYLE_CHOICES:
            raise HTTPException(422, f"estilo inválido: {body.style}")
        with edit_lock:
            job.result = restyle(job.result, body.style)
        r = job.result
        return {"style": r.style, "duty": r.duty, "gate": r.gate, "peak_db": round(r.peak_db, 1)}

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
                "style": r.style,
                "duty": r.duty,
                "gate": r.gate,
                "rating": body.rating,
                "tag": body.tag if body.rating == -1 else None,
            },
        )
        return {"ok": True, "edit_recorded": edited}

    @app.get("/api/state")
    def state():
        try:
            p = load_profile(data_dir / "device.json")
            profile = profile_payload(p)
        except ValueError:
            profile = None
        history = feedback.load(data_dir)
        return {
            "profile": profile,
            "coil_notes": [COIL_LOW, COIL_HIGH],  # notas do soundpad e da calibragem
            "suggestion": feedback.suggest_stem(history),
            "ratings": feedback.count_ratings(history),
            "peak_db": PEAK_DB,  # nível do teclado ao vivo = o da saída (referência Mario)
            "tags": list(feedback.TAGS),
        }

    @app.post("/api/live/export")
    def live_export(body: LiveIn):
        """Exporta a gravação do soundpad (com acordes) como WAV/MID/MP3 pela render do pipeline."""
        import tempfile

        if body.format not in ("wav", "mid", "mp3"):
            raise HTTPException(422, "formato deve ser wav, mid ou mp3")
        try:
            p = load_profile(data_dir / "device.json")
            lo_hz, hi_hz, heard = p.min_hz, p.max_hz, p.notes
        except ValueError:
            (lo_hz, hi_hz), heard = FALLBACK_RANGE, ()
        # fit=False: grava as notas como tocadas (faixa larga, sem dobrar)
        wide = (30.0, 8000.0)  # cobre C1..B8
        low, high = notes_mod.pitch_range(lo_hz, hi_hz)
        raw = [n.model_dump() for n in body.notes]
        if body.fit:
            for n in raw:
                n["pitch"] = notes_mod.fold_pitch(n["pitch"], low, high, heard)
        try:
            notes = notes_mod.validate_poly(raw, *(wide if not body.fit else (lo_hz, hi_hz)))
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        if not notes:
            raise HTTPException(422, "nenhuma nota gravada")
        t0 = notes[0].start  # corta o silêncio inicial
        notes = [replace(n, start=n.start - t0, end=n.end - t0) for n in notes]
        rng = (lo_hz, hi_hz) if body.fit else wide
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / f"soundpad.{body.format}"
            if body.format == "mid":
                write_midi(out, notes_mod.to_segments(notes))  # um evento por nota
            else:
                wav = out if body.format == "wav" else Path(tmp) / "soundpad.wav"
                write_mono_wav(wav, render(notes_mod.to_chords(notes), *rng, PEAK_DB), RATE)
                if body.format == "mp3":
                    try:
                        wav_to_mp3(wav, out)
                    except FfmpegError as e:
                        raise HTTPException(500, str(e)) from None
            data = out.read_bytes()
        mime = {"wav": "audio/wav", "mid": "audio/midi", "mp3": "audio/mpeg"}[body.format]
        return Response(
            data,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="soundpad.{body.format}"'},
        )

    @app.put("/api/profile")
    def save_profile_route(body: ProfileIn):
        try:
            if body.notes is not None:
                profile = profile_from_notes(body.notes)
            elif body.min_hz is not None and body.max_hz is not None:
                profile = Profile(body.min_hz, body.max_hz)
            else:
                raise ValueError("envie as notas ouvidas ou a faixa (menor e maior Hz)")
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        if profile.max_hz < 2 * profile.min_hz:
            raise HTTPException(422, "a faixa precisa ter pelo menos 1 oitava (máx >= 2x mín)")
        save_profile(profile, data_dir / "device.json")
        return profile_payload(profile)

    @app.get("/api/scores/featured")
    def scores_featured():
        return {"results": scores.FEATURED}

    @app.get("/api/scores/search")
    def scores_search(q: str, start: int = 0):
        try:
            return scores.search(q, start)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except OSError:
            raise HTTPException(502, "não consegui consultar o Mutopia agora") from None

    @app.get("/api/scores/movements")
    def scores_movements(file: str):
        try:
            return {"members": scores.movements(file)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except OSError:
            raise HTTPException(502, "não consegui baixar a partitura agora") from None

    @app.post("/api/scores/generate")
    def scores_generate(body: ScoreIn):
        """Baixa o .mid da partitura no Mutopia e gera a melodia (um lote, como no envio de .mid)."""
        try:
            data = scores.midi_bytes(body.file, body.member)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except OSError:
            raise HTTPException(502, "não consegui baixar a partitura agora") from None
        title = body.title or Path(body.member or body.file).stem
        name, _ = safe_name(re.sub(r"[/\\.]", " ", title) + ".mid")
        dest = data_dir / "input" / f"{name}-{hashlib.sha1(data).hexdigest()[:8]}.mid"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        plan = [(None, "trilhas automáticas"), (-1, "todas as trilhas")]
        if body.part is not None:  # peça em destaque: a trilha da melodia já é conhecida
            plan = [(body.part, "melodia principal"), plan[0]]
        batch_id = uuid.uuid4().hex
        made = [jobs.submit(str(dest), "mix", p, 0.0, None, batch_id, label) for p, label in plan]
        return {"id": batch_id, "jobs": [j.to_dict() for j in made]}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def create_default_app() -> FastAPI:
    """Fábrica para o uvicorn (`--factory`): COIL_DATA define a pasta de dados."""
    return create_app(Path(os.environ.get("COIL_DATA", "data")))
