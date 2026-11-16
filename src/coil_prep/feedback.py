"""Histórico de avaliações de ouvido (feedback.jsonl, fora do git) para sugerir o melhor stem."""

import json
import time
from pathlib import Path

FILE = "feedback.jsonl"
TAGS = (
    "melodia errada",
    "notas muito graves/agudas",
    "picotado",
    "silêncio demais",
    "outro",
)


def record(workdir: Path, entry: dict) -> None:
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    with (workdir / FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load(workdir: Path) -> list[dict]:
    path = workdir / FILE
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # linha corrompida não pode travar o app
    return entries


def suggest_stem(entries: list[dict], default: str = "other") -> str:
    """Stem com a maior média de avaliação (+1/-1); sem histórico, o padrão."""
    scores: dict[str, list[int]] = {}
    for e in entries:
        if e.get("stem") and e.get("rating") in (1, -1):
            scores.setdefault(e["stem"], []).append(e["rating"])
    if not scores:
        return default
    best = max(scores, key=lambda s: (sum(scores[s]) / len(scores[s]), s == default))
    return best if sum(scores[best]) > 0 else default
