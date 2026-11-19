"""Fluxo único: URL ou arquivo -> (stem) -> melodia -> onda quadrada -> WAV para a bobina."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from . import notes as notes_mod
from .analysis import analyze
from .calibrate import write_mono_wav
from .fetch import fetch, is_url
from .ffmpeg import decode_mono
from .midi import (
    ALL_PARTS,
    MIDI_EXTS,
    RATE,
    best_shift,
    combine_parts,
    contour_fold,
    coverage,
    median_note_ms,
    melody_line,
    notes_from,
    out_of_range_fraction,
    overlap_fraction,
    read_midi_parts,
    render,
    select_part,
    transpose,
)
from .profile import Profile
from .separate import separate

STEM_CHOICES = ("other", "vocals", "mix")  # "mix" = sem separação
PEAK_DB = -8.0  # nível que a bobina tocou bem (referência Mario)
FALLBACK_RANGE = (100.0, 800.0)
OVERLAP_WARN = 0.3  # acima disso a transcrição é muito polifônica para uma bobina
MAX_PEAK = -3.0
OUT_OF_RANGE_WARN = 0.2  # mesmo após transpor, muita nota fora da faixa vira salto de oitava
FAST_NOTE_MS = 100  # mediana abaixo disso: o arco não tem tempo de formar (Mario ~140 ms)

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Result:
    wav: Path
    notes_file: Path  # JSON com as notas geradas e as editadas
    original: Path | None  # áudio de origem (None se a entrada foi .mid)
    stem: str
    min_hz: float
    max_hz: float
    notes: int
    segments: int
    overlap: float
    shift: int  # semitons aplicados à música inteira para caber na faixa
    out_of_range: float  # fração do tempo fora da faixa depois do shift (antes de dobrar)
    median_note_ms: float
    peak_db: float
    clipped: bool
    parts: tuple = ()  # .mid: trilhas disponíveis (dicts id/name/notes/lo/hi/chosen) para o usuário trocar
    part: int | None = None  # .mid: id da trilha usada

    @property
    def ok(self) -> bool:
        return self.peak_db <= MAX_PEAK and not self.clipped


def process(
    source: str,
    workdir: Path,
    profile: Profile | None,
    stem: str = "other",
    progress: Progress = lambda _msg: None,
    part: int | None = None,
) -> Result:
    """`source` é URL http(s) ou caminho de áudio/.mid. Saída em workdir/output/<nome>.wav.

    `part` (só .mid): id da trilha (trilha*16+canal); -1 = todas; None = combina automaticamente
    as trilhas que parecem melodia (a melhor manda, as outras preenchem as pausas dela).
    """
    if stem not in STEM_CHOICES:
        raise ValueError(f"stem inválido: {stem}")
    min_hz, max_hz = (profile.min_hz, profile.max_hz) if profile else FALLBACK_RANGE

    if is_url(source):
        progress("Baixando áudio...")
        original = fetch(source, workdir / "input", cookies=workdir / "cookies.txt")
    else:
        original = Path(source).expanduser()
        if not original.is_file():
            raise ValueError(f"arquivo não encontrado: {original}")
    is_midi = original.suffix.lower() in MIDI_EXTS

    origin = original
    if stem != "mix" and not is_midi:
        progress(f"Isolando '{stem}' com Demucs (CPU, pode levar minutos na 1ª vez)...")
        origin = separate(original, stem, workdir / "separated")

    progress("Extraindo a melodia...")
    parts_info, chosen_part = (), None
    if is_midi:
        parts = read_midi_parts(origin)
        if part is None:  # automático: melodia combinada de várias trilhas
            notes, used = combine_parts(parts)
        else:
            chosen = select_part(parts, part)
            chosen_part, notes = chosen.id, chosen.notes
            used = {p.id: coverage(p.notes)[0] for p in parts} if part == ALL_PARTS else {}
            used = used or {chosen.id: coverage(notes)[0]}
        parts_info = tuple(
            {
                "id": p.id,
                "name": p.name,
                "notes": len(p.notes),
                "lo": min((n.pitch for n in p.notes), default=0),
                "hi": max((n.pitch for n in p.notes), default=0),
                "used": round(used.get(p.id, 0.0), 1),  # segundos desta trilha na melodia final
            }
            for p in parts
        )
    else:
        notes = notes_from(origin)
    segments = melody_line(notes, 0.06, 0.02, 0.02)
    if not segments:
        raise ValueError("nenhuma nota detectada; tente outro som (stem) ou outro arquivo")
    shift = best_shift(segments, min_hz, max_hz)
    segments = transpose(segments, shift)
    out_of_range = out_of_range_fraction(segments, min_hz, max_hz)
    segments = contour_fold(segments, min_hz, max_hz)

    progress("Gerando a onda quadrada...")
    suffix = f".t{chosen_part}" if chosen_part is not None and part is not None else ""
    out = workdir / "output" / f"{original.stem}.{stem}{suffix}.wav"  # um arquivo por stem/trilha
    out.parent.mkdir(parents=True, exist_ok=True)
    write_mono_wav(out, render(segments, min_hz, max_hz, PEAK_DB), RATE)
    editable = notes_mod.from_segments(segments)
    notes_file = out.with_suffix(".notes.json")
    notes_mod.save(notes_file, min_hz, max_hz, editable, editable)
    report = analyze(decode_mono(out))
    return Result(
        wav=out,
        notes_file=notes_file,
        original=None if is_midi else original,
        stem="mix" if (stem == "mix" or is_midi) else stem,
        min_hz=min_hz,
        max_hz=max_hz,
        notes=len(notes),
        segments=len(segments),
        overlap=0.0 if is_midi else overlap_fraction(notes),
        shift=shift,
        out_of_range=out_of_range,
        median_note_ms=median_note_ms(segments),
        peak_db=report.peak_db,
        clipped=report.clipped,
        parts=parts_info,
        part=chosen_part,
    )


def apply_edit(result: Result, raw_notes: list[dict]) -> tuple[Result, list[notes_mod.EditNote]]:
    """Valida as notas editadas, re-renderiza o WAV (mesmo `render` do pipeline) e salva o JSON."""
    saved = notes_mod.load(result.notes_file)
    cleaned = notes_mod.validate(raw_notes, result.min_hz, result.max_hz)
    if not cleaned:
        raise ValueError("nenhuma nota: o resultado ficaria em silêncio")
    samples = render(notes_mod.to_segments(cleaned), result.min_hz, result.max_hz, PEAK_DB)
    write_mono_wav(result.wav, samples, RATE)
    notes_mod.save(result.notes_file, result.min_hz, result.max_hz, saved["generated"], cleaned)
    report = analyze(decode_mono(result.wav))
    updated = replace(result, segments=len(cleaned), peak_db=report.peak_db, clipped=report.clipped)
    return updated, cleaned
