"""MIDIWorld (https://www.midiworld.com): busca de MIDIs de pop, rock, TV, games...

O site não tem API nem robots.txt; a busca é uma página HTML simples (30 resultados por página)
com link direto para cada arquivo. O app só lê a busca e baixa UM arquivo quando a pessoa pede
para gerar, com no mínimo 1 s entre pedidos. Os MIDIs são versões de obras com direitos
autorais: uso pessoal.
"""

import html
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.midiworld.com"
USER_AGENT = "coil-prep (uso pessoal)"
MIN_INTERVAL = 1.0
TTL = 600.0
MAX_QUERY = 100
MAX_HTML = 2 * 1024 * 1024
MAX_FILE = 5 * 1024 * 1024
PAGE_SIZE = 30

_ITEM = re.compile(
    r"<li>\s*(?P<name>[^<]+?)\s*-\s*<a href=\"https://www\.midiworld\.com/download/(?P<id>\d+)\""
)
_lock = threading.Lock()
_last = 0.0
_cache: dict[tuple[str, int], tuple[float, dict]] = {}
_files: dict[int, bytes] = {}
_broken: set[int] = set()  # links que o próprio site devolve 404 (arquivo sumiu de lá)
BROKEN = "o MIDIWorld não tem mais este arquivo (link quebrado no próprio site); tente outra versão ou outro acervo"


def _get(url: str, limit: int) -> bytes:
    global _last
    with _lock:  # serializa e espaça os pedidos ao site
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as res:
            data = res.read(limit + 1)
    if len(data) > limit:
        raise ValueError("arquivo grande demais")
    return data


def parse_search(page_html: str) -> dict:
    """[{id, title, artist}] e se há próxima página. "Título (Artista)" vira os dois campos."""
    results = []
    for m in _ITEM.finditer(page_html):
        name = html.unescape(" ".join(m["name"].split()))
        artist = ""
        paren = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", name)
        if paren:
            name, artist = paren[1], paren[2]
        sid = int(m["id"])
        results.append({"id": sid, "title": name, "artist": artist, "broken": sid in _broken})
    return {"results": results, "has_next": 'class="nomark">next' in page_html}


def search(query: str, page: int = 0) -> dict:
    query = " ".join(query.split())
    if not query or len(query) > MAX_QUERY:
        raise ValueError("digite o que buscar (até 100 caracteres)")
    if page < 0:
        raise ValueError("página inválida")
    key = (query.lower(), page)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1] | {"results": [r | {"broken": r["id"] in _broken} for r in hit[1]["results"]]}
    q = urllib.parse.urlencode({"q": query})
    url = f"{BASE}/search/?{q}" if page == 0 else f"{BASE}/search/{page + 1}/?{q}"
    data = parse_search(_get(url, MAX_HTML).decode("utf-8", "replace"))
    if len(_cache) >= 100:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), data)
    return data


def download(song_id: int) -> bytes:
    if not isinstance(song_id, int) or song_id <= 0:
        raise ValueError("música inválida")
    if song_id in _broken:
        raise ValueError(BROKEN)
    if song_id not in _files:
        try:
            data = _get(f"{BASE}/download/{song_id}", MAX_FILE)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                _broken.add(song_id)
                raise ValueError(BROKEN) from None
            raise
        if not data.startswith(b"MThd"):
            raise ValueError("o arquivo baixado não é um MIDI")
        if len(_files) >= 30:
            _files.pop(next(iter(_files)))
        _files[song_id] = data
    return _files[song_id]
