"""Separação de stems com Demucs (extra [separate]). Roda em CPU: é lento, por isso há cache."""

import importlib.util
import subprocess
import sys
from pathlib import Path

STEMS = ("vocals", "other", "bass", "drums")
MODEL = "htdemucs"
DEFAULT_DIR = Path("separated")  # já está no .gitignore


def stem_path(src: Path, stem: str, out_dir: Path) -> Path:
    """Onde o Demucs grava o stem: <out>/<modelo>/<nome da faixa>/<stem>.wav."""
    return out_dir / MODEL / src.stem / f"{stem}.wav"


def separate(src: Path, stem: str = "vocals", out_dir: Path = DEFAULT_DIR) -> Path:
    """Devolve o WAV do stem pedido, rodando o Demucs só se ainda não existir em `out_dir`."""
    target = stem_path(src, stem, out_dir)
    if target.exists():
        print(f"usando stem em cache: {target}", file=sys.stderr)
        return target
    if importlib.util.find_spec("demucs") is None:
        raise RuntimeError(
            "Demucs não instalado. Instale o extra: pip install -e '.[separate]'\n"
            "(baixa o PyTorch, vários GB; o modelo é baixado na primeira execução)"
        )
    print(f"separando '{stem}' com Demucs em CPU (pode levar vários minutos)...", file=sys.stderr)
    cmd = [
        sys.executable, "-m", "demucs", "-d", "cpu", "-n", MODEL,
        "--two-stems", stem, "-o", str(out_dir), str(src),
    ]  # fmt: skip
    if subprocess.run(cmd, check=False).returncode != 0 or not target.exists():
        raise RuntimeError(f"Demucs falhou ao separar {src} (veja a saída acima)")
    return target
