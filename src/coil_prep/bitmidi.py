"""Busca no BitMidi (https://bitmidi.com), só nas páginas de busca.

O BitMidi não tem API. O robots.txt dele proíbe `/uploads/` (onde ficam os .mid), então
este módulo NÃO baixa arquivos: só lê `/search` (permitido) e devolve os links das páginas
de detalhe. O usuário abre a página, baixa o .mid e envia pelo campo de arquivo.
"""

import re
import threading
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

SEARCH_URL = "https://bitmidi.com/search"
USER_AGENT = "coil-prep (uso pessoal; busca manual)"
TTL = 600.0  # cache em memória: a mesma busca não bate de novo no site por 10 min
MIN_INTERVAL = 1.0  # no mínimo 1 s entre requisições ao site
MAX_QUERY = 100
PAGE_SIZE = 15

_SLUG = re.compile(r"^(?:https://bitmidi\.com)?/([a-z0-9_-]+-mid)$")
_cache: dict[tuple[str, int], tuple[float, dict]] = {}
_lock = threading.Lock()
_last_request = 0.0


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self.has_next = False
        self._seen: set[str] = set()
        self._link: dict | None = None
        self._page = 0

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        a = dict(attrs)
        href = a.get("href") or ""
        m = _SLUG.match(href)
        if m and m.group(1) not in self._seen:
            self._link = {"slug": m.group(1), "title": (a.get("title") or "").strip(), "text": ""}
        elif "/search?" in href and "page=" in href:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            try:
                self.has_next = self.has_next or int(q["page"][0]) > self._page
            except (KeyError, ValueError):
                pass

    def handle_data(self, data):
        if self._link is not None:
            self._link["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            link, self._link = self._link, None
            name = link["title"] or " ".join(link["text"].split())
            if name:
                self._seen.add(link["slug"])
                self.results.append({"name": name, "url": f"https://bitmidi.com/{link['slug']}"})


def parse_search(html: str, page: int = 0) -> dict:
    """Extrai os resultados (nome + página de detalhe) e se existe próxima página."""
    p = _Parser()
    p._page = page
    p.feed(html)
    return {"results": p.results, "has_next": p.has_next}


def _download(url: str) -> str:
    global _last_request
    with _lock:  # serializa e espaça as requisições
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(2_000_000).decode("utf-8", "replace")


def search(query: str, page: int = 0) -> dict:
    query = " ".join(query.split())[:MAX_QUERY]
    if not query:
        raise ValueError("digite o que buscar")
    if page < 0:
        raise ValueError("página inválida")
    key = (query.lower(), page)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    params = {"q": query} | ({"page": page} if page else {})
    data = parse_search(_download(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"), page)
    data["has_next"] = data["has_next"] or len(data["results"]) >= PAGE_SIZE
    _cache[key] = (time.monotonic(), data)
    return data
