"""Músicas populares em MIDI: subconjunto "clean" do Lakh MIDI Dataset (Colin Raffel).

~17 mil arquivos .mid organizados por artista e título (pop e rock, sobretudo até ~2010:
Beatles, Queen, ABBA, Metallica...), com licença CC-BY 4.0 para a coleção
(https://colinraffel.com/projects/lmd/). O pacote oficial tem 234 MB; aqui se usa a cópia no
GitHub (https://github.com/ryohey/lakh-midi), que tem um índice (files.json, ~0,5 MB) e permite
baixar um arquivo por vez. As músicas em si continuam sendo obras com direitos autorais: uso
pessoal.
"""

import json
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

INDEX_URL = "https://raw.githubusercontent.com/ryohey/lakh-midi/main/files.json"
RAW_URL = "https://raw.githubusercontent.com/ryohey/lakh-midi/main/clean_midi/"
ATTRIBUTION = "Lakh MIDI Dataset (Colin Raffel), CC-BY 4.0 — https://colinraffel.com/projects/lmd/"
USER_AGENT = "coil-prep (uso pessoal)"
INDEX_TTL = 24 * 3600.0
PAGE_SIZE = 20
MAX_QUERY = 100
MAX_FILE = 5 * 1024 * 1024
MIN_INTERVAL = 0.5

_lock = threading.Lock()
_last = 0.0
_index: tuple[float, list[dict]] = (0.0, [])
_files: dict[str, bytes] = {}
_VERSION = re.compile(r"\.(\d+)$")  # "Song.1.mid" = 2ª versão do mesmo título


def _get(url: str, limit: int) -> bytes:
    global _last
    with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as res:
        data = res.read(limit + 1)
    if len(data) > limit:
        raise ValueError("arquivo grande demais")
    return data


def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in norm if not unicodedata.combining(c))


def index() -> list[dict]:
    """[{path, artist, title, version, key}] de todas as músicas (índice guardado por 24 h)."""
    global _index
    stamp, cached = _index
    if cached and time.monotonic() - stamp < INDEX_TTL:
        return cached
    raw = json.loads(_get(INDEX_URL, 20 * 1024 * 1024))
    out = []
    for artist, entry in raw.items():
        files = entry.get("files", []) if isinstance(entry, dict) else []
        for name in files:
            if not name.lower().endswith((".mid", ".midi")) or "/" in name or "/" in artist:
                continue
            stem = name.rsplit(".", 1)[0]
            m = _VERSION.search(stem)
            title = stem[: m.start()] if m else stem
            out.append(
                {
                    "path": f"{artist}/{name}",
                    "artist": artist,
                    "title": title,
                    "version": int(m.group(1)) + 1 if m else 1,
                    "key": _fold(f"{artist} {title}"),
                }
            )
    out.sort(key=lambda s: (s["artist"].lower(), s["title"].lower(), s["version"]))
    if out:
        _index = (time.monotonic(), out)
    return out


def search(query: str, page: int = 0) -> dict:
    """Todas as palavras precisam aparecer em "artista título" (sem acento/maiúsculas)."""
    query = " ".join(query.split())
    if not query or len(query) > MAX_QUERY:
        raise ValueError("digite o artista ou a música (até 100 caracteres)")
    if page < 0:
        raise ValueError("página inválida")
    words = _fold(query).split()
    found = [s for s in index() if all(w in s["key"] for w in words)]
    start = page * PAGE_SIZE
    return {
        "results": [
            {k: s[k] for k in ("path", "artist", "title", "version")}
            for s in found[start : start + PAGE_SIZE]
        ],
        "total": len(found),
        "has_next": start + PAGE_SIZE < len(found),
        "attribution": ATTRIBUTION,
    }


def download(path: str) -> bytes:
    """Bytes do .mid `path` ("Artista/Título.mid"), que precisa estar no índice."""
    if path not in {s["path"] for s in index()}:
        raise ValueError("música não encontrada no acervo")
    if path not in _files:
        data = _get(RAW_URL + urllib.parse.quote(path), MAX_FILE)
        if not data.startswith(b"MThd"):
            raise ValueError("o arquivo baixado não é um MIDI")
        if len(_files) >= 30:
            _files.pop(next(iter(_files)))
        _files[path] = data
    return _files[path]
