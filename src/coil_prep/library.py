"""Busca única em todos os acervos de notas (MIDI, partituras e note block).

Cada fonte responde em paralelo, com limite de tempo; a que falhar ou demorar aparece com o erro,
sem derrubar as outras. Todos os resultados vêm no mesmo formato, com a origem e a ação:
- "generate": o app baixa e gera (POST em `endpoint` com `body`);
- "movements": partitura com vários movimentos (escolher um antes de gerar);
- "link": o site não permite baixar automaticamente; abre a página para baixar e enviar.
"""

from concurrent.futures import ThreadPoolExecutor, wait

from . import bitmidi, lakh, midiworld, nbssongs, noteblockworld, scores

TIMEOUT = 20.0  # s por busca; o resto da página não espera uma fonte lenta

SOURCES = {
    "lakh": {
        "label": "Lakh MIDI Dataset",
        "kind": "MIDI",
        "about": "~17 mil MIDIs de pop e rock (sobretudo até ~2010). CC-BY 4.0 (Colin Raffel).",
        "url": "https://colinraffel.com/projects/lmd/",
    },
    "midiworld": {
        "label": "MIDIWorld",
        "kind": "MIDI",
        "about": "MIDIs de pop, rock, TV, filmes e jogos enviados ao site desde 1995.",
        "url": "https://www.midiworld.com/",
    },
    "mcutils": {
        "label": "MC Utils",
        "kind": "NBS (Minecraft)",
        "about": "~550 arranjos de note block feitos por fãs (repositório flytegg/nbs-songs).",
        "url": "https://mcutils.com/note-block-songs",
    },
    "noteblockworld": {
        "label": "Note Block World",
        "kind": "NBS (Minecraft)",
        "about": "Site oficial do Open Note Block Studio. Baixar exige login no site: "
        "abra a página, baixe o .nbs e envie em Gerar.",
        "url": "https://noteblock.world/",
    },
    "mutopia": {
        "label": "Mutopia Project",
        "kind": "Partitura (MIDI)",
        "about": "Partituras clássicas em domínio público e Creative Commons, com o MIDI delas.",
        "url": "https://www.mutopiaproject.org/",
    },
    "bitmidi": {
        "label": "BitMidi",
        "kind": "MIDI",
        "about": "Acervo grande de MIDIs. O site não permite downloads automáticos: abra a "
        "página, baixe o .mid e envie em Gerar.",
        "url": "https://bitmidi.com/",
    },
}


def _mmss(seconds) -> str:
    if not seconds:
        return ""
    s = round(float(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _lakh(q: str, page: int) -> dict:
    r = lakh.search(q, page)
    items = [
        {
            "title": s["title"],
            "subtitle": s["artist"],
            "detail": f"versão {s['version']}" if s["version"] > 1 else "",
            "action": {
                "type": "generate",
                "endpoint": "/api/lakh/generate",
                "body": {"path": s["path"]},
            },
        }
        for s in r["results"]
    ]
    return {"results": items, "total": r["total"], "has_next": r["has_next"]}


def _midiworld(q: str, page: int) -> dict:
    r = midiworld.search(q, page)
    items = [
        {
            "title": s["title"],
            "subtitle": s["artist"],
            "detail": "",
            "action": {
                "type": "generate",
                "endpoint": "/api/midiworld/generate",
                "body": {"id": s["id"], "title": s["title"], "artist": s["artist"]},
            },
        }
        for s in r["results"]
    ]
    return {"results": items, "total": None, "has_next": r["has_next"]}


def _mcutils(q: str, page: int) -> dict:
    r = nbssongs.search(q, page)
    items = [
        {
            "title": s["title"],
            "subtitle": "",
            "detail": _mmss(s["seconds"]),
            "action": {
                "type": "generate",
                "endpoint": "/api/nbs-songs/generate",
                "body": {"name": s["name"]},
            },
        }
        for s in r["results"]
    ]
    return {"results": items, "total": r["total"], "has_next": r["has_next"]}


def _noteblockworld(q: str, page: int) -> dict:
    r = noteblockworld.search(q, page)
    items = [
        {
            "title": s["title"],
            "subtitle": s["author"],
            "detail": " · ".join(
                x
                for x in (_mmss(s["seconds"]), f"por {s['uploader']}" if s["uploader"] else "")
                if x
            ),
            "action": {"type": "link", "url": s["url"]},
        }
        for s in r["results"]
    ]
    return {"results": items, "total": r["total"], "has_next": r["has_next"]}


def _featured(q: str) -> list[dict]:
    """Peças em destaque (trilha da melodia já conferida) que batem com a busca."""
    words = [w for w in q.lower().split() if w]
    found = []
    for p in scores.FEATURED:
        text = f"{p['title']} {p['composer']}".lower()
        if words and all(w in text for w in words):
            found.append(
                {
                    "title": p["title"],
                    "subtitle": p["composer"],
                    "detail": "destaque: melodia conferida",
                    "action": {
                        "type": "generate",
                        "endpoint": "/api/scores/generate",
                        "body": {"file": p["file"], "title": p["title"], "part": p["part"]},
                    },
                }
            )
    return found


def _mutopia(q: str, page: int) -> dict:
    r = scores.search(q, page * scores.PAGE_SIZE)
    items = _featured(q) if page == 0 else []
    for s in r["results"]:
        title = s["title"]
        if s["file"].endswith(".zip"):
            action = {"type": "movements", "file": s["file"], "title": title}
        else:
            action = {
                "type": "generate",
                "endpoint": "/api/scores/generate",
                "body": {"file": s["file"], "title": title},
            }
        items.append(
            {
                "title": title,
                "subtitle": s["composer"],
                "detail": " · ".join(x for x in (s["instrument"], s["license"]) if x),
                "action": action,
            }
        )
    return {"results": items, "total": None, "has_next": r["has_next"]}


def _bitmidi(q: str, page: int) -> dict:
    r = bitmidi.search(q, page)
    items = [
        {
            "title": s["name"],
            "subtitle": "",
            "detail": "",
            "action": {"type": "link", "url": s["url"]},
        }
        for s in r["results"]
    ]
    return {"results": items, "total": None, "has_next": r["has_next"]}


PROVIDERS = {
    "lakh": _lakh,
    "midiworld": _midiworld,
    "mcutils": _mcutils,
    "noteblockworld": _noteblockworld,
    "mutopia": _mutopia,
    "bitmidi": _bitmidi,
}
_pool = ThreadPoolExecutor(max_workers=len(PROVIDERS), thread_name_prefix="busca")


def _one(source: str, q: str, page: int) -> dict:
    info = SOURCES[source] | {"id": source}
    try:
        data = PROVIDERS[source](q, page)
    except ValueError as e:
        return info | {"results": [], "error": str(e)}
    except Exception:  # noqa: BLE001 - site fora do ar, HTML mudou...: só esta fonte falha
        return info | {"results": [], "error": "fonte indisponível agora"}
    return info | data | {"page": page}


def search(q: str, source: str | None = None, page: int = 0) -> dict:
    """Todas as fontes (página 0) ou uma só (`source`, qualquer página)."""
    q = " ".join(q.split())
    if not q or len(q) > 100:
        raise ValueError("digite o que buscar (até 100 caracteres)")
    if source is not None:
        if source not in PROVIDERS:
            raise ValueError(f"fonte desconhecida: {source}")
        return {"query": q, "sources": [_one(source, q, page)]}
    futures = {s: _pool.submit(_one, s, q, 0) for s in PROVIDERS}
    wait(futures.values(), timeout=TIMEOUT)
    out = []
    for s, f in futures.items():
        if f.done():
            out.append(f.result())
        else:
            out.append(
                SOURCES[s] | {"id": s, "results": [], "error": "demorou demais para responder"}
            )
    return {"query": q, "sources": out}
