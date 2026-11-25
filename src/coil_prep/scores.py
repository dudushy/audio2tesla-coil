"""Partituras do Mutopia Project (https://www.mutopiaproject.org): busca e download do .mid.

O Mutopia publica partituras em domínio público ou Creative Commons, cada uma com um .mid
gerado da própria partitura (notas exatas, sem transcrição de áudio). O robots.txt dele libera
robôs e a licença permite baixar e usar; a licença de cada peça é mostrada ao usuário.
"""

import io
import re
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser

BASE = "https://www.mutopiaproject.org"
SEARCH_URL = BASE + "/cgibin/make-table.cgi"
FILES_URL = BASE + "/ftp/"
USER_AGENT = "coil-prep (uso pessoal; busca manual)"
TTL = 600.0  # cache em memória: o mesmo pedido não vai de novo ao site por 10 min
MIN_INTERVAL = 1.0  # no mínimo 1 s entre requisições ao site
MAX_QUERY = 100
PAGE_SIZE = 10
MAX_HTML = 2 * 1024 * 1024
MAX_FILE = 10 * 1024 * 1024  # .mid/.zip; também o limite de cada .mid dentro do zip

# só arquivos .mid ou o zip de .mid, em caminho relativo dentro de /ftp/
_FILE = re.compile(r"^(?:[\w-][\w.,()+-]*/)+[\w-][\w.,()+-]*(?:\.midi?|-mids\.zip)$")


def _featured(title: str, composer: str, file: str, part: int) -> dict:
    return {"title": title, "composer": composer, "file": file, "part": part}


# conferidas uma a uma: `part` é a trilha (trilha*16+canal) que carrega a melodia
FEATURED = [
    _featured(
        "Für Elise", "Beethoven", "BeethovenLv/WoO59/fur_Elise_WoO59/fur_Elise_WoO59.mid", 16
    ),
    _featured("Ode à Alegria", "Beethoven", "BeethovenLv/ode/ode.mid", 17),
    _featured(
        "Marcha Turca (Rondo alla turca)",
        "Mozart",
        "MozartWA/KV331/KV331_3_RondoAllaTurca/KV331_3_RondoAllaTurca.mid",
        16,
    ),
    _featured(
        "Eine kleine Nachtmusik (1º mov.)",
        "Mozart",
        "MozartWA/KV525/eine-kleine-nachtmusik-mvt1/eine-kleine-nachtmusik-mvt1.mid",
        17,
    ),
    _featured("Cânone em Ré", "Pachelbel", "PachelbelJ/CanonInD/CanonInD.mid", 17),
    _featured(
        "Tocata e Fuga em Ré menor", "Bach", "BachJS/BWV565/ToccataFugue/ToccataFugue.mid", 16
    ),
    _featured(
        "Danúbio Azul (tema)", "J. Strauss Jr.", "StraussJJ/O314/blue_danube/blue_danube.mid", 16
    ),
    _featured(
        "Na Gruta do Rei da Montanha",
        "Grieg",
        "GriegE/O46/Dans_l_antre_du_roi_de_la_montagne/Dans_l_antre_du_roi_de_la_montagne.mid",
        16,
    ),
    _featured("The Entertainer", "Joplin", "JoplinS/entertainer/entertainer.mid", 16),
    _featured("Canção de Ninar", "Brahms", "BrahmsJ/LullabyBrahms-C/LullabyBrahms-C.mid", 16),
    _featured(
        "Greensleeves", "Tradicional", "Traditional/greensleeves_guitar/greensleeves_guitar.mid", 16
    ),
    _featured(
        "Amazing Grace", "Tradicional", "Traditional/amazing-mutopia/amazing-mutopia.mid", 16
    ),
]


class _Parser(HTMLParser):
    """Cada peça é uma <table class="... result-table"> com as células em ordem fixa."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pieces: list[dict] = []
        self.has_next = False
        self._piece: dict | None = None
        self._cell: list[str] | None = None
        self._next_href = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and "result-table" in (a.get("class") or ""):
            self._piece = {"cells": [], "links": []}
        elif tag == "td" and self._piece is not None:
            self._cell = []
        elif tag == "a":
            href = a.get("href") or ""
            if self._piece is not None:
                self._piece["links"].append(href)
            self._next_href = "startat=" in href

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._next_href and data.strip().startswith("Next"):
            self.has_next = True

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._piece["cells"].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "a":
            self._next_href = False
        elif tag == "table" and self._piece is not None:
            self.pieces.append(self._piece)
            self._piece = None


def parse_search(html: str) -> dict:
    """Peças com .mid (título, compositor, instrumento, licença, arquivo) e se há próxima página."""
    p = _Parser()
    p.feed(html)
    results = []
    for piece in p.pieces:
        cells = piece["cells"] + [""] * 12
        files = [
            h.split("/ftp/", 1)[1]
            for h in piece["links"]
            if "/ftp/" in h and _FILE.match(h.split("/ftp/", 1)[1])
        ]
        if not files:
            continue
        info = next((h for h in piece["links"] if h.startswith("piece-info.cgi?id=")), "")
        results.append(
            {
                "title": cells[0],
                "composer": cells[1].removeprefix("by "),
                "opus": cells[2],
                "instrument": cells[4].removeprefix("for "),
                "license": cells[9],
                "url": f"{BASE}/cgibin/{info}" if info else BASE,
                "file": files[0],
            }
        )
    return {"results": results, "has_next": p.has_next}


_lock = threading.Lock()
_last_request = 0.0
_searches: dict[tuple[str, int], tuple[float, dict]] = {}
_files: dict[str, tuple[float, bytes]] = {}


def _download(url: str, limit: int) -> bytes:
    global _last_request
    with _lock:  # serializa e espaça as requisições
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as res:
            data = res.read(limit + 1)
    if len(data) > limit:
        raise ValueError("arquivo grande demais")
    return data


def _cached(cache: dict, key, make):
    hit = cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    value = make()
    while len(cache) >= 50:
        cache.pop(next(iter(cache)))
    cache[key] = (time.monotonic(), value)
    return value


def search(query: str, start: int = 0) -> dict:
    query = " ".join(query.split())
    if not query or len(query) > MAX_QUERY:
        raise ValueError("digite o que buscar (até 100 caracteres)")
    if start < 0:
        raise ValueError("página inválida")
    params = urllib.parse.urlencode({"searchingfor": query, "startat": start})

    def make():
        html = _download(f"{SEARCH_URL}?{params}", MAX_HTML).decode("utf-8", "replace")
        return parse_search(html) | {"page_size": PAGE_SIZE}

    return _cached(_searches, (query.lower(), start), make)


def _fetch_file(file: str) -> bytes:
    if not _FILE.match(file):
        raise ValueError("arquivo de partitura inválido")
    url = FILES_URL + urllib.parse.quote(file)
    return _cached(_files, file, lambda: _download(url, MAX_FILE))


def _zip(data: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("o .zip da partitura está corrompido") from None


def _members(z: zipfile.ZipFile) -> list[str]:
    names = (
        i.filename
        for i in z.infolist()
        if i.filename.lower().endswith((".mid", ".midi")) and i.file_size <= MAX_FILE
    )
    return sorted(
        names, key=lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n)]
    )


def movements(file: str) -> list[str]:
    """Nomes dos .mid (movimentos) dentro de um -mids.zip."""
    if not file.endswith(".zip"):
        raise ValueError("só o .zip tem movimentos")
    with _zip(_fetch_file(file)) as z:
        return _members(z)


def midi_bytes(file: str, member: str | None = None) -> bytes:
    """O .mid pedido; para .zip, o `member` (obrigatório se houver mais de um)."""
    data = _fetch_file(file)
    if file.endswith(".zip"):
        with _zip(data) as z:
            names = _members(z)
            if member is None and len(names) == 1:
                member = names[0]
            if member not in names:
                raise ValueError("escolha um dos movimentos da peça")
            data = z.read(member)  # só leitura em memória: nada é extraído para o disco
    if not data.startswith(b"MThd"):
        raise ValueError("o arquivo baixado não é um MIDI")
    return data
