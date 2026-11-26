"""Voz cantada/cantarolada/assobiada (uma voz só) -> notas com a frequência exata que foi cantada.

O Basic Pitch é feito para música com vários instrumentos: arredonda cada nota para o semitom e
confunde harmônicos da voz com notas. Aqui a frequência fundamental é medida quadro a quadro
(pYIN do librosa); o que é ruído, respiração ou silêncio fica de fora, a oscilação natural da voz
(vibrato, deslizes curtos) é alisada e cada trecho estável vira uma nota na altura mediana em que
foi cantado, com os centésimos de semitom preservados: nada é transposto nem "afinado".
"""

from pathlib import Path

import numpy as np

from .ffmpeg import decode_mono

SR = 22050
HOP = 256  # ~11,6 ms por quadro
FMIN, FMAX = 50.0, 2000.0  # G1 a B6: voz grave até assobio
SMOOTH = 7  # quadros da mediana móvel (~80 ms): tira o tremido sem atrasar troca de nota
SPLIT = 0.75  # semitons longe da nota atual para começar outra
SPLIT_FRAMES = 4  # ...por pelo menos este nº de quadros (~46 ms): vibrato rápido não divide
MIN_NOTE = 0.08  # s; mais curto que isso é deslize ou ruído
MAX_GAP = 0.06  # s; falha curta de detecção dentro de uma nota não a interrompe
SILENCE_DB = 35.0  # quadros mais de 35 dB abaixo do trecho mais alto contam como silêncio
DIP = 0.7  # vale de volume abaixo de 70% dos picos vizinhos = nova sílaba (rearticula a nota)
DIP_WINDOW = 0.15  # s; distância máxima até os picos que definem o vale
RELEASE = 0.25  # a nota acaba quando o volume cai abaixo de 25% do pico dela (sílaba sumindo)
ARTICULATION = 0.025  # s de silêncio entre sílabas: sem isso a bobina emenda as notas iguais
MIN_VOICING = 0.04  # probabilidade de voz (pYIN) mediana mínima da nota; ruído fica em ~0,01


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[início, fim) de cada sequência de True."""
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(np.int8), 0]))
    return list(zip(edges[::2], edges[1::2], strict=True))


def pitch_track(y: np.ndarray, sr: int = SR) -> tuple[np.ndarray, ...]:
    """(tempos em s, altura MIDI por quadro (NaN sem voz), probabilidade de voz, volume).

    O volume usa janela curta (46 ms), para enxergar o vale entre duas sílabas.
    """
    import librosa

    f0, voiced, prob = librosa.pyin(
        y, fmin=FMIN, fmax=FMAX, sr=sr, frame_length=2048, hop_length=HOP, center=True
    )
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP, center=True)[0]
    n = min(len(f0), len(rms))
    f0, voiced, prob, rms = f0[:n], voiced[:n], prob[:n], rms[:n]
    loud = rms > rms.max() * 10 ** (-SILENCE_DB / 20) if n else rms > 0
    midi = np.where(voiced & loud & np.isfinite(f0), 69 + 12 * np.log2(f0 / 440.0), np.nan)
    level = librosa.feature.rms(y=y, frame_length=1024, hop_length=HOP, center=True)[0][:n]
    return np.arange(n) * HOP / sr, midi, prob, level


def _smooth(midi: np.ndarray) -> np.ndarray:
    """Mediana móvel só dentro de cada trecho com voz (não mistura com o silêncio)."""
    out = midi.copy()
    half = SMOOTH // 2
    for a, b in _runs(np.isfinite(midi)):
        seg = midi[a:b]
        for i in range(b - a):
            out[a + i] = np.median(seg[max(0, i - half) : i + half + 1])
    return out


def _dips(level: np.ndarray | None, frame: float) -> np.ndarray:
    """Quadros que são vale de volume entre duas sílabas (True) ou não."""
    if level is None or not len(level):
        return np.zeros(0 if level is None else len(level), dtype=bool)
    w = max(1, round(DIP_WINDOW / frame))
    n = len(level)
    out = np.zeros(n, dtype=bool)
    for i in range(1, n - 1):
        if level[i] > level[i - 1] or level[i] > level[i + 1]:
            continue  # só mínimos locais
        left, right = level[max(0, i - w) : i].max(), level[i + 1 : i + 1 + w].max()
        out[i] = level[i] < DIP * min(left, right)
    return out


def _close_gaps(midi: np.ndarray, max_frames: int, dips: np.ndarray) -> np.ndarray:
    """Preenche falhas curtas de detecção dentro de uma nota (não um intervalo entre sílabas)."""
    out = midi.copy()
    gaps = _runs(~np.isfinite(midi))
    for a, b in gaps:
        if a == 0 or b == len(midi) or b - a > max_frames:
            continue
        if len(dips) and dips[max(0, a - 2) : b + 2].any():
            continue  # o volume caiu: é a pausa entre duas sílabas
        if abs(midi[a - 1] - midi[b]) < SPLIT:
            out[a:b] = np.linspace(midi[a - 1], midi[b], b - a + 2)[1:-1]
    return out


def notes_from_track(
    times: np.ndarray,
    midi: np.ndarray,
    prob: np.ndarray | None = None,
    level: np.ndarray | None = None,
) -> list[tuple]:
    """Quadros -> segmentos (início, fim, (altura,)) com altura fracionária (0,01 semitom).

    Com `level` (volume), cada sílaba vira uma nota: um vale de volume corta a nota (mesmo que
    a altura não mude, como em "lá-lá-lá") e a nota termina quando a sílaba vai sumindo.
    """
    if not len(midi):
        return []
    frame = float(times[1] - times[0]) if len(times) > 1 else HOP / SR
    dips = _dips(level, frame)
    midi = _smooth(_close_gaps(midi, round(MAX_GAP / frame), dips))
    if len(dips):
        midi[dips] = np.nan  # o vale separa as sílabas
    notes: list[list[int]] = []  # [início, fim) em quadros
    for a, b in _runs(np.isfinite(midi)):
        start, ref, away = a, midi[a], 0
        for i in range(a, b):
            away = away + 1 if abs(midi[i] - ref) > SPLIT else 0
            if away >= SPLIT_FRAMES:  # mudou de nota: o corte é onde começou a mudança
                cut = i - SPLIT_FRAMES + 1
                notes.append([start, cut])
                start, away = cut, 0
            # referência = mediana do começo estável da nota atual
            ref = float(np.median(midi[start : max(i + 1 - away, start + 1)]))
        notes.append([start, b])
    out = []
    for a, b in notes:
        if level is not None and b > a:  # tira o começo e o fim fracos da sílaba
            peak = level[a:b].max()
            while b - a > 1 and level[b - 1] < RELEASE * peak:
                b -= 1
            while b - a > 1 and level[a] < RELEASE * peak:
                a += 1
        if (b - a) * frame < MIN_NOTE:
            continue
        if prob is not None and float(np.median(prob[a:b])) < MIN_VOICING:
            continue  # ruído com "altura" por acaso
        pitch = round(float(np.median(midi[a:b])), 2)
        out.append((float(times[a]), float(times[a] + (b - a) * frame), (pitch,)))
    return _articulate(_merge_same(out, keep_apart=level is not None))


def _merge_same(segments: list[tuple], keep_apart: bool = False) -> list[tuple]:
    """Junta notas vizinhas quase iguais que a divisão separou (vibrato largo, falha de detecção).

    Com `keep_apart`, notas separadas por qualquer pausa ficam separadas (são sílabas).
    """
    out: list[tuple] = []
    for a, b, (p,) in segments:
        if out and not (keep_apart and a > out[-1][1] + 1e-6):
            pa, pb, (q,) = out[-1]
            if a - pb <= MAX_GAP and abs(p - q) < SPLIT / 2:
                w1, w2 = pb - pa, b - a
                out[-1] = (pa, b, (round((q * w1 + p * w2) / (w1 + w2), 2),))
                continue
        out.append((a, b, (p,)))
    return out


def _articulate(segments: list[tuple]) -> list[tuple]:
    """Garante um silêncio curto entre notas coladas: sem ele, duas sílabas iguais soam como uma."""
    out = []
    for i, (a, b, p) in enumerate(segments):
        nxt = segments[i + 1][0] if i + 1 < len(segments) else None
        if nxt is not None and nxt - b < ARTICULATION and b - a > 2 * ARTICULATION:
            b = nxt - ARTICULATION
        out.append((a, b, p))
    return out


def voice_notes(path: Path) -> list[tuple]:
    """Segmentos (início, fim, (altura MIDI fracionária,)) da voz gravada em `path`."""
    try:
        import librosa  # noqa: F401
    except ImportError as e:
        raise RuntimeError(f"librosa não instalado ({e}); instale o extra [midi-onnx]") from None
    return notes_from_track(*pitch_track(decode_mono(path, SR)))
