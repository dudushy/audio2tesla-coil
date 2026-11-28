"""YourMT3+: transcrição de vários instrumentos (Chang et al., 2024), pelo mt3-infer (MIT; código
do YourMT3 sob Apache-2.0). No desafio de transcrição de 2025 ficou bem acima do Basic Pitch em
música com vários instrumentos. É pesado: modelo de 536 MB, baixado na 1ª vez para o cache, e em
CPU leva bem mais que a duração da música. Roda num processo à parte, que devolve a memória ao
terminar; o resultado é um .mid lido como as trilhas de um arquivo de notas.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .midi import Note, read_midi_parts

MODEL = "yourmt3"
SR = 16000

_SCRIPT = """
import sys
import numpy as np
from mt3_infer import load_model
from coil_prep.ffmpeg import decode_mono
audio = decode_mono(sys.argv[1], 16000).astype(np.float32)
midi = load_model(sys.argv[3], device="cpu").transcribe(audio, sr=16000)
midi.save(sys.argv[2])
"""


def checkpoint_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "mt3"


def transcribe_notes(path: Path) -> list[Note]:
    """Notas de todos os instrumentos (sem bateria) transcritas pelo YourMT3+."""
    if importlib.util.find_spec("mt3_infer") is None:
        raise RuntimeError(
            "YourMT3+ não instalado: pip install mt3-infer (no Docker: rebuild com WITH_MT3=1)"
        )
    env = os.environ | {"MT3_CHECKPOINT_DIR": str(checkpoint_dir())}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "mt3.mid"
        cmd = [sys.executable, "-c", _SCRIPT, str(path), str(out), MODEL]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, env=env)
        if result.returncode != 0 or not out.is_file():
            tail = (result.stderr or "").strip().splitlines()[-5:]
            raise RuntimeError("YourMT3+ falhou:\n" + "\n".join(tail))
        notes = [n for part in read_midi_parts(out) for n in part.notes]
    return sorted(notes, key=lambda n: (n.start, n.pitch))
