"""Modo melodia: notas (de .mid ou de áudio) -> 1 voz -> faixa da bobina -> onda quadrada."""

import tempfile
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np

from .ffmpeg import run_ffmpeg

RATE = 44100
MIDI_EXTS = {".mid", ".midi"}
DRUM_CHANNEL = 9  # canal 10 do MIDI (índice 9) é percussão


@dataclass(frozen=True)
class Note:
    start: float  # s
    end: float  # s
    pitch: int  # número MIDI


def midi_to_hz(pitch: float) -> float:
    return 440.0 * 2 ** ((pitch - 69) / 12)


def read_midi(path: Path) -> list[Note]:
    """Lê um .mid (ignora percussão). mido já resolve tempo -> segundos."""
    import mido

    try:
        mid = mido.MidiFile(str(path))
    except (OSError, ValueError, EOFError) as e:
        raise ValueError(f"MIDI inválido: {path} ({e})") from None
    notes, active, now = [], {}, 0.0
    for msg in mid:  # iterar já une as trilhas; msg.time é delta em segundos
        now += msg.time
        if msg.type not in ("note_on", "note_off") or msg.channel == DRUM_CHANNEL:
            continue
        key = (msg.channel, msg.note)
        if msg.type == "note_on" and msg.velocity > 0:
            active.setdefault(key, now)
        elif key in active:
            notes.append(Note(active.pop(key), now, msg.note))
    return notes


def transcribe_audio(path: Path) -> list[Note]:
    """Áudio -> notas via Basic Pitch (extra [midi]). Decodifica antes com o ffmpeg."""
    try:
        from basic_pitch.inference import predict
    except ImportError:
        raise RuntimeError(
            "Basic Pitch não instalado. Instale o extra: pip install -e '.[midi]'\n"
            "(precisa de Python 3.10 ou 3.11 no Linux; veja o README)"
        ) from None
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "in.wav"
        run_ffmpeg(["-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "22050", str(wav)])
        _, _, events = predict(str(wav))
    return [Note(float(s), float(e), int(p)) for s, e, p, *_ in events]


def skyline(notes: list[Note], voices: int = 1, min_note: float = 0.06) -> list[tuple]:
    """Em cada instante fica com as `voices` notas mais agudas ativas.

    Retorna segmentos (início, fim, (alturas...)) sem sobreposição; trechos mais curtos
    que `min_note` s são descartados (ruído de transcrição).
    """
    if not notes:
        return []
    cuts = sorted({t for n in notes for t in (n.start, n.end)})
    segments = []
    for a, b in pairwise(cuts):
        mid = (a + b) / 2
        active = sorted({n.pitch for n in notes if n.start <= mid < n.end}, reverse=True)[:voices]
        if not active:
            continue
        pitches = tuple(active)
        if segments and segments[-1][1] == a and segments[-1][2] == pitches:
            segments[-1] = (segments[-1][0], b, pitches)  # une trechos iguais e contíguos
        else:
            segments.append((a, b, pitches))
    return [s for s in segments if s[1] - s[0] >= min_note]


def fold_to_range(pitch: int, min_hz: float, max_hz: float) -> float:
    """Move a nota de oitava até caber em [min_hz, max_hz]. Exige faixa >= 1 oitava."""
    if max_hz < 2 * min_hz:
        raise ValueError(f"faixa {min_hz:g}-{max_hz:g} Hz tem menos de 1 oitava")
    f = midi_to_hz(pitch)
    while f < min_hz:
        f *= 2
    while f > max_hz:
        f /= 2
    return f


def render(
    segments: list[tuple], min_hz: float, max_hz: float, peak_db: float = -4.0
) -> np.ndarray:
    """Onda quadrada, uma frequência (ou soma de vozes) por segmento; silêncio fora das notas."""
    if not segments:
        raise ValueError("nenhuma nota encontrada")
    total = int(np.ceil(segments[-1][1] * RATE)) + 1
    out = np.zeros(total)
    ramp = int(0.002 * RATE)  # 2 ms evita estalo na troca de nota
    for start, end, pitches in segments:
        i, j = int(start * RATE), int(end * RATE)
        t = np.arange(i, j) / RATE  # tempo absoluto: fase contínua entre segmentos iguais
        wave = sum(
            np.where(np.sin(2 * np.pi * fold_to_range(p, min_hz, max_hz) * t) >= 0, 1.0, -1.0)
            for p in pitches
        )
        wave = wave / len(pitches)
        r = min(ramp, len(wave) // 2)
        if r:
            wave[:r] *= np.linspace(0, 1, r)
            wave[-r:] *= np.linspace(1, 0, r)
        out[i:j] = wave
    return out * 10 ** (peak_db / 20)


def notes_from(path: Path) -> list[Note]:
    return read_midi(path) if path.suffix.lower() in MIDI_EXTS else transcribe_audio(path)
