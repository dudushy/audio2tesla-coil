"""Note Block World (https://noteblock.world): site oficial de músicas do Open Note Block Studio.

Busca pela API pública (https://api.noteblock.world/v1/song). Baixar o .nbs exige estar logado
no site (a API responde 401 sem login), então o app só mostra os resultados com o link da
página: a pessoa baixa lá e envia o arquivo em Gerar. Cada música tem a licença escolhida por
quem enviou (ex.: CC BY-SA) e a indicação se o autor permite baixar.
"""

import json
import threading
import time
import urllib.parse
import urllib.request

API = "https://api.noteblock.world/v1/song"
SITE = "https://noteblock.world/song/"
USER_AGENT = "coil-prep (uso pessoal)"
PAGE_SIZE = 20
TTL = 600.0
MIN_INTERVAL = 0.5
LICENSES = {
    "cc_by_sa": "CC BY-SA 4.0",
    "standard": "todos os direitos reservados",
}

_lock = threading.Lock()
_last = 0.0
_cache: dict[tuple[str, int], tuple[float, dict]] = {}


def _get(url: str) -> dict:
    global _last
    with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read(2 * 1024 * 1024))


def search(query: str, page: int = 0) -> dict:
    query = " ".join(query.split())
    if not query or len(query) > 100:
        raise ValueError("digite o que buscar (até 100 caracteres)")
    key = (query.lower(), page)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    q = urllib.parse.urlencode({"q": query, "page": page + 1, "limit": PAGE_SIZE})
    data = _get(f"{API}?{q}")
    results = []
    for s in data.get("content", []):
        if s.get("visibility", "public") != "public" or not s.get("publicId"):
            continue
        results.append(
            {
                "id": s["publicId"],
                "title": s.get("title") or "(sem título)",
                "author": s.get("originalAuthor") or "",
                "uploader": (s.get("uploader") or {}).get("username", ""),
                "seconds": s.get("duration"),
                "url": SITE + urllib.parse.quote(s["publicId"]),
            }
        )
    total = int(data.get("total") or 0)
    out = {"results": results, "total": total, "has_next": (page + 1) * PAGE_SIZE < total}
    if len(_cache) >= 100:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), out)
    return out
