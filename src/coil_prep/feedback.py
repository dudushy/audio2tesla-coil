"""Histórico de avaliações de ouvido (feedback.jsonl, fora do git) para sugerir o melhor stem."""

import json
import time
from pathlib import Path

from .notes import EditNote

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


EPS = 1e-3  # s; diferença menor que 1 ms não conta como "movida"


def _tuple(n: EditNote) -> list:
    return [round(n.start, 3), round(n.end, 3), n.pitch]


def diff_notes(generated: list[EditNote], final: list[EditNote]) -> dict:
    """Diferença exata entre o que o app gerou e o que o usuário deixou (casando por `id`).

    `deleted`: geradas que sumiram; `added`: notas novas; `moved`: mesmo id com início, fim ou
    tom diferentes (com as variações). `summary` tem as contagens, `kept` = iguais às geradas.
    """
    gen = {n.id: n for n in generated}
    fin = {n.id: n for n in final}
    deleted = [_tuple(gen[i]) for i in gen if i not in fin]
    added = [_tuple(fin[i]) for i in fin if i not in gen]
    moved = []
    for i in gen.keys() & fin.keys():
        g, f = gen[i], fin[i]
        if abs(g.start - f.start) > EPS or abs(g.end - f.end) > EPS or g.pitch != f.pitch:
            moved.append(
                {
                    "from": _tuple(g),
                    "to": _tuple(f),
                    "pitch_delta": f.pitch - g.pitch,
                    "start_delta": round(f.start - g.start, 3),
                    "end_delta": round(f.end - g.end, 3),
                }
            )
    moved.sort(key=lambda m: m["from"])
    summary = {
        "generated": len(generated),
        "final": len(final),
        "kept": len(gen.keys() & fin.keys()) - len(moved),
        "deleted": len(deleted),
        "added": len(added),
        "moved": len(moved),
    }
    return {"summary": summary, "deleted": deleted, "added": added, "moved": moved}


def has_changes(diff: dict) -> bool:
    s = diff["summary"]
    return bool(s["deleted"] or s["added"] or s["moved"])


def count_ratings(entries: list[dict]) -> int:
    return sum(1 for e in entries if e.get("rating") in (1, -1))
