"""Gravação do microfone: qualquer formato do navegador/celular -> WAV mono no volume certo."""

from pathlib import Path

import numpy as np

from .calibrate import RATE, write_mono_wav
from .ffmpeg import decode_mono

MIN_SECONDS = 1.0
MAX_SECONDS = 600.0
SILENCE_PEAK = 0.005  # ~-46 dBFS: abaixo disso não há o que transcrever
TARGET_PEAK = 10 ** (-1 / 20)  # -1 dBFS


def to_wav(src: Path, dst: Path) -> Path:
    """Converte e normaliza pelo pico (microfone costuma gravar baixo). Erro claro se vazia."""
    samples = decode_mono(src, RATE)
    if len(samples) < MIN_SECONDS * RATE:
        raise ValueError("gravação curta demais: grave pelo menos 1 segundo")
    if len(samples) > MAX_SECONDS * RATE:
        raise ValueError("gravação longa demais: o máximo é 10 minutos")
    peak = float(np.abs(samples).max())
    if not peak >= SILENCE_PEAK:  # também pega NaN
        raise ValueError("a gravação está em silêncio: confira o microfone e grave de novo")
    dst.parent.mkdir(parents=True, exist_ok=True)
    write_mono_wav(dst, samples * (TARGET_PEAK / peak), RATE)
    return dst
