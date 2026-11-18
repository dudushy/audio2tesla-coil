import pytest
from fastapi.testclient import TestClient

from coil_prep import bitmidi
from coil_prep.web.app import create_app

HTML = """
<nav><a href="/random">Random MIDI</a><a href="/about">About</a></nav>
<article><a href="https://bitmidi.com/mario-bros-super-mario-bros-theme-mid"
  title="Mario Bros. - Super Mario Bros. Theme.mid"><h2>Mario Bros. - Super Mario Bros. Theme.mid</h2></a></article>
<article><a href="/mario-kart-64-mario-raceway-mid"><h2>Mario Kart 64 &amp; Raceway.mid</h2></a></article>
<article><a href="/mario-kart-64-mario-raceway-mid" title="dup">dup</a></article>
<a href="/search?q=mario&page=1">Next</a>
"""


@pytest.fixture(autouse=True)
def clean_cache():
    bitmidi._cache.clear()
    yield
    bitmidi._cache.clear()


def test_parse_search():
    data = bitmidi.parse_search(HTML)
    assert [r["url"] for r in data["results"]] == [
        "https://bitmidi.com/mario-bros-super-mario-bros-theme-mid",
        "https://bitmidi.com/mario-kart-64-mario-raceway-mid",
    ]
    assert data["results"][1]["name"] == "Mario Kart 64 & Raceway.mid"
    assert data["has_next"] is True


def test_parse_last_page_has_no_next():
    assert bitmidi.parse_search(HTML.replace("page=1", "page=0"), page=0)["has_next"] is False


def test_search_validates_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(bitmidi, "_download", lambda url: calls.append(url) or HTML)
    with pytest.raises(ValueError):
        bitmidi.search("   ")
    first = bitmidi.search("Mario  ", 0)
    assert bitmidi.search("mario", 0) is first
    assert len(calls) == 1 and calls[0] == "https://bitmidi.com/search?q=Mario"
    bitmidi.search("mario", 2)
    assert calls[1].endswith("q=mario&page=2")


def test_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(bitmidi, "_download", lambda url: HTML)
    client = TestClient(create_app(tmp_path / "data"))
    r = client.get("/api/bitmidi/search", params={"q": "mario"})
    assert r.status_code == 200 and len(r.json()["results"]) == 2
    assert client.get("/api/bitmidi/search", params={"q": " "}).status_code == 422

    def boom(url):
        raise OSError("sem rede")

    monkeypatch.setattr(bitmidi, "_download", boom)
    assert client.get("/api/bitmidi/search", params={"q": "zelda"}).status_code == 502
