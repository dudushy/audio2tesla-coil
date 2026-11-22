"""Avaliação objetiva: a saída é tocável na bobina e parece com um resultado esperado?

Duas medidas, as duas feitas no ÁUDIO (não nas notas intermediárias do pipeline):

- `playability`: o WAV é uma quadrada monofônica, dentro da faixa, duty 50% e no pico certo.
- `compare`: notas do WAV gerado x notas de um áudio de referência que já é a melodia em onda
  quadrada (o "resultado esperado"). F1 por nota (início ±50 ms, altura ±50 cents) via mir_eval,
  erro de oitava e % do tempo com a nota certa.

Uso: `python -m coil_prep.evaluate esperado.mp3 gerado.wav [--min-hz 100 --max-hz 1000]`.
"""

import math
from dataclasses import asdict, dataclass
from itertools import pairwise

import numpy as np

from .midi import RATE

MIN_NOTE = 0.04  # s; trecho menor que isso não conta como nota
ONSET_TOL = 0.05  # s
GRID = 0.01  # s; passo da medida "% do tempo"


def square_notes(samples: np.ndarray, rate: int = RATE, floor_db: float = -30.0) -> list[tuple]:
    """Áudio de onda quadrada monofônica -> [(início, fim, nota MIDI inteira)].

    Mede o período entre cruzamentos de zero ascendentes (interpolados): numa quadrada isso dá a
    frequência exata de cada ciclo, sem modelo e com precisão de um ciclo no tempo. Um ciclo só
    conta se a amplitude dele passa de `floor_db` (re. pico); ciclos vizinhos com a mesma nota
    viram uma nota. Serve para o WAV gerado e para um MP3/gravação limpa da mesma onda.
    """
    x = np.asarray(samples, dtype=np.float64)
    if x.size < 4 or np.max(np.abs(x)) <= 0:
        return []
    floor = np.max(np.abs(x)) * 10 ** (floor_db / 20)
    up = np.where((x[:-1] < 0) & (x[1:] >= 0))[0]
    if len(up) < 2:
        return []
    t = (up - x[up] / (x[up + 1] - x[up])) / rate  # instante de cada cruzamento
    peaks = np.maximum.reduceat(np.abs(x), up)[:-1]  # pico de cada ciclo
    period = np.diff(t)
    ok = (peaks >= floor) & (period > 1 / 5000) & (period < 1 / 40)
    pitch = np.full(len(period), -1)
    pitch[ok] = np.round(69 + 12 * np.log2(1 / period[ok] / 440)).astype(int)
    notes, i = [], 0
    while i < len(pitch):
        j = i
        while j < len(pitch) and pitch[j] == pitch[i]:
            j += 1
        if pitch[i] >= 0 and t[j] - t[i] >= MIN_NOTE:
            notes.append((float(t[i]), float(t[j]), int(pitch[i])))
        i = j
    return notes


@dataclass(frozen=True)
class Playability:
    peak_db: float
    min_hz: float  # menor e maior frequência que soa
    max_hz: float
    duty: float  # fração média do ciclo em nível alto
    two_level: float  # fração das amostras com som que estão em ±pico (1 voz = só dois níveis)
    ok: bool
    problems: tuple[str, ...]


def playability(
    samples: np.ndarray,
    min_hz: float,
    max_hz: float,
    peak_db: float = -8.0,
    rate: int = RATE,
) -> Playability:
    """Confere no WAV final: 1 voz, frequências em [min_hz, max_hz], duty 50%, pico `peak_db`."""
    x = np.asarray(samples, dtype=np.float64)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0:
        return Playability(-math.inf, 0, 0, 0, 0, False, ("áudio em silêncio",))
    got_db = 20 * math.log10(peak)
    up = np.where((x[:-1] <= 0) & (x[1:] > 0))[0]
    down = np.where((x[:-1] > 0) & (x[1:] <= 0))[0]
    # ciclo = de uma subida até a próxima; se a maior parte é silêncio, é pausa entre notas
    cycles = [
        (a, b)
        for a, b in pairwise(up)
        # o 1º ciclo de cada nota sai do silêncio no meio do período (a fase segue o tempo
        # absoluto) e mediria uma frequência falsa, mais aguda: fica de fora
        if b - a <= rate / 40 and x[a] != 0 and np.count_nonzero(x[a + 1 : b + 1]) > (b - a) / 2
    ]
    if not cycles:
        return Playability(got_db, 0, 0, 0, 0, False, ("nenhum ciclo de onda encontrado",))
    freqs = np.array([rate / (b - a) for a, b in cycles])
    k = np.searchsorted(down, [a for a, _ in cycles], side="right")
    highs = np.array(
        [(down[i] - a) / (b - a) for (a, b), i in zip(cycles, k, strict=True) if i < len(down)]
    )
    inside = np.concatenate([x[a + 1 : b + 1] for a, b in cycles])
    two_level = float(np.mean(np.abs(np.abs(inside) - peak) < 0.02 * peak))
    # todas as notas contam (uma nota rara fora da faixa é defeito); a frequência de um ciclo só
    # é medida com resolução de 1 amostra, daí a folga de 3% no teste abaixo
    lo, hi = float(freqs.min()), float(freqs.max())
    duty = float(np.mean(highs))
    problems = []
    if abs(got_db - peak_db) > 0.2:
        problems.append(f"pico {got_db:.1f} dBFS (esperado {peak_db:g})")
    if lo < min_hz * 0.97 or hi > max_hz * 1.03:
        problems.append(f"frequências {lo:.0f}-{hi:.0f} Hz fora de {min_hz:g}-{max_hz:g}")
    if abs(duty - 0.5) > 0.02:
        problems.append(f"duty {100 * duty:.0f}% (esperado 50%)")
    if two_level < 0.9:
        problems.append(f"só {100 * two_level:.0f}% das amostras em ±pico: mais de 1 voz?")
    return Playability(got_db, lo, hi, duty, two_level, not problems, tuple(problems))


@dataclass(frozen=True)
class Comparison:
    ref_notes: int
    est_notes: int
    f1: float  # nota certa (início ±50 ms, altura ±50 cents), oitava incluída
    precision: float
    recall: float
    f1_any_octave: float  # idem ignorando a oitava
    onset_f1: float  # só o início da nota (ritmo), qualquer altura
    octave_errors: float  # das notas casadas ignorando a oitava: fração na oitava errada
    time_correct: float  # do tempo em que a referência soa: fração com a mesma nota na saída
    time_any_octave: float
    extra_time: float  # do tempo em que a saída soa: fração com a referência em silêncio
    onset_jitter_ms: float  # desvio-padrão do erro de início das notas casadas
    offset: float  # s somados aos tempos da saída para alinhar com a referência

    def lines(self) -> list[str]:
        pct = lambda v: f"{100 * v:5.1f}%"
        return [
            f"notas (referência / saída)     : {self.ref_notes} / {self.est_notes}",
            f"F1 por nota (50 ms, 50 cents)  : {pct(self.f1)}  (P {pct(self.precision)}, R {pct(self.recall)})",
            f"F1 ignorando a oitava          : {pct(self.f1_any_octave)}",
            f"F1 só do início (ritmo)        : {pct(self.onset_f1)}",
            f"erro de oitava (notas casadas) : {pct(self.octave_errors)}",
            f"tempo com a nota certa         : {pct(self.time_correct)}  (qualquer oitava {pct(self.time_any_octave)})",
            f"tempo soando onde a ref. cala  : {pct(self.extra_time)}",
            f"jitter de início               : {self.onset_jitter_ms:.0f} ms (alinhamento {self.offset:+.3f} s)",
        ]

    def to_dict(self) -> dict:
        return asdict(self)


def _arrays(notes: list[tuple], offset: float = 0.0, fold: bool = False):
    iv = np.array([(a + offset, b + offset) for a, b, _ in notes], dtype=float).reshape(-1, 2)
    p = np.array([60 + p % 12 if fold else p for _, _, p in notes], dtype=float)
    return iv, 440.0 * 2 ** ((p - 69) / 12)


def _frames(notes: list[tuple], n: int, offset: float = 0.0) -> np.ndarray:
    out = np.full(n, -1)
    for a, b, p in notes:
        i, j = max(round((a + offset) / GRID), 0), min(round((b + offset) / GRID), n)
        out[i:j] = p
    return out


def _f1(tp: int, n_ref: int, n_est: int) -> tuple[float, float, float]:
    p, r = (tp / n_est if n_est else 0.0), (tp / n_ref if n_ref else 0.0)
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def compare(ref: list[tuple], est: list[tuple], max_offset: float = 1.0) -> Comparison:
    """Compara duas listas (início, fim, nota MIDI): `ref` = esperado, `est` = gerado.

    Um deslocamento global de tempo (até ±`max_offset` s; as duas gravações raramente começam no
    mesmo instante) é escolhido pelo melhor casamento de inícios, sem olhar a altura, e vale para
    todas as medidas. A altura NÃO é realinhada: tocar em outra oitava conta como erro de oitava.
    """
    from mir_eval.transcription import match_note_onsets, match_notes

    if not ref or not est:
        return Comparison(len(ref), len(est), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    ref_iv, ref_hz = _arrays(ref)

    def onset_pairs(offset: float):
        return match_note_onsets(ref_iv, _arrays(est, offset)[0], onset_tolerance=ONSET_TOL)

    def fit(offset: float) -> tuple[int, float]:  # mais inícios casados; empate: menor erro
        pairs = onset_pairs(offset)
        err = sum(abs(est[j][0] + offset - ref[i][0]) for i, j in pairs)
        return len(pairs), -err / max(len(pairs), 1)

    offset = float(max(np.arange(-max_offset, max_offset + 1e-9, 0.01), key=fit))
    est_iv, est_hz = _arrays(est, offset)
    kw = {"onset_tolerance": ONSET_TOL, "pitch_tolerance": 50.0, "offset_ratio": None}
    exact = match_notes(ref_iv, ref_hz, est_iv, est_hz, **kw)
    folded = match_notes(
        ref_iv, _arrays(ref, fold=True)[1], est_iv, _arrays(est, fold=True)[1], **kw
    )
    onsets = onset_pairs(offset)
    precision, recall, f1 = _f1(len(exact), len(ref), len(est))
    wrong_octave = sum(ref[i][2] != est[j][2] for i, j in folded)
    errors = np.array([est_iv[j, 0] - ref_iv[i, 0] for i, j in folded])

    n = round(max(ref_iv.max(), est_iv.max()) / GRID) + 1
    r, e = _frames(ref, n), _frames(est, n, offset)
    r_on, e_on = r >= 0, e >= 0
    return Comparison(
        ref_notes=len(ref),
        est_notes=len(est),
        f1=f1,
        precision=precision,
        recall=recall,
        f1_any_octave=_f1(len(folded), len(ref), len(est))[2],
        onset_f1=_f1(len(onsets), len(ref), len(est))[2],
        octave_errors=wrong_octave / len(folded) if folded else 0.0,
        time_correct=float(np.mean(r[r_on] == e[r_on])),
        time_any_octave=float(np.mean(e_on[r_on] & ((r[r_on] - e[r_on]) % 12 == 0))),
        extra_time=float(np.mean(~r_on[e_on])) if e_on.any() else 0.0,
        onset_jitter_ms=float(1000 * errors.std()) if len(errors) else 0.0,
        offset=offset,
    )


def main() -> None:
    import argparse
    from pathlib import Path

    from .ffmpeg import decode_mono

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("expected", type=Path, help="áudio da melodia em onda quadrada (referência)")
    ap.add_argument("generated", type=Path, help="WAV gerado pelo app")
    ap.add_argument("--min-hz", type=float, default=100)
    ap.add_argument("--max-hz", type=float, default=1000)
    a = ap.parse_args()
    out = decode_mono(a.generated, RATE)
    play = playability(out, a.min_hz, a.max_hz)
    print(
        f"tocável: {'sim' if play.ok else 'NÃO'}  (pico {play.peak_db:.1f} dBFS, "
        f"{play.min_hz:.0f}-{play.max_hz:.0f} Hz, duty {100 * play.duty:.0f}%, "
        f"2 níveis {100 * play.two_level:.0f}%)"
    )
    for p in play.problems:
        print(f"  - {p}")
    ref = square_notes(decode_mono(a.expected, RATE))
    print("\n".join(compare(ref, square_notes(out)).lines()))


if __name__ == "__main__":
    main()
