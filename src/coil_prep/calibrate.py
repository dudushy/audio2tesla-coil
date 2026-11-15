import wave
from pathlib import Path

import numpy as np

RATE = 44100
DEFAULT_FREQS = [100, 150, 200, 300, 440, 600, 800, 1000, 1500, 2000, 3000, 4000, 6000, 8000]


def square_tones(freqs: list[float], seconds: float, gap: float, peak_db: float) -> np.ndarray:
    """Sequência de ondas quadradas em volume constante, separadas por silêncio."""
    amp = 10 ** (peak_db / 20)
    t = np.arange(int(RATE * seconds)) / RATE
    silence = np.zeros(int(RATE * gap))
    parts = []
    for f in freqs:
        parts += [amp * np.where(np.sin(2 * np.pi * f * t) >= 0, 1.0, -1.0), silence]
    return np.concatenate(parts)


def write_mono_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
