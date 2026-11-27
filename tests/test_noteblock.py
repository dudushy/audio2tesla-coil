import io

import numpy as np
import pynbs
import pytest
from fastapi.testclient import TestClient

from coil_prep.calibrate import write_mono_wav
from coil_prep.midi import midi_to_hz
from coil_prep.noteblock import (
    MC_HIGH,
    MC_LOW,
    fft_notes,
    noteblock_style,
    read_nbs_parts,
    write_nbs,
)
from coil_prep.web.app import create_app
from coil_prep.web.jobs import JobManager


def make_nbs(path, notes, tempo=10.0):
    """notes: (tique, camada, instrumento, tecla)."""
    song = pynbs.new_file(tempo=tempo)
    song.layers = [pynbs.Layer(i, f"L{i}") for i in range(4)]
    song.notes = [pynbs.Note(t, layer, inst, key) for t, layer, inst, key in notes]
    song.save(str(path))
    return path


def test_read_nbs_layers_become_parts_with_instrument_octaves(tmp_path):
    path = make_nbs(
        tmp_path / "a.nbs",
        [(0, 0, 0, 45), (5, 0, 0, 47), (0, 1, 1, 45), (0, 2, 2, 40)],  # harpa, contrabaixo, bumbo
        tempo=5,
    )
    parts = {p.name: p for p in read_nbs_parts(path)}
    harp = parts["L0 (harpa)"].notes
    assert [(n.start, n.pitch) for n in harp] == [(0.0, 66), (1.0, 68)]  # tecla 45 = F#4
    assert harp[0].end == pytest.approx(0.5)  # nota de note block não sustenta
    assert parts["L1 (contrabaixo)"].notes[0].pitch == 66 - 24
    assert not any("L2" in name for name in parts)  # percussão fica de fora


def test_write_nbs_roundtrip_folds_into_minecraft_range(tmp_path):
    segs = [(0.0, 0.4, (60,)), (0.42, 0.6, (90.3,)), (0.42, 0.6, (40,))]
    write_nbs(tmp_path / "o.nbs", segs, "teste")
    song = pynbs.read(str(tmp_path / "o.nbs"))
    keys = [(n.tick, n.layer, n.key + 21, n.pitch) for n in song.notes]
    # 40 -> 64 (E4); 90,3 -> 66,3 (78,3 passaria de F#5): tecla 66 + 30 centésimos
    assert keys == [(0, 0, 60, 0), (4, 0, 64, 0), (4, 1, 66, 30)]
    assert song.header.tempo == 10 and len(song.layers) == 2


def test_noteblock_style_strikes_on_ticks_in_25_notes():
    segs = [(0.03, 2.0, (40,)), (0.31, 0.5, (91,)), (0.33, 0.6, (60,))]
    out = noteblock_style(segs)
    assert [round(a, 2) for a, _, _ in out] == [0.0, 0.3]
    assert all(MC_LOW <= p <= MC_HIGH for _, _, (p,) in out)
    assert out[0][1] <= 0.3  # batida curta, não sustenta 2 s
    assert out[1][2] == (79 - 12,)  # 91 (G7) desce para G5; a 2ª nota do mesmo tique sai


def test_fft_notes_finds_fundamental_not_harmonics(tmp_path):
    rate = 22050
    parts = []
    for p in (57, 60, 64):
        t = np.arange(int(rate * 0.5)) / rate
        f = midi_to_hz(p)
        parts.append(
            sum(a * np.sin(2 * np.pi * k * f * t) for k, a in ((1, 0.4), (2, 0.9), (3, 0.5)))
        )
    write_mono_wav(tmp_path / "t.wav", 0.3 * np.concatenate(parts), rate)
    got = [p[0] for _, _, p in fft_notes(tmp_path / "t.wav")]
    assert got == [57, 60, 64]


def test_nbs_upload_methods_and_exports(tmp_path):
    data = tmp_path / "data"
    client = TestClient(create_app(data))
    nbs = make_nbs(
        tmp_path / "s.nbs", [(i * 2, 0, 0, 33 + k) for i, k in enumerate([0, 2, 4, 5, 7])]
    )
    r = client.post(
        "/api/batches",
        data={"methods": "melody,noteblock,exact"},  # "exact" não vale para notas: ignorado
        files={"file": ("musica.nbs", io.BytesIO(nbs.read_bytes()))},
    )
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert [j["variant"] for j in jobs] == ["trilhas automáticas", "estilo Note Block"]
    import time

    for j in jobs:
        for _ in range(100):
            st = client.get(f"/api/jobs/{j['id']}").json()
            if st["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert st["status"] == "done", st
    assert jobs[0]["id"] != jobs[1]["id"]
    out = client.get(f"/api/jobs/{jobs[1]['id']}/nbs")
    assert out.status_code == 200 and "attachment" in out.headers["content-disposition"]
    (tmp_path / "back.nbs").write_bytes(out.content)
    assert len(pynbs.read(str(tmp_path / "back.nbs")).notes) == 5
    live = client.post(
        "/api/live/export",
        json={"format": "nbs", "notes": [{"start": 0, "end": 0.3, "pitch": 60}]},
    )
    assert live.status_code == 200 and "soundpad.nbs" in live.headers["content-disposition"]


def test_audio_batch_is_stems_times_methods(tmp_path):
    seen = []

    def fake(source, data_dir, profile, stem, progress=None, **kw):
        seen.append((stem, kw.get("method", "melody")))
        raise ValueError("fim")

    data = tmp_path / "data"
    client = TestClient(create_app(data, JobManager(data, fake)))
    r = client.post(
        "/api/batches",
        data={"url": "https://a/b", "stems": "other,mix", "methods": "melody,noteblock"},
    )
    assert [j["variant"] for j in r.json()["jobs"]] == [
        "other · transcrição",
        "other · estilo Note Block",
        "mix · transcrição",
        "mix · estilo Note Block",
    ]
    bad = client.post("/api/batches", data={"url": "https://a/b", "methods": "magica"})
    assert bad.status_code == 422
