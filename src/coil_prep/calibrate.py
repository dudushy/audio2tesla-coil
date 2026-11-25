import wave
from pathlib import Path

import numpy as np

RATE = 44100


def write_mono_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
