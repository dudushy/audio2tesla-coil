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
            "  Windows:      winget install Gyan.FFmpeg\n"
            "  Ubuntu/WSL:   sudo apt install ffmpeg"
        )
    return path


def build_filter(highpass: float, lowpass: float, limit: float, rate: int) -> str:
    # aresample antes do limiter: reamostrar depois poderia ultrapassar o limite.
    # level=0 desliga o auto-level do alimiter, que mexe no ganho final.
    return (
        f"aresample={rate},highpass=f={highpass:g},lowpass=f={lowpass:g},"
        f"alimiter=limit={limit:g}:level=0"
    )


def _run(args: list[str]) -> bytes:
    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", *args]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        raise FfmpegError(result.stderr.decode(errors="replace").strip() or "ffmpeg falhou")
    return result.stdout


def prep_file(
    src: Path, dst: Path, highpass: float, lowpass: float, limit: float, rate: int
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-af",
            build_filter(highpass, lowpass, limit, rate),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )


def decode_mono(path: Path) -> np.ndarray:
    """Decodifica qualquer formato para float32 mono (-1..1) na taxa original."""
    data = _run(["-i", str(path), "-vn", "-ac", "1", "-f", "f32le", "-"])
    return np.frombuffer(data, dtype="<f4")
