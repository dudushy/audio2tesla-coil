"""Acervo de músicas de note block (.nbs) do MC Utils (https://mcutils.com/note-block-songs).

O site lista e baixa os arquivos do repositório público https://github.com/flytegg/nbs-songs
(que se descreve como "em domínio público"; o robots.txt do site libera robôs). Aqui se faz o
mesmo: a lista vem da árvore do repositório (1 pedido, guardado por 1 h) e cada música é baixada
do endereço "raw" do GitHub. Só nomes que estão na lista podem ser baixados.
"""

import json
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

TREE_URL = "https://api.github.com/repos/flytegg/nbs-songs/git/trees/master"
RAW_URL = "https://raw.githubusercontent.com/flytegg/nbs-songs/master/"
DURATIONS_URL = "https://mcutils.com/nbs/durations.json"  # nome -> segundos (opcional)
USER_AGENT = "coil-prep (uso pessoal)"
LIST_TTL = 3600.0
PAGE_SIZE = 20
MAX_QUERY = 100
MAX_FILE = 5 * 1024 * 1024
MIN_INTERVAL = 0.5  # s entre pedidos ao GitHub

_lock = threading.Lock()
_last = 0.0
_list: tuple[float, list[dict]] = (0.0, [])
_files: dict[str, bytes] = {}


def _get(url: str, limit: int, accept: str = "*/*") -> bytes:
    global _last
    with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as res:
        data = res.read(limit + 1)
    if len(data) > limit:
        raise ValueError("arquivo grande demais")
    return data


def _fold(text: str) -> str:
    """Minúsculas e sem acento, para a busca achar "pokemon" em "Pokémon"."""
    norm = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in norm if not unicodedata.combining(c))


def songs() -> list[dict]:
    """[{name, title, seconds}] de todas as músicas, em ordem alfabética (lista guardada por 1 h)."""
    global _list
    stamp, cached = _list
    if cached and time.monotonic() - stamp < LIST_TTL:
        return cached
    tree = json.loads(_get(TREE_URL, 10 * 1024 * 1024, "application/vnd.github+json"))
    names = [
        t["path"]
        for t in tree.get("tree", [])
        if t.get("type") == "blob" and t["path"].lower().endswith(".nbs") and "/" not in t["path"]
    ]
    try:
        durations = json.loads(_get(DURATIONS_URL, 2 * 1024 * 1024))
    except (OSError, ValueError):
        durations = {}  # a duração é só informativa
    out = [
        {"name": n, "title": n[: -len(".nbs")], "seconds": durations.get(n)}
        for n in sorted(names, key=str.lower)
    ]
    if out:
        _list = (time.monotonic(), out)
    return out


def search(query: str, page: int = 0) -> dict:
    """Busca por todas as palavras no nome (sem acento/maiúsculas). Vazia = lista tudo."""
    query = " ".join(query.split())
    if len(query) > MAX_QUERY:
        raise ValueError("busca longa demais")
    if page < 0:
        raise ValueError("página inválida")
    words = _fold(query).split()
    found = [s for s in songs() if all(w in _fold(s["title"]) for w in words)]
    start = page * PAGE_SIZE
    return {
        "results": found[start : start + PAGE_SIZE],
        "total": len(found),
        "has_next": start + PAGE_SIZE < len(found),
    }


def download(name: str) -> bytes:
    """Bytes do .nbs `name` (precisa estar na lista do acervo)."""
    if name not in {s["name"] for s in songs()}:
        raise ValueError("música não encontrada no acervo")
    if name not in _files:
        data = _get(RAW_URL + urllib.parse.quote(name), MAX_FILE)
        if len(_files) >= 30:
            _files.pop(next(iter(_files)))
        _files[name] = data
    return _files[name]
