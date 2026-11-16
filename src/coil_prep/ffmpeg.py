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


def decode_mono(path: Path) -> np.ndarray:
    """Decodifica qualquer formato para float32 mono (-1..1) na taxa original."""
    data = run_ffmpeg(["-i", str(path), "-vn", "-ac", "1", "-f", "f32le", "-"])
    return np.frombuffer(data, dtype="<f4")
