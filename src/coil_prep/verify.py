"""Verificação objetiva: o WAV de onda quadrada gerado toca as notas da música original?

Mede no ÁUDIO FINAL (não nas notas intermediárias), então pega erro em qualquer etapa: escolha da
melodia, transposição, dobra de oitava e o próprio render. Para cada nota de referência (as notas
da música original, antes do tratamento) detecta a frequência que de fato soa no WAV no meio da nota
e compara a altura. Uso: `python -m coil_prep.verify musica.mid [--min-hz 100 --max-hz 600]`.
"""

import itertools
import math
from dataclasses import dataclass

import numpy as np

from .midi import RATE, Note

WINDOW = 0.05  # s de áudio analisado em torno do meio da nota


def detect_midi_pitch(samples: np.ndarray, t: float, rate: int = RATE) -> float | None:
    """Altura (número MIDI fracionário) da onda quadrada em torno de `t`, ou None se silêncio.

    Usa os cruzamentos de zero ascendentes (interpolados): f = (ciclos) / (tempo entre o 1º e o último).
    """
    i, j = max(int((t - WINDOW / 2) * rate), 0), min(int((t + WINDOW / 2) * rate), len(samples))
    x = samples[i:j]
    if len(x) < 8 or np.max(np.abs(x)) < 1e-3:
        return None
    up = np.where((x[:-1] < 0) & (x[1:] >= 0))[0]
    if len(up) < 3:
        return None
    frac = -x[up] / (x[up + 1] - x[up])
    times = (up + frac) / rate
    hz = (len(up) - 1) / (times[-1] - times[0])
    return 69 + 12 * math.log2(hz / 440)


@dataclass(frozen=True)
class Report:
    notes: int  # notas de referência avaliadas
    heard: float  # fração com algum som no WAV no meio da nota
    pitch_class: float  # das ouvidas: mesma nota (dó, ré...), qualquer oitava
    exact_octave: float  # das ouvidas: mesma nota e mesma oitava (após 1 deslocamento global)
    in_harmony: float  # das ouvidas: nota existe em alguma voz ativa naquele instante
    contour: float  # pares consecutivos em que subiu/desceu igual à referência
    false_leaps: int  # saltos >= 1 oitava na saída onde a referência anda < 5 semitons
    global_shift: int  # deslocamento de oitavas (semitons) mais comum saída-referência
    cents_p95: (
        float  # desvio de afinação (percentil 95, cents) do que soa no WAV vs semitom inteiro
    )

    def lines(self) -> list[str]:
        pct = lambda v: f"{100 * v:5.1f}%"
        return [
            f"notas de referência        : {self.notes}",
            f"com som no WAV              : {pct(self.heard)}",
            f"nota certa (qualquer oitava): {pct(self.pitch_class)}",
            f"nota e oitava certas        : {pct(self.exact_octave)}  (deslocamento global {self.global_shift:+d} st)",
            f"nota existe na harmonia     : {pct(self.in_harmony)}",
            f"contorno (sobe/desce) certo : {pct(self.contour)}",
            f"saltos de oitava falsos     : {self.false_leaps}",
            f"erro de afinação (p95)     : {self.cents_p95:.1f} cents",
        ]


def compare(
    reference: list[Note],
    samples: np.ndarray,
    rate: int = RATE,
    min_note: float = 0.06,
    harmony: list[Note] | None = None,
) -> Report:
    """Compara `samples` (WAV final) com as notas de referência.

    `reference` é a melodia que se espera ouvir (1 voz). `harmony` são todas as notas da música
    (padrão: a própria referência): `in_harmony` mede se a nota ouvida existe em alguma voz ativa
    naquele instante, o mínimo para não soar errada mesmo quando a escolha da melodia diverge.
    """
    pool = harmony if harmony is not None else reference
    refs = sorted((n for n in reference if n.end - n.start >= min_note), key=lambda n: n.start)
    heard, cents = [], []
    for n in refs:
        mid = (n.start + n.end) / 2
        p = detect_midi_pitch(samples, mid, rate)
        if p is None:
            continue
        q = round(p)
        cents.append(abs(p - q) * 100)
        heard.append((n, mid, q))
    if not heard:
        return Report(len(refs), 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0)
    diffs = [q - n.pitch for n, _, q in heard]
    shift = _mode([round(d / 12) * 12 for d in diffs])
    pc = sum(d % 12 == 0 for d in diffs)
    exact = sum(d == shift for d in diffs)
    harmony = sum(
        any((q - m.pitch) % 12 == 0 for m in pool if m.start <= mid < m.end) for _, mid, q in heard
    )
    ok = leaps = pairs = 0
    for (a, _, qa), (b, _, qb) in itertools.pairwise(heard):
        ra, oa = b.pitch - a.pitch, qb - qa
        pairs += 1
        ok += (ra > 0) == (oa > 0) and (ra < 0) == (oa < 0)
        leaps += abs(oa) >= 12 and abs(ra) < 5
    return Report(
        notes=len(refs),
        heard=len(heard) / len(refs),
        pitch_class=pc / len(heard),
        exact_octave=exact / len(heard),
        in_harmony=harmony / len(heard),
        contour=ok / pairs if pairs else 1.0,
        false_leaps=leaps,
        global_shift=shift,
        cents_p95=float(np.percentile(cents, 95)),
    )


def _mode(values: list[int]) -> int:
    counts: dict[int, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda k: (counts[k], -abs(k)))


def main() -> None:
    import argparse
    import tempfile
    from pathlib import Path

    from .ffmpeg import decode_mono
    from .midi import combine_parts, read_midi_parts, select_part, skyline
    from .pipeline import process
    from .profile import Profile

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("midi", type=Path)
    ap.add_argument("--min-hz", type=float, default=100)
    ap.add_argument("--max-hz", type=float, default=600)
    ap.add_argument("--part", type=int, default=None, help="trilha (-1 = todas); padrão: combinada")
    a = ap.parse_args()
    parts = read_midi_parts(a.midi)
    notes = combine_parts(parts)[0] if a.part is None else select_part(parts, a.part).notes
    # referência: a voz mais aguda de cada instante (a melodia, para a maioria dos .mid)
    ref = [Note(s, e, p[0]) for s, e, p in skyline(notes, 1, 0.06, 0.0)]
    with tempfile.TemporaryDirectory() as tmp:
        res = process(str(a.midi), Path(tmp), Profile(a.min_hz, a.max_hz), "mix", part=a.part)
        print("\n".join(compare(ref, decode_mono(res.wav), harmony=notes).lines()))


if __name__ == "__main__":
    main()
