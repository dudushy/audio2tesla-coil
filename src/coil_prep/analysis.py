from dataclasses import dataclass

import numpy as np

CLIP_THRESHOLD = 0.999
CLIP_MIN_RUN = 3  # amostras consecutivas no teto contam como clipping


@dataclass(frozen=True)
class Report:
    peak_db: float
    rms_db: float
    clipped: bool


def _to_db(x: float) -> float:
    return 20 * np.log10(x) if x > 0 else float("-inf")


def _has_clipping(samples: np.ndarray) -> bool:
    hot = np.abs(samples) >= CLIP_THRESHOLD
    if hot.sum() < CLIP_MIN_RUN:
        return False
    # runs consecutivos: soma deslizante de janela CLIP_MIN_RUN
    window = np.convolve(hot.astype(np.int8), np.ones(CLIP_MIN_RUN, dtype=np.int8), "valid")
    return bool((window == CLIP_MIN_RUN).any())


def analyze(samples: np.ndarray) -> Report:
    if samples.size == 0:
        raise ValueError("áudio vazio")
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return Report(peak_db=_to_db(peak), rms_db=_to_db(rms), clipped=_has_clipping(samples))
