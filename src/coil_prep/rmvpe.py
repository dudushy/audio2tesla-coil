"""RMVPE: altura da voz cantada medida direto na música (com instrumentos), sem separar antes.

Modelo de Wei, Li et al. ("RMVPE: A Robust Model for Vocal Pitch Estimation in Polyphonic
Music", Interspeech 2023; https://github.com/Dream-High/RMVPE). Usa os pesos em ONNX publicados
pelo projeto RVC (https://huggingface.co/lj1995/VoiceConversionWebUI, licença MIT; 362 MB,
baixados na 1ª vez para o cache). Roda no onnxruntime, o mesmo do Basic Pitch: não precisa do
PyTorch. O pré e o pós-processamento seguem infer/rmvpe.py do RVC (MIT).
"""

import os
import threading
from pathlib import Path

import numpy as np

from .downloads import fetch

MODEL_URL = "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/rmvpe.onnx"
MODEL_BYTES = 361_688_443
SR = 16000
HOP = 160  # 10 ms por quadro
N_FFT = 1024
CHUNK = 32 * 100  # quadros por chamada ao modelo (32 s); múltiplo de 32, como o modelo exige
THRESHOLD = 0.03  # saliência mínima para haver voz (valor do RVC)
_CENTS = np.pad(20 * np.arange(360) + 1997.3794084376191, (4, 4))

_lock = threading.Lock()
_session = None
_mel_basis = None


def model_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "coil-prep" / "rmvpe.onnx"


def _model():
    global _session, _mel_basis
    with _lock:
        if _session is None:
            try:
                import librosa
                import onnxruntime as ort
            except ImportError as e:
                raise RuntimeError(f"RMVPE precisa de onnxruntime e librosa ({e})") from None
            path = model_path()
            fetch(MODEL_URL, path, MODEL_BYTES)
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            _session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
            _mel_basis = librosa.filters.mel(
                sr=SR, n_fft=N_FFT, n_mels=128, fmin=30, fmax=8000, htk=True
            )
    return _session, _mel_basis


def _log_mel(y: np.ndarray, basis: np.ndarray) -> np.ndarray:
    import librosa

    # como torch.stft(center=True): borda espelhada, janela de Hann periódica, magnitude
    spec = np.abs(
        librosa.stft(y, n_fft=N_FFT, hop_length=HOP, win_length=N_FFT, window="hann",
                     center=True, pad_mode="reflect")
    )  # fmt: skip
    return np.log(np.maximum(basis @ spec, 1e-5)).astype(np.float32)


def _decode(salience: np.ndarray) -> np.ndarray:
    """Média ponderada de 9 bins em volta do pico (cents) -> Hz; 0 onde não há voz."""
    center = np.argmax(salience, axis=1) + 4
    padded = np.pad(salience, ((0, 0), (4, 4)))
    idx = center[:, None] + np.arange(-4, 5)[None, :]
    weights = np.take_along_axis(padded, idx, axis=1)
    cents = (weights * _CENTS[idx]).sum(1) / np.maximum(weights.sum(1), 1e-9)
    cents[padded.max(axis=1) <= THRESHOLD] = 0
    f0 = 10 * 2 ** (cents / 1200)
    f0[cents == 0] = 0
    return f0


def pitch(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(tempos em s, f0 em Hz com 0 sem voz, confiança 0..1) a cada 10 ms."""
    import librosa

    session, basis = _model()
    if sr != SR:
        y = librosa.resample(y.astype(np.float32), orig_sr=sr, target_sr=SR)
    mel = _log_mel(y.astype(np.float32), basis)
    frames = mel.shape[1]
    name_in, name_out = session.get_inputs()[0].name, session.get_outputs()[0].name
    parts = []
    for a in range(0, frames, CHUNK):
        block = mel[:, a : a + CHUNK]
        n = block.shape[1]
        pad = 32 * ((n - 1) // 32 + 1) - n
        if pad:
            block = np.pad(block, ((0, 0), (0, pad)))
        out = session.run([name_out], {name_in: block[None]})[0][0]
        parts.append(out[:n])
    salience = np.concatenate(parts) if parts else np.zeros((0, 360), np.float32)
    f0 = _decode(salience)
    return np.arange(len(f0)) * HOP / SR, f0, salience.max(axis=1) if len(f0) else f0


def voice_notes(path) -> list[tuple]:
    """Melodia cantada de uma música inteira (ou da parte vocals) como notas com afinação exata.

    A altura vem do RMVPE; a limpeza (nota x ruído, vibrato, sílabas pelas quedas de volume) é a
    mesma da gravação de voz (`voice.notes_from_track`).
    """
    import librosa

    from .ffmpeg import decode_mono
    from .voice import notes_from_track

    y = decode_mono(path, SR)
    times, f0, conf = pitch(y, SR)
    midi = np.where(f0 > 0, 69 + 12 * np.log2(np.maximum(f0, 1e-9) / 440.0), np.nan)
    level = librosa.feature.rms(y=y, frame_length=736, hop_length=HOP, center=True)[0]
    n = min(len(midi), len(level))
    return notes_from_track(times[:n], midi[:n], conf[:n], level[:n])
