"""Notas editáveis da melodia: validação, persistência em JSON e conversão para o render."""

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from .midi import midi_to_hz

MIN_DURATION = 0.02  # s; menor que isso vira estalo
MAX_NOTES = 5000


@dataclass(frozen=True)
class EditNote:
    id: int
    start: float  # s
    end: float  # s
    pitch: int  # número MIDI, já dentro da faixa da bobina


def pitch_range(min_hz: float, max_hz: float) -> tuple[int, int]:
    """Menor e maior nota MIDI cuja frequência cabe em [min_hz, max_hz]."""
    low = math.ceil(69 + 12 * math.log2(min_hz / 440))
    high = math.floor(69 + 12 * math.log2(max_hz / 440))
    # guarda contra erro de ponto flutuante nas bordas
    while midi_to_hz(low) < min_hz:
        low += 1
    while midi_to_hz(high) > max_hz:
        high -= 1
    return low, high


def from_segments(segments: list[tuple]) -> list[EditNote]:
    """Segmentos (início, fim, (nota,)) do pipeline -> notas com id estável."""
    return [EditNote(i, float(a), float(b), int(p[0])) for i, (a, b, p) in enumerate(segments)]


def to_segments(notes: list[EditNote]) -> list[tuple]:
    return [(n.start, n.end, (n.pitch,)) for n in notes]


def validate(raw: list[dict], min_hz: float, max_hz: float) -> list[EditNote]:
    """Valida a lista vinda do editor e devolve notas ordenadas, monofônicas e com ids únicos.

    Notas sobrepostas: a anterior é cortada no início da seguinte (uma voz só); se sobrar menos
    que MIN_DURATION, é descartada. Pitch fora da faixa da bobina é erro (sem dobrar oitava).
    """
    if len(raw) > MAX_NOTES:
        raise ValueError(f"notas demais ({len(raw)}); o máximo é {MAX_NOTES}")
    low, high = pitch_range(min_hz, max_hz)
    parsed = []
    used = {n["id"] for n in raw if isinstance(n.get("id"), int)}
    next_id = max(used, default=-1) + 1
    seen = set()
    for n in raw:
        try:
            start, end, pitch = float(n["start"]), float(n["end"]), int(n["pitch"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("nota inválida: precisa de start, end e pitch numéricos") from None
        if not (math.isfinite(start) and math.isfinite(end)) or start < 0:
            raise ValueError(f"nota com tempo inválido: {start}-{end}")
        if end - start < MIN_DURATION:
            raise ValueError(f"nota com menos de {MIN_DURATION * 1000:g} ms em {start:.2f}s")
        if not low <= pitch <= high:
            raise ValueError(f"nota {pitch} fora da faixa da bobina ({low}-{high})")
        nid = n.get("id")
        if not isinstance(nid, int) or nid in seen:
            nid, next_id = next_id, next_id + 1
        seen.add(nid)
        parsed.append(EditNote(nid, start, end, pitch))
    parsed.sort(key=lambda n: (n.start, n.end))
    out: list[EditNote] = []
    for n in parsed:
        if out and out[-1].end > n.start:
            prev = out[-1]
            if n.start - prev.start < MIN_DURATION:
                out.pop()  # a anterior ficaria curta demais
            else:
                out[-1] = EditNote(prev.id, prev.start, n.start, prev.pitch)
        out.append(n)
    return out


def save(
    path: Path, min_hz: float, max_hz: float, generated: list[EditNote], current: list[EditNote]
):
    data = {
        "min_hz": min_hz,
        "max_hz": max_hz,
        "generated": [asdict(n) for n in generated],
        "current": [asdict(n) for n in current],
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load(path: Path) -> dict:
    """{'min_hz', 'max_hz', 'generated': [EditNote], 'current': [EditNote]}"""
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("generated", "current"):
        data[key] = [EditNote(**n) for n in data[key]]
    return data
