import json

import pytest
from fastapi.testclient import TestClient

from coil_prep import nbssongs
from coil_prep.midi import MidiPart, Note, combine_parts
from coil_prep.web.app import create_app


@pytest.fixture
def library(monkeypatch, tmp_path):
    import pynbs

    song = pynbs.new_file(tempo=10)
    song.notes = [pynbs.Note(i * 3, 0, 0, 33 + k) for i, k in enumerate([0, 2, 4, 5, 7, 9])]
    path = tmp_path / "s.nbs"
    song.save(str(path))
    tree = {"tree": [{"path": n, "type": "blob"} for n in
                     ("Pokémon Center.nbs", "Zelda - Lost Woods.nbs", "readme.md", "sub/x.nbs")]}  # fmt: skip
    calls = []

    def fake(url, limit, accept="*/*"):
        calls.append(url)
        if url == nbssongs.TREE_URL:
            return json.dumps(tree).encode()
        if url == nbssongs.DURATIONS_URL:
            return json.dumps({"Zelda - Lost Woods.nbs": 61.5}).encode()
        return path.read_bytes()

    monkeypatch.setattr(nbssongs, "_get", fake)
    monkeypatch.setattr(nbssongs, "_list", (0.0, []))
    nbssongs._files.clear()
    return calls


def test_search_lists_only_top_level_nbs_and_ignores_accents(library):
    assert nbssongs.search("")["total"] == 2
    found = nbssongs.search("POKEMON center")["results"]
    assert [s["title"] for s in found] == ["Pokémon Center"]
    assert nbssongs.search("lost woods")["results"][0]["seconds"] == 61.5
    nbssongs.search("zelda")
    assert library.count(nbssongs.TREE_URL) == 1  # a lista fica guardada


def test_only_listed_songs_can_be_downloaded(library):
    with pytest.raises(ValueError):
        nbssongs.download("../../etc/passwd")
    assert nbssongs.download("Zelda - Lost Woods.nbs")[:2] == b"\x00\x00"


def test_generate_from_library_and_pretty_download_names(library, tmp_path):
    import time

    client = TestClient(create_app(tmp_path / "data"))
    r = client.post("/api/nbs-songs/generate", json={"name": "Zelda - Lost Woods.nbs"})
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert [j["variant"] for j in jobs] == [
        "trilhas automáticas",
        "estilo Note Block",
        "todas as trilhas",
    ]
    job = jobs[1]
    for _ in range(100):
        if client.get(f"/api/jobs/{job['id']}").json()["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    from urllib.parse import unquote

    def name(url):
        return unquote(client.get(url).headers["content-disposition"])

    assert name(f"/api/jobs/{job['id']}/nbs").endswith("Zelda - Lost Woods - estilo Note Block.nbs")
    wav = name(f"/api/jobs/{job['id']}/audio/result?download=true")
    assert wav.endswith("Zelda - Lost Woods - estilo Note Block.wav")
    assert (
        client.post("/api/nbs-songs/generate", json={"name": "nao existe.nbs"}).status_code == 422
    )


def test_quieter_second_melody_fills_the_lead_pauses():
    """Regressão (A Little Piece of Heaven): a 2ª voz toca pouco, mas é melodia; o baixo só entra
    em pausa longa que nenhuma melodia cobre."""
    lead = MidiPart(0, "Lead 1", [Note(t, t + 0.4, 72) for t in (0, 0.5, 1, 1.5)])
    lead.notes += [Note(t, t + 0.4, 74) for t in (6, 6.5, 7, 7.5, 12, 12.5)]
    answer = MidiPart(16, "Lead 2", [Note(t, t + 0.4, 76) for t in (2.5, 3, 3.5)])
    bass = MidiPart(33, "Bass", [Note(t, t + 0.9, 40 + t % 2) for t in range(14)])
    notes, used = combine_parts([lead, answer, bass])
    assert 16 in used  # antes ficava de fora: tocava menos que 40% do Lead 1
    assert 33 in used  # baixo entra na pausa longa 8-12 s
    assert not any(n.part == 33 and n.start < 6 for n in notes)  # mas não onde a 2ª voz já cobriu
