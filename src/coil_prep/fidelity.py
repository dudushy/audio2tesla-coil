"""Fidelidade da onda quadrada ao original: métrica objetiva + busca simples de estilo.

Uma quadrada monofônica tem timbre fixo, então "chegar perto do timbre" só é possível por poucos
controles: ciclo de trabalho (`duty`, muda os harmônicos), articulação (`gate`, quanto de cada nota
soa: staccato x legato) e a oitava (já tratada em `contour_fold`). Este módulo mede o quanto o WAV
final se parece com a referência e escolhe a melhor combinação de `duty` x `gate`.

Referência: o áudio original (ou o stem isolado) ou, para .mid, as notas da voz mais aguda.
Métricas (0..1, só numpy):
  - nota:     cromagrama quadro a quadro (a nota certa, qualquer oitava);
  - onset:    F1 dos inícios de nota (tolerância de 50 ms);
  - envelope: distância (dB) entre os contornos de energia (a articulação: staccato x legato);
  - brilho:   centroide espectral (só quando a referência é áudio).
A nota e o onset não dependem de duty/gate; o que a busca otimiza é envelope + brilho. A métrica é
um proxy do que a bobina soa, NÃO substitui o ouvido: o preset padrão só cede se o ganho for claro.
"""

from dataclasses import dataclass

import numpy as np

from .midi import RATE, render

FRAME = 2048  # amostras por quadro da STFT (46 ms a 44,1 kHz)
HOP = 1024
MAX_SECONDS = 150.0  # só os primeiros segundos entram na métrica (custo da busca)
DB_RANGE = 45.0  # dinâmica do envelope: abaixo disso é silêncio
BRIGHT_MAX_HZ = 5000.0
ONSET_TOL = 0.05  # s

W_NOTE, W_ONSET, W_ENV, W_BRIGHT = 0.30, 0.15, 0.35, 0.20

DUTIES = (0.5, 0.33, 0.25, 0.125)
GATES = (1.0, 0.85, 0.7, 0.55)
CLASSIC = (0.5, 1.0)  # a onda que a bobina já tocou bem
MIN_GAIN = 0.02  # o automático só troca o clássico se o score subir pelo menos isso

# presets nomeados para a interface; "auto" é escolhido pela busca
PRESETS = {
    "classic": ("Clássico (50%, legato)", 0.5, 1.0),
    "staccato": ("Staccato (50%, notas 70%)", 0.5, 0.7),
    "bright": ("Brilhante (25%, legato)", 0.25, 1.0),
    "thin": ("Fino (12,5%, legato)", 0.125, 1.0),
}


@dataclass(frozen=True)
class Reference:
    env: np.ndarray  # dB por quadro (já limitado a DB_RANGE)
    chroma: np.ndarray  # (quadros, 12), cada linha normalizada
    onsets: np.ndarray  # s
    centroid: float | None  # Hz (mediana dos quadros ativos); None se a referência não é áudio


@dataclass(frozen=True)
class Scores:
    note: float
    onset: float
    envelope: float
    brightness: float | None  # None: referência sem áudio
    total: float

    def to_dict(self) -> dict:
        r = lambda v: None if v is None else round(v, 3)
        return {
            "note": r(self.note),
            "onset": r(self.onset),
            "envelope": r(self.envelope),
            "brightness": r(self.brightness),
            "total": r(self.total),
        }


@dataclass(frozen=True)
class Style:
    duty: float
    gate: float


def _frames(x: np.ndarray) -> np.ndarray:
    n = 1 + max(len(x) - FRAME, 0) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    x = np.pad(x, (0, max(0, (n - 1) * HOP + FRAME - len(x))))
    return np.abs(np.fft.rfft(x[idx] * np.hanning(FRAME), axis=1)) ** 2  # potência


def _band(rate: int, lo: float, hi: float) -> np.ndarray:
    f = np.fft.rfftfreq(FRAME, 1 / rate)
    return (f >= lo) & (f <= hi)


def _env_db(power: np.ndarray, rate: int, min_hz: float, max_hz: float) -> np.ndarray:
    """Energia por quadro na faixa da melodia (do piso da bobina até ~3x o topo, com harmônicos)."""
    e = power[:, _band(rate, min_hz * 0.8, max_hz * 3)].sum(axis=1)
    db = 10 * np.log10(e + 1e-12)
    return np.maximum(db, db.max() - DB_RANGE)


def _chroma_frames(power: np.ndarray, rate: int) -> np.ndarray:
    f = np.fft.rfftfreq(FRAME, 1 / rate)
    sel = (f >= 80) & (f <= 2000)
    cls = (np.round(12 * np.log2(f[sel] / 440)).astype(int) + 69) % 12  # 0 = dó, como a nota MIDI
    c = np.zeros((len(power), 12))
    for k in range(12):
        c[:, k] = power[:, sel][:, cls == k].sum(axis=1)
    return c / (c.sum(axis=1, keepdims=True) + 1e-12)


def _centroid(power: np.ndarray, rate: int, env: np.ndarray) -> float | None:
    f = np.fft.rfftfreq(FRAME, 1 / rate)
    band = f <= BRIGHT_MAX_HZ
    active = env > env.max() - 30
    if not active.any():
        return None
    p = power[active][:, band]
    c = (p * f[band]).sum(axis=1) / (p.sum(axis=1) + 1e-12)
    return float(np.median(c))


def _flux_onsets(power: np.ndarray, rate: int) -> np.ndarray:
    """Inícios de nota por fluxo espectral (aumento de energia por bin) com limiar adaptativo."""
    f = np.fft.rfftfreq(FRAME, 1 / rate)
    mag = 10 * np.log10(power[:, (f >= 80) & (f <= 2000)] + 1e-10)
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    if len(flux) < 3:
        return np.array([])
    thr = flux.mean() + flux.std()
    peaks = [
        i
        for i in range(1, len(flux) - 1)
        if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] > flux[i + 1]
    ]
    out: list[int] = []
    for i in peaks:
        if not out or (i - out[-1]) * HOP / rate >= 0.08:
            out.append(i)
    return (np.array(out) + 1) * HOP / rate


def reference_from_audio(samples: np.ndarray, min_hz: float, max_hz: float) -> Reference:
    """`samples`: áudio mono em RATE Hz (use `decode_mono(path, RATE)`)."""
    rate = RATE
    samples = np.asarray(samples, dtype=np.float64)[: int(MAX_SECONDS * rate)]
    power = _frames(samples)
    env = _env_db(power, rate, min_hz, max_hz)
    return Reference(
        env, _chroma_frames(power, rate), _flux_onsets(power, rate), _centroid(power, rate, env)
    )


def reference_from_notes(notes: list[tuple]) -> Reference:
    """Referência de um .mid: segmentos (início, fim, (altura,)) da voz mais aguda, sem transpor."""
    rate = RATE
    n_frames = int(min(max(b for _, b, _ in notes), MAX_SECONDS) * rate / HOP) + 1
    env = np.full(n_frames, -DB_RANGE)
    chroma = np.zeros((n_frames, 12))
    for a, b, p in notes:
        i, j = int(a * rate / HOP), min(int(b * rate / HOP) + 1, n_frames)
        if i >= n_frames:
            continue
        env[i:j] = 0.0
        chroma[i:j, p[0] % 12] = 1.0
    return Reference(env, chroma, np.array([a for a, _, _ in notes]), None)


def _render_features(segments, min_hz, max_hz, duty, gate, rate=RATE):
    wav = render(segments, min_hz, max_hz, -8.0, duty, gate, rate)[: int(MAX_SECONDS * rate)]
    power = _frames(wav)
    env = _env_db(power, rate, min_hz, max_hz)
    return env, _centroid(power, rate, env)


def _env_score(a: np.ndarray, b: np.ndarray) -> float:
    """1 - diferença média absoluta (dB) entre os contornos de energia, nivelados pelo percentil 95.

    Penaliza tanto o silêncio onde a referência soa (staccato demais) quanto o som contínuo onde a
    referência para (legato demais). Correlação não serve: com referência quase plana ela é instável.
    """
    n = min(len(a), len(b))
    a, b = a[:n] - np.percentile(a[:n], 95), b[:n] - np.percentile(b[:n], 95)
    return float(
        max(0.0, 1 - np.abs(np.maximum(a, -DB_RANGE) - np.maximum(b, -DB_RANGE)).mean() / DB_RANGE)
    )


def _note_score(ref: Reference, segments: list[tuple]) -> float:
    """Cosseno do cromagrama (nota tocada x referência), ponderado pela energia da referência."""
    n = len(ref.chroma)
    num = den = 0.0
    for a, b, p in segments:
        i, j = int(a * RATE / HOP), min(int(b * RATE / HOP) + 1, n)
        if i >= j:
            continue
        c = ref.chroma[i:j, p[0] % 12]  # cosseno com vetor unitário = componente da nota
        norm = np.linalg.norm(ref.chroma[i:j], axis=1) + 1e-12
        w = 10 ** ((ref.env[i:j] - ref.env.max()) / 10)
        num += float((w * c / norm).sum())
        den += float(w.sum())
    return num / den if den else 0.0


def _onset_f1(ref_on: np.ndarray, segments: list[tuple]) -> float:
    mine = np.array([a for a, _, _ in segments if a <= MAX_SECONDS])
    if len(ref_on) == 0 or len(mine) == 0:
        return 0.0
    hit = sum(np.min(np.abs(mine - t)) <= ONSET_TOL for t in ref_on)
    prec = sum(np.min(np.abs(ref_on - t)) <= ONSET_TOL for t in mine) / len(mine)
    rec = hit / len(ref_on)
    return 2 * prec * rec / (prec + rec) if prec + rec else 0.0


def _bright_score(ref_c: float | None, got: float | None) -> float | None:
    if ref_c is None or got is None:
        return None
    return float(max(0.0, 1 - abs(np.log2(got / ref_c))))  # 0 = >= 1 oitava de diferença


def score(ref: Reference, segments: list[tuple], min_hz: float, max_hz: float, style: Style):
    env, cent = _render_features(segments, min_hz, max_hz, style.duty, style.gate)
    note, onset = _note_score(ref, segments), _onset_f1(ref.onsets, segments)
    envelope = _env_score(ref.env, env)
    bright = _bright_score(ref.centroid, cent)
    parts = [(W_NOTE, note), (W_ONSET, onset), (W_ENV, envelope)]
    if bright is not None:
        parts.append((W_BRIGHT, bright))
    total = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    return Scores(note, onset, envelope, bright, total)


def search(
    ref: Reference, segments: list[tuple], min_hz: float, max_hz: float
) -> tuple[Style, list[tuple[Style, Scores]]]:
    """Avalia a grade duty x gate e devolve (melhor estilo, tabela ordenada por score).

    O clássico (50%, legato) é o padrão: outro estilo só ganha com `MIN_GAIN` a mais de score.
    Se a referência é só .mid (sem brilho), o duty não é buscado: não há o que comparar.
    """
    duties = DUTIES if ref.centroid is not None else (0.5,)
    table = [
        (Style(d, g), score(ref, segments, min_hz, max_hz, Style(d, g)))
        for d in duties
        for g in GATES
    ]
    table.sort(key=lambda row: -row[1].total)
    classic = next(r for r in table if (r[0].duty, r[0].gate) == CLASSIC)
    best = table[0]
    chosen = best[0] if best[1].total - classic[1].total >= MIN_GAIN else classic[0]
    return chosen, table
