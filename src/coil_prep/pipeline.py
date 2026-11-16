"""Fluxo único: URL ou arquivo -> (stem) -> melodia -> onda quadrada -> WAV para a bobina."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .analysis import analyze
from .calibrate import write_mono_wav
from .fetch import fetch, is_url
from .ffmpeg import decode_mono
from .midi import (
    MIDI_EXTS,
    RATE,
    contour_fold,
    melody_line,
    notes_from,
    overlap_fraction,
    render,
)
from .profile import Profile
from .separate import separate

STEM_CHOICES = ("other", "vocals", "mix")  # "mix" = sem separação
PEAK_DB = -8.0  # nível que a bobina tocou bem (referência Mario)
FALLBACK_RANGE = (100.0, 800.0)
OVERLAP_WARN = 0.3  # acima disso a transcrição é muito polifônica para uma bobina
MAX_PEAK = -3.0

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Result:
    wav: Path
    original: Path | None  # áudio de origem (None se a entrada foi .mid)
    stem: str
    min_hz: float
    max_hz: float
    notes: int
    segments: int
    overlap: float
    peak_db: float
    clipped: bool

    @property
    def ok(self) -> bool:
        return self.peak_db <= MAX_PEAK and not self.clipped


def process(
    source: str,
    workdir: Path,
    profile: Profile | None,
    stem: str = "other",
    progress: Progress = lambda _msg: None,
) -> Result:
    """`source` é URL http(s) ou caminho de áudio/.mid. Saída em workdir/output/<nome>.wav."""
    if stem not in STEM_CHOICES:
        raise ValueError(f"stem inválido: {stem}")
    min_hz, max_hz = (profile.min_hz, profile.max_hz) if profile else FALLBACK_RANGE

    if is_url(source):
        progress("Baixando áudio...")
        original = fetch(source, workdir / "input")
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
    notes = notes_from(origin)
    segments = melody_line(notes, 0.06, 0.02, 0.02)
    if segments:
        segments = contour_fold(segments, min_hz, max_hz)
    if not segments:
        raise ValueError("nenhuma nota detectada; tente outro som (stem) ou outro arquivo")

    progress("Gerando a onda quadrada...")
    out = workdir / "output" / f"{original.stem}.{stem}.wav"  # um arquivo por stem
    out.parent.mkdir(parents=True, exist_ok=True)
    write_mono_wav(out, render(segments, min_hz, max_hz, PEAK_DB), RATE)
    report = analyze(decode_mono(out))
    return Result(
        wav=out,
        original=None if is_midi else original,
        stem="mix" if (stem == "mix" or is_midi) else stem,
        min_hz=min_hz,
        max_hz=max_hz,
        notes=len(notes),
        segments=len(segments),
        overlap=0.0 if is_midi else overlap_fraction(notes),
        peak_db=report.peak_db,
        clipped=report.clipped,
    )
