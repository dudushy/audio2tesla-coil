"""Download de modelos grandes com retomada: a conexão caindo no meio não recomeça do zero.

Os modelos (centenas de MB) vêm de GitHub/Hugging Face; numa rede lenta o download leva dezenas
de minutos e as bibliotecas originais recomeçam do zero quando a conexão cai.
"""

import os
import time
import urllib.request
from pathlib import Path

USER_AGENT = "coil-prep"
ATTEMPTS = 30
CHUNK = 1 << 20


def fetch(url: str, dst: Path, size: int | None = None) -> Path:
    """Baixa `url` em `dst` (via `dst`.part, com Range para retomar). Pula se já está completo."""
    if dst.is_file() and (size is None or dst.stat().st_size == size):
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    last_error = None
    for attempt in range(ATTEMPTS):
        have = part.stat().st_size if part.exists() else 0
        if size is not None and have >= size:
            break
        headers = {"User-Agent": USER_AGENT} | ({"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            ) as r:
                if have and r.status != 206:  # servidor ignorou o Range: recomeça
                    part.unlink()
                with open(part, "ab") as f:
                    while chunk := r.read(CHUNK):
                        f.write(chunk)
            if size is None:
                break
        except OSError as e:
            last_error = e
            time.sleep(min(30, 2 + 3 * attempt))
    got = part.stat().st_size if part.exists() else 0
    if size is not None and got != size:
        raise RuntimeError(
            f"download incompleto de {dst.name} ({got} de {size} bytes): {last_error}"
        )
    os.replace(part, dst)
    return dst
