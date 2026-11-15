import wave

import numpy as np
import pytest

RATE = 44100


def write_wav(path, samples: np.ndarray, rate: int = RATE) -> None:
    """Grava float (-1..1; shape (n,) ou (n, canais)) como WAV PCM 16-bit."""
    data = np.clip(samples, -1, 1)
    pcm = (data * 32767).astype("<i2")
    channels = 1 if pcm.ndim == 1 else pcm.shape[1]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def sine(freq: float, seconds: float = 1.0, amp: float = 1.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return amp * np.sin(2 * np.pi * freq * t)


@pytest.fixture
def hot_stereo(tmp_path):
    """Seno 440 Hz estéreo com amplitude 3x, clipado em ±1 (pior caso)."""
    mono = sine(440, amp=3.0)
    path = tmp_path / "hot.wav"
    write_wav(path, np.stack([mono, mono], axis=1))
    return path
