import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from coil_prep import lakh
from coil_prep.web.app import create_app, pretty_title
from tests.test_web import midi_bytes, wait_done


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    index = {
        "Queen": {
            "files": ["Bohemian Rhapsody.mid", "Bohemian Rhapsody.1.mid", "Killer Queen.mid"]
        },
        "Beyoncé": {"files": ["Halo.mid", "notas.txt"]},
        "../x": {"files": ["y.mid"]},
        "files": [],
    }
    data = midi_bytes(tmp_path)
    calls = []

    def fake(url, limit):
        calls.append(url)
        return json.dumps(index).encode() if url == lakh.INDEX_URL else data

    monkeypatch.setattr(lakh, "_get", fake)
    monkeypatch.setattr(lakh, "_index", (0.0, []))
    lakh._files.clear()
    return calls


def test_search_by_artist_and_title_without_accents(catalog):
    r = lakh.search("queen bohemian")
    assert [(s["title"], s["version"]) for s in r["results"]] == [
        ("Bohemian Rhapsody", 1),
        ("Bohemian Rhapsody", 2),
    ]
    assert lakh.search("beyonce")["results"][0]["path"] == "Beyoncé/Halo.mid"
    assert lakh.search("queen")["total"] == 3
    assert "CC-BY" in r["attribution"]
    with pytest.raises(ValueError):
        lakh.search("   ")
    lakh.search("halo")
    assert catalog.count(lakh.INDEX_URL) == 1  # índice guardado


def test_only_indexed_files_are_downloaded(catalog):
    with pytest.raises(ValueError):
        lakh.download("../x/y.mid")
    with pytest.raises(ValueError):
        lakh.download("Queen/Inexistente.mid")
    assert lakh.download("Queen/Killer Queen.mid")[:4] == b"MThd"
    assert catalog[-1].endswith("clean_midi/Queen/Killer%20Queen.mid")


def test_generate_from_catalog_names_the_downloads(catalog, tmp_path):
    client = TestClient(create_app(tmp_path / "data"))
    r = client.post("/api/lakh/generate", json={"path": "Queen/Bohemian Rhapsody.1.mid"})
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert [j["variant"] for j in jobs] == ["trilhas automáticas", "todas as trilhas"]
    assert wait_done(client, jobs[0]["id"])["status"] == "done"
    cd = unquote(client.get(f"/api/jobs/{jobs[0]['id']}/midi").headers["content-disposition"])
    assert cd.endswith("Queen - Bohemian Rhapsody - versão 2 - trilhas automáticas.mid")
    assert client.post("/api/lakh/generate", json={"path": "nada.mid"}).status_code == 422


@pytest.mark.parametrize(
    "stem,variant,expected",
    [
        (
            "Mr._Brightside-1a2b3c4d.mix.t-1",
            "todas as trilhas",
            "Mr. Brightside - todas as trilhas",
        ),
        ("gravacao-2026-11-26-80bda659.rec.mix.exact", "", "gravacao-2026-11-26"),
        ("Song.other.noteblock", "x", "Song - x"),
    ],
)
def test_pretty_title_keeps_dots_in_names(stem, variant, expected):
    job = SimpleNamespace(
        result=SimpleNamespace(wav=Path(stem + ".wav")), variant=variant, source=""
    )
    assert pretty_title(job) == expected
