import time

import pytest
from fastapi.testclient import TestClient

from coil_prep import library, midiworld, noteblockworld
from coil_prep.web.app import create_app
from tests.test_web import midi_bytes, wait_done

MIDIWORLD_PAGE = """<ul>
<li>
Dancing Queen (ABBA) - <a href="https://www.midiworld.com/download/29" target="_blank">download</a>
<div id="audio-player29">x</div></li>
<li>
Bohemian Rhapsody &amp; more (Queen) - <a href="https://www.midiworld.com/download/6745" target="_blank">download</a>
</li><li>Sem artista - <a href="https://www.midiworld.com/download/7">download</a></li>
</ul><ul class="listing"><li><a href="https://www.midiworld.com/search/2/?q=queen" class="nomark">next »</a></li></ul>"""


def test_midiworld_parse_splits_title_and_artist():
    r = midiworld.parse_search(MIDIWORLD_PAGE)
    assert r["has_next"]
    assert r["results"] == [
        {"id": 29, "title": "Dancing Queen", "artist": "ABBA"},
        {"id": 6745, "title": "Bohemian Rhapsody & more", "artist": "Queen"},
        {"id": 7, "title": "Sem artista", "artist": ""},
    ]


def test_midiworld_download_checks_id_and_content(monkeypatch):
    monkeypatch.setattr(midiworld, "_get", lambda url, limit: b"<html>erro</html>")
    midiworld._files.clear()
    with pytest.raises(ValueError):
        midiworld.download(-1)
    with pytest.raises(ValueError, match="não é um MIDI"):
        midiworld.download(5)


def test_noteblockworld_lists_public_songs_with_link(monkeypatch):
    page = {
        "total": 2,
        "content": [
            {"publicId": "abc", "title": "Zelda", "originalAuthor": "Nintendo", "duration": 61.4,
             "uploader": {"username": "fã"}, "visibility": "public"},
            {"publicId": "zzz", "title": "privada", "visibility": "private"},
        ],
    }  # fmt: skip
    monkeypatch.setattr(noteblockworld, "_get", lambda url: page)
    noteblockworld._cache.clear()
    r = noteblockworld.search("zelda")
    assert [s["url"] for s in r["results"]] == ["https://noteblock.world/song/abc"]
    assert r["results"][0]["author"] == "Nintendo" and not r["has_next"]


def test_unified_search_keeps_working_when_one_source_fails(monkeypatch):
    def ok(q, page):
        return {"results": [{"title": q, "subtitle": "", "detail": "", "action": {"type": "link", "url": "u"}}],
                "total": 1, "has_next": False}  # fmt: skip

    def broken(q, page):
        raise OSError("fora do ar")

    def slow(q, page):
        time.sleep(0.5)
        return ok(q, page)

    providers = dict.fromkeys(library.PROVIDERS, ok) | {"midiworld": broken, "bitmidi": slow}
    monkeypatch.setattr(library, "PROVIDERS", providers)
    monkeypatch.setattr(library, "TIMEOUT", 0.2)
    r = library.search("  zelda  ")
    by = {s["id"]: s for s in r["sources"]}
    assert r["query"] == "zelda" and set(by) == set(library.SOURCES)
    assert by["lakh"]["results"][0]["title"] == "zelda" and by["lakh"]["kind"] == "MIDI"
    assert by["midiworld"]["error"] and not by["midiworld"]["results"]
    assert "demorou" in by["bitmidi"]["error"]
    one = library.search("zelda", "lakh", 2)
    assert [s["id"] for s in one["sources"]] == ["lakh"] and one["sources"][0]["page"] == 2
    with pytest.raises(ValueError):
        library.search("x", "naoexiste")
    with pytest.raises(ValueError):
        library.search("   ")


def test_featured_classics_come_first_in_mutopia(monkeypatch):
    from coil_prep import scores

    monkeypatch.setattr(scores, "search", lambda q, start=0: {"results": [], "has_next": False})
    r = library._mutopia("elise", 0)
    assert r["results"][0]["title"] == "Für Elise"
    assert r["results"][0]["action"]["body"]["part"] == 16


def test_midiworld_generate_endpoint(monkeypatch, tmp_path):
    data = midi_bytes(tmp_path)
    monkeypatch.setattr(midiworld, "_get", lambda url, limit: data)
    midiworld._files.clear()
    client = TestClient(create_app(tmp_path / "data"))
    r = client.post(
        "/api/midiworld/generate", json={"id": 29, "title": "Dancing Queen", "artist": "ABBA"}
    )
    assert r.status_code == 200, r.text
    job = r.json()["jobs"][0]
    assert job["source"].startswith("ABBA_-_Dancing_Queen-")
    assert wait_done(client, job["id"])["status"] == "done"
    assert client.get("/api/library/search?q=").status_code == 422
