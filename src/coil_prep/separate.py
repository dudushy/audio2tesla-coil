"""Separação de partes da música (stems). Roda em CPU: é lenta, por isso há cache.

Separadores (escolha do usuário; medidos num PC de 6 núcleos, 30 s de música):
- "demucs": Demucs htdemucs, 4 partes (voz, bateria, baixo, outros). ~24 s.
- "demucs_hq": Demucs htdemucs_ft (4 modelos afinados, um por parte). Melhor, ~90 s.
- "roformer": Mel-Band RoFormer (python-audio-separator), estado da arte para isolar a voz.
  Só separa voz x instrumental; as outras partes seguem com o Demucs.
Piano e guitarra só existem no Demucs de 6 partes (htdemucs_6s, ~23 s), qualquer que seja o
separador escolhido (a separação de piano é a mais fraca dos modelos).
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .downloads import fetch

STEMS = ("vocals", "other", "bass", "drums", "piano", "guitar")
SEPARATOR_CHOICES = ("demucs", "demucs_hq", "roformer")
MODEL = "htdemucs"
DEMUCS_MODELS = {"demucs": "htdemucs", "demucs_hq": "htdemucs_ft"}
SIX_STEMS = "htdemucs_6s"
ROFORMER_MODEL = "vocals_mel_band_roformer.ckpt"  # Kimberley Jensen, SDR 12,6 na voz (a melhor)
ROFORMER_FILES = {  # baixados aqui (com retomada); o audio-separator só os encontra prontos
    "vocals_mel_band_roformer.ckpt": 913106900,
    "vocals_mel_band_roformer.yaml": 944,
}
ROFORMER_URL = (
    "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/"
)
DEFAULT_STEM = "other"  # melhor na Blue (melodia em sintes); em música cantada teste --stem vocals
DEFAULT_DIR = Path("separated")  # já está no .gitignore


def model_for(stem: str, separator: str = "demucs") -> str:
    """Nome do modelo que separa `stem` com o `separator` escolhido (também a pasta do cache)."""
    if separator not in SEPARATOR_CHOICES:
        raise ValueError(f"separador inválido: {separator}")
    if stem in ("piano", "guitar"):
        return SIX_STEMS
    if separator == "roformer" and stem == "vocals":
        return "roformer"
    return DEMUCS_MODELS.get(separator, MODEL)


def stem_path(src: Path, stem: str, out_dir: Path, model: str = MODEL) -> Path:
    """Onde fica o stem: <out>/<modelo>/<nome da faixa>/<stem>.wav."""
    return out_dir / model / src.stem / f"{stem}.wav"


def separate(
    src: Path, stem: str = DEFAULT_STEM, out_dir: Path = DEFAULT_DIR, separator: str = "demucs"
) -> Path:
    """Devolve o WAV do stem pedido, separando só se ainda não existir em `out_dir`."""
    if stem not in STEMS:
        raise ValueError(f"parte inválida: {stem}")
    model = model_for(stem, separator)
    target = stem_path(src, stem, out_dir, model)
    if target.exists():
        return target
    if model == "roformer":
        return _roformer(src, target)
    return _demucs(src, stem, out_dir, model, target)


def _demucs(src: Path, stem: str, out_dir: Path, model: str, target: Path) -> Path:
    if importlib.util.find_spec("demucs") is None:
        raise RuntimeError(
            "Demucs não instalado. Instale o extra: pip install -e '.[separate]' "
            "(no Docker: rebuild com WITH_SEPARATE=1)\n"
            "(baixa o PyTorch, vários GB; o modelo é baixado na primeira execução)"
        )
    cmd = [
        sys.executable, "-m", "demucs", "-d", "cpu", "-n", model,
        "--two-stems", stem, "-o", str(out_dir), str(src),
    ]  # fmt: skip
    # saída capturada: o TUI ocupa o terminal, então o Demucs não pode escrever nele
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0 or not target.exists():
        tail = (result.stderr or "").strip().splitlines()[-5:]
        raise RuntimeError(f"Demucs falhou ao separar {src}:\n" + "\n".join(tail))
    return target


def _roformer(src: Path, target: Path) -> Path:
    """Voz com o Mel-Band RoFormer, num processo à parte (modelo grande, memória devolvida ao fim)."""
    if importlib.util.find_spec("audio_separator") is None:
        raise RuntimeError(
            "RoFormer não instalado: pip install 'audio-separator[cpu]' "
            "(no Docker: rebuild com WITH_SEPARATE=1)"
        )
    work = target.parent / "_roformer"
    work.mkdir(parents=True, exist_ok=True)
    code = (
        "import sys\n"
        "from audio_separator.separator import Separator\n"
        "s = Separator(output_dir=sys.argv[2], model_file_dir=sys.argv[3], output_single_stem='Vocals')\n"
        "s.load_model(model_filename=sys.argv[4])\n"
        "print('\\n'.join(s.separate(sys.argv[1])))\n"
    )
    models_dir = (
        Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "audio-separator"
    )
    for name, size in ROFORMER_FILES.items():
        fetch(ROFORMER_URL + name, models_dir / name, size)
    cmd = [sys.executable, "-c", code, str(src), str(work), str(models_dir), ROFORMER_MODEL]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    made = [work / Path(p.strip()).name for p in result.stdout.splitlines() if p.strip()]
    made = [p for p in made if p.is_file()]
    if result.returncode != 0 or not made:
        tail = (result.stderr or "").strip().splitlines()[-5:]
        raise RuntimeError(f"RoFormer falhou ao separar {src}:\n" + "\n".join(tail))
    shutil.move(str(made[0]), target)
    shutil.rmtree(work, ignore_errors=True)
    return target
