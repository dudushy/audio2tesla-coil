import shutil
import subprocess
from pathlib import Path

import numpy as np


class FfmpegError(RuntimeError):
    pass


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise FfmpegError(
            "ffmpeg não encontrado no PATH. Instale e abra um novo terminal:\n"
            "  Ubuntu/WSL:   sudo apt install ffmpeg\n"
            "  Windows:      winget install Gyan.FFmpeg"
        )
    return path


def run_ffmpeg(args: list[str]) -> bytes:
    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", *args]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        raise FfmpegError(result.stderr.decode(errors="replace").strip() or "ffmpeg falhou")
    return result.stdout


def decode_mono(path: Path, rate: int | None = None) -> np.ndarray:
    """Decodifica qualquer formato para float32 mono (-1..1) na taxa original (ou em `rate` Hz)."""
    resample = ["-ar", str(rate)] if rate else []
    data = run_ffmpeg(["-i", str(path), "-vn", "-ac", "1", *resample, "-f", "f32le", "-"])
    return np.frombuffer(data, dtype="<f4")


def wav_to_mp3(src: Path, dst: Path) -> None:
    """MP3 VBR ~190 kbps (-q:a 2). Codec com perdas pode arredondar um pouco as quinas da quadrada."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-y", "-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(dst)])
