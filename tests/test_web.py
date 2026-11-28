import time

import pytest
from fastapi.testclient import TestClient
from test_midi import make_mid

from coil_prep.web.app import create_app, safe_name


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "data"))


def wait_done(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    pytest.fail("job não terminou")


def midi_bytes(tmp_path):
    path = tmp_path / "a.mid"
    make_mid(path, [(0, 60, 0, 2), (0, 64, 2, 2)])
    return path.read_bytes()


def test_index_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "coil-prep" in r.text


def test_upload_midi_generates_playable_wav(client, tmp_path):
    r = client.post(
        "/api/jobs",
        data={"stem": "mix"},
        files={"file": ("../../Minha Música.mid", midi_bytes(tmp_path), "audio/midi")},
    )
    assert r.status_code == 200
    job = wait_done(client, r.json()["id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["ok"] and not res["has_original"] and res["notes"] == 2
    audio = client.get(f"/api/jobs/{job['id']}/audio/result")
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    assert client.get(f"/api/jobs/{job['id']}/audio/original").status_code == 404
    # nome sanitizado: nada fora de data/
    inputs = list((tmp_path / "data" / "input").iterdir())
    assert len(inputs) == 1 and ".." not in inputs[0].name and "/" not in inputs[0].name


def test_download_flag_sets_attachment(client, tmp_path):
    r = client.post(
        "/api/jobs", data={"stem": "mix"}, files={"file": ("a.mid", midi_bytes(tmp_path))}
    )
    job = wait_done(client, r.json()["id"])
    audio = client.get(f"/api/jobs/{job['id']}/audio/result?download=true")
    assert "attachment" in audio.headers["content-disposition"]


def test_empty_midi_reports_error(client, tmp_path):
    make_mid(tmp_path / "e.mid", [(9, 36, 0, 1)])  # só percussão
    r = client.post(
        "/api/jobs",
        data={"stem": "mix"},
        files={"file": ("e.mid", (tmp_path / "e.mid").read_bytes())},
    )
    job = wait_done(client, r.json()["id"])
    assert job["status"] == "error" and "nenhuma nota" in job["error"]


@pytest.mark.parametrize(
    "data,files",
    [
        ({}, None),  # nem link nem arquivo
        ({"url": "ftp://x/y"}, None),
        ({"url": "https://a/b", "stem": "bass"}, None),
        ({"url": "file:///etc/passwd"}, None),
    ],
)
def test_invalid_requests_are_rejected(client, data, files):
    assert client.post("/api/jobs", data=data, files=files).status_code == 422


def test_rejects_unknown_extension_and_both_sources(client):
    r = client.post("/api/jobs", files={"file": ("x.exe", b"MZ")})
    assert r.status_code == 422
    r = client.post("/api/jobs", data={"url": "https://a/b"}, files={"file": ("x.mp3", b"x")})
    assert r.status_code == 422


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/naoexiste").status_code == 404
    assert client.get("/api/jobs/naoexiste/audio/result").status_code == 404


def test_safe_name():
    assert safe_name("../../etc/Música X!.MP3") == ("Música_X", ".mp3")
    assert safe_name("") == ("audio", "")


def generate(client, tmp_path):
    r = client.post(
        "/api/jobs", data={"stem": "mix"}, files={"file": ("a.mid", midi_bytes(tmp_path))}
    )
    return wait_done(client, r.json()["id"])


def test_feedback_is_saved_once_and_drives_suggestion(client, tmp_path):
    assert client.get("/api/state").json()["suggestion"] == "other"
    job = generate(client, tmp_path)
    r = client.post(f"/api/jobs/{job['id']}/feedback", json={"rating": 1})
    assert r.status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/feedback", json={"rating": 1}).status_code == 409
    st = client.get("/api/state").json()
    assert st["ratings"] == 1 and st["suggestion"] == "mix"  # único stem bem avaliado
    line = (tmp_path / "data" / "feedback.jsonl").read_text().splitlines()
    assert len(line) == 1 and '"rating": 1' in line[0]


def test_bad_feedback_stores_tag_and_validates_input(client, tmp_path):
    job = generate(client, tmp_path)
    url = f"/api/jobs/{job['id']}/feedback"
    assert client.post(url, json={"rating": 5}).status_code == 422
    assert client.post(url, json={"rating": -1, "tag": "inventada"}).status_code == 422
    assert client.post(url, json={"rating": -1, "tag": "picotado"}).status_code == 200
    assert '"tag": "picotado"' in (tmp_path / "data" / "feedback.jsonl").read_text()


def test_feedback_unknown_job_is_404(client):
    assert client.post("/api/jobs/x/feedback", json={"rating": 1}).status_code == 404


def test_calibration_roundtrip_and_validation(client, tmp_path):
    assert client.get("/api/state").json()["profile"] is None
    r = client.put("/api/profile", json={"min_hz": 120, "max_hz": 1000})
    assert r.status_code == 200
    assert client.get("/api/state").json()["profile"] == {"min_hz": 120.0, "max_hz": 1000.0}
    assert (
        client.put("/api/profile", json={"min_hz": 500, "max_hz": 600}).status_code == 422
    )  # < 1 oitava
    assert client.put("/api/profile", json={"min_hz": 900, "max_hz": 300}).status_code == 422
    # a próxima geração usa a faixa salva
    low = tmp_path / "low.mid"
    make_mid(low, [(0, 36, 0, 2)])  # C2 65 Hz, abaixo de 120 Hz
    r = client.post(
        "/api/jobs", data={"stem": "mix"}, files={"file": ("low.mid", low.read_bytes())}
    )
    job = wait_done(client, r.json()["id"])
    assert job["result"]["min_hz"] == 120.0 and job["result"]["max_hz"] == 1000.0


def test_calibration_by_heard_notes(client, tmp_path):
    heard = [43, 45, 48, 55, 60, 67]
    r = client.put("/api/profile", json={"notes": heard})
    assert r.status_code == 200, r.text
    state = client.get("/api/state").json()
    assert state["profile"]["notes"] == heard and state["coil_notes"] == [24, 119]
    assert client.put("/api/profile", json={"notes": [60, 62]}).status_code == 422
    assert client.put("/api/profile", json={}).status_code == 422
    # exportação "ajustada": C6 (84) cai na oitava de C ouvida mais próxima, C4 (60)
    import mido

    body = {"format": "mid", "notes": [{"start": 0, "end": 0.5, "pitch": 84}]}
    r = client.post("/api/live/export", json=body)
    path = tmp_path / "e.mid"
    path.write_bytes(r.content)
    assert [m.note for m in mido.MidiFile(str(path)) if m.type == "note_on"] == [60]


def test_score_from_mutopia_becomes_a_batch(client, tmp_path, monkeypatch):
    from coil_prep import scores

    data = midi_bytes(tmp_path)
    monkeypatch.setattr(scores, "_download", lambda url, limit: data)
    scores._files.clear()
    r = client.post(
        "/api/scores/generate",
        json={"file": "A/b/c.mid", "title": "Für Elise / teste", "part": -1},
    )
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert [j["variant"] for j in jobs] == ["melodia principal", "trilhas automáticas"]
    assert jobs[0]["source"].startswith("Für_Elise_teste-")
    for j in jobs:
        assert wait_done(client, j["id"])["status"] == "done"
    bad = client.post("/api/scores/generate", json={"file": "http://evil/x.mid"})
    assert bad.status_code == 422
    assert client.get("/api/scores/featured").json()["results"]


def test_favicon_is_served_and_linked(client):
    r = client.get("/favicon.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert 'href="/favicon.svg"' in client.get("/").text


def dominant_hz_of(content, tmp_path, start, end):
    import numpy as np

    from coil_prep.ffmpeg import decode_mono
    from coil_prep.midi import RATE

    p = tmp_path / "x.wav"
    p.write_bytes(content)
    x = decode_mono(p)[int(start * RATE) : int(end * RATE)]
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.argmax(spec) * RATE / len(x)


def test_get_notes_returns_generated_and_range(client, tmp_path):
    job = generate(client, tmp_path)
    r = client.get(f"/api/jobs/{job['id']}/notes").json()
    assert r["min_pitch"] == 44 and r["max_pitch"] == 79
    assert [n["pitch"] for n in r["notes"]] == [60, 64]
    assert r["generated"] == r["notes"]


def test_put_notes_rerenders_audio_and_keeps_generated(client, tmp_path):
    job = generate(client, tmp_path)
    url = f"/api/jobs/{job['id']}/notes"
    cur = client.get(url).json()
    # sobe a 1ª nota para A4 (440 Hz), apaga a 2ª
    edited = [{**cur["notes"][0], "pitch": 69}]
    r = client.put(url, json={"notes": edited})
    assert r.status_code == 200 and [n["pitch"] for n in r.json()["notes"]] == [69]
    again = client.get(url).json()
    assert [n["pitch"] for n in again["generated"]] == [60, 64]  # geradas não mudam
    audio = client.get(f"/api/jobs/{job['id']}/audio/result").content
    assert abs(dominant_hz_of(audio, tmp_path, 0.1, 0.9) - 440) < 6
    assert client.get(f"/api/jobs/{job['id']}").json()["result"]["segments"] == 1


def test_put_notes_rejects_empty_and_out_of_range(client, tmp_path):
    job = generate(client, tmp_path)
    url = f"/api/jobs/{job['id']}/notes"
    assert client.put(url, json={"notes": []}).status_code == 422
    bad = [{"start": 0, "end": 1, "pitch": 20}]
    r = client.put(url, json={"notes": bad})
    assert r.status_code == 422 and "fora da faixa" in r.json()["detail"]
    assert client.put("/api/jobs/x/notes", json={"notes": []}).status_code == 404


def test_export_mp3_and_invalid_format(client, tmp_path):
    job = generate(client, tmp_path)
    base = f"/api/jobs/{job['id']}/audio/result"
    mp3 = client.get(base + "?format=mp3&download=true")
    assert mp3.status_code == 200 and mp3.headers["content-type"] == "audio/mpeg"
    assert mp3.content[:3] == b"ID3" or mp3.content[0] == 0xFF
    assert ".mp3" in mp3.headers["content-disposition"]
    assert client.get(base + "?format=ogg").status_code == 422
    assert client.get(f"/api/jobs/{job['id']}/audio/original?format=mp3").status_code == 404


def test_editor_static_assets_are_served(client):
    r = client.get("/static/editor.js")
    assert r.status_code == 200 and "PianoRoll" in r.text
    assert "/static/editor.js" in client.get("/track/x").text


def read_feedback(tmp_path):
    import json

    path = tmp_path / "data" / "feedback.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_rating_without_edits_records_no_edit_event(client, tmp_path):
    job = generate(client, tmp_path)
    r = client.post(f"/api/jobs/{job['id']}/feedback", json={"rating": 1})
    assert r.json() == {"ok": True, "edit_recorded": False}
    kinds = [e["kind"] for e in read_feedback(tmp_path)]
    assert kinds == ["rating"]


def test_export_after_edit_records_diff_once_with_sidecar(client, tmp_path):
    job = generate(client, tmp_path)
    url = f"/api/jobs/{job['id']}"
    cur = client.get(url + "/notes").json()["notes"]
    edited = [{**cur[0], "pitch": 62}]  # 1ª movida +2, 2ª apagada
    assert client.put(url + "/notes", json={"notes": edited}).status_code == 200
    assert read_feedback(tmp_path) == []  # só editar não grava
    client.get(url + "/audio/result?download=true")
    client.get(url + "/audio/result?format=mp3&download=true")  # mesmo estado: não repete
    events = read_feedback(tmp_path)
    assert len(events) == 1 and events[0]["kind"] == "edit" and events[0]["job_id"] == job["id"]
    assert events[0]["summary"] == {
        "generated": 2,
        "final": 1,
        "kept": 0,
        "deleted": 1,
        "added": 0,
        "moved": 1,
    }
    assert events[0]["moved"][0]["pitch_delta"] == 2 and events[0]["deleted"][0][2] == 64
    import json

    side = json.loads((tmp_path / "data" / events[0]["notes_file"]).read_text())
    assert len(side["generated"]) == 2 and len(side["final"]) == 1


def test_rating_after_edit_records_edit_then_rating_and_state_counts_only_ratings(client, tmp_path):
    job = generate(client, tmp_path)
    url = f"/api/jobs/{job['id']}"
    cur = client.get(url + "/notes").json()["notes"]
    client.put(url + "/notes", json={"notes": cur[:1]})
    r = client.post(url + "/feedback", json={"rating": -1, "tag": "melodia errada"})
    assert r.json()["edit_recorded"] is True
    assert [e["kind"] for e in read_feedback(tmp_path)] == ["edit", "rating"]
    assert client.get("/api/state").json()["ratings"] == 1  # o evento de edição não conta


def test_speed_rerenders_slower_wav_and_rejects_bad_values(client, tmp_path):
    r = client.post(
        "/api/jobs", data={"stem": "mix"}, files={"file": ("a.mid", midi_bytes(tmp_path))}
    )
    job = wait_done(client, r.json()["id"])
    assert "shift" in job["result"] and "fast_warning" in job["result"]
    normal = client.get(f"/api/jobs/{job['id']}/audio/result").content
    slow = client.get(f"/api/jobs/{job['id']}/audio/result?speed=0.5")
    assert slow.status_code == 200 and len(slow.content) > len(normal) * 1.8
    assert client.get(f"/api/jobs/{job['id']}/audio/result?speed=5").status_code == 422


def test_midi_part_choice_via_api(client, tmp_path):
    from test_midi import song_mid

    data = song_mid(tmp_path).read_bytes()
    first = wait_done(
        client,
        client.post("/api/jobs", data={"stem": "mix"}, files={"file": ("s.mid", data)}).json()[
            "id"
        ],
    )
    res = first["result"]
    assert res["part"] is None and len(res["parts"]) == 4
    assert [p["id"] for p in res["parts"] if p["used"] > 0] == [0]
    second = wait_done(
        client,
        client.post(
            "/api/jobs", data={"stem": "mix", "part": "-1"}, files={"file": ("s.mid", data)}
        ).json()["id"],
    )
    assert second["result"]["part"] == -1


def test_state_exposes_live_keyboard_level(client):
    assert client.get("/api/state").json()["peak_db"] == -8.0


def test_style_presets_are_reported_and_switching_rerenders(client, tmp_path):
    r = client.post(
        "/api/jobs", data={"stem": "mix"}, files={"file": ("a.mid", midi_bytes(tmp_path))}
    )
    job = wait_done(client, r.json()["id"])
    res = job["result"]
    ids = [p["id"] for p in res["fidelity"]["presets"]]
    assert ids == ["auto", "classic", "staccato", "bright", "thin"]
    before = client.get(f"/api/jobs/{job['id']}/audio/result").content
    assert (
        client.put(f"/api/jobs/{job['id']}/style", json={"style": "bright"}).json()["duty"] == 0.25
    )
    after = client.get(f"/api/jobs/{job['id']}/audio/result").content
    assert after != before and after[:4] == b"RIFF"
    assert client.get(f"/api/jobs/{job['id']}").json()["result"]["style"] == "bright"
    assert client.put(f"/api/jobs/{job['id']}/style", json={"style": "x"}).status_code == 422
    assert client.put("/api/jobs/nope/style", json={"style": "classic"}).status_code == 404


def test_midi_download_and_excerpt_via_api(client, tmp_path):
    """Trecho (início/fim) e exportação do .mid das notas atuais."""
    path = tmp_path / "long.mid"
    make_mid(path, [(0, n, i, 1) for i, n in enumerate([60, 62, 64, 65, 67, 69])])  # 0,5 s cada
    r = client.post(
        "/api/jobs",
        data={"stem": "mix", "start": "1", "end": "2"},
        files={"file": ("long.mid", path.read_bytes(), "audio/midi")},
    )
    job = wait_done(client, r.json()["id"])
    assert job["status"] == "done", job
    assert job["result"]["segments"] == 2  # só as notas entre 1 s e 2 s
    mid = client.get(f"/api/jobs/{job['id']}/midi")
    assert mid.status_code == 200 and mid.content[:4] == b"MThd"
    assert "attachment" in mid.headers["content-disposition"]
    assert client.get("/api/jobs/nope/midi").status_code == 404
    bad = client.post(
        "/api/jobs",
        data={"stem": "mix", "start": "5", "end": "2"},
        files={"file": ("long.mid", path.read_bytes(), "audio/midi")},
    )
    assert bad.status_code == 422


def test_pages_are_served(client):
    for path in ("/", "/track/qualquer", "/soundpad", "/calibrate", "/scores"):
        r = client.get(path)
        assert r.status_code == 200 and "coil-prep" in r.text, path
    assert (
        "data-full" in client.get("/soundpad").text and "Soundpad" in client.get("/soundpad").text
    )
    assert client.get("/static/common.js").status_code == 200


@pytest.mark.parametrize(
    "old,new",
    [
        ("/teclado", "/soundpad"),
        ("/calibrar", "/calibrate"),
        ("/partituras", "/scores"),
        ("/faixa/abc", "/track/abc"),
    ],
)
def test_old_portuguese_urls_redirect(client, old, new):
    r = client.get(old, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == new
    assert client.get(old).status_code == 200


def test_batch_of_midi_generates_auto_and_all_tracks(client, tmp_path):
    r = client.post("/api/batches", files={"file": ("a.mid", midi_bytes(tmp_path))})
    assert r.status_code == 200
    batch = r.json()
    assert [j["variant"] for j in batch["jobs"]] == ["trilhas automáticas", "todas as trilhas"]
    for j in batch["jobs"]:
        assert wait_done(client, j["id"])["status"] == "done"
    status = client.get(f"/api/batches/{batch['id']}").json()
    assert len(status["jobs"]) == 2 and all(j["batch"] == batch["id"] for j in status["jobs"])
    assert status["source"].startswith("a-") and status["source"].endswith(".mid")


def test_batch_of_audio_has_one_job_per_stem(tmp_path):
    seen = []

    def fake(source, data_dir, profile, stem, progress=None, **kw):
        seen.append(stem)
        raise ValueError("sem áudio de verdade")

    from coil_prep.web.jobs import JobManager

    c = TestClient(create_app(tmp_path / "data", JobManager(tmp_path / "data", fake)))
    r = c.post("/api/batches", data={"url": "https://a/b", "stems": "mix,other,mix"})
    assert [j["variant"] for j in r.json()["jobs"]] == ["mix", "other"]
    for j in r.json()["jobs"]:
        wait_done(c, j["id"])
    assert sorted(seen) == ["mix", "other"]


@pytest.mark.parametrize(
    "data",
    [{}, {"url": "https://a/b", "stems": "bass"}, {"url": "https://a/b", "stems": ","}],
)
def test_invalid_batch_is_rejected(client, data):
    assert client.post("/api/batches", data=data).status_code == 422
    assert client.get("/api/batches/naoexiste").status_code == 404


def test_redo_with_other_part_joins_the_batch(client, tmp_path):
    batch = client.post("/api/batches", files={"file": ("a.mid", midi_bytes(tmp_path))}).json()
    first = batch["jobs"][0]
    wait_done(client, first["id"])
    r = client.post(f"/api/jobs/{first['id']}/part", json={"part": -1})
    assert r.status_code == 200 and r.json()["batch"] == batch["id"]
    assert wait_done(client, r.json()["id"])["status"] == "done"
    assert len(client.get(f"/api/batches/{batch['id']}").json()["jobs"]) == 3
    assert client.post("/api/jobs/x/part", json={"part": None}).status_code == 404


def live_notes():
    return [
        {"start": 5.0, "end": 5.3, "pitch": 60},
        {"start": 5.4, "end": 5.8, "pitch": 95},  # B6 ~1976 Hz: fora da faixa, dobra por oitava
    ]


@pytest.mark.parametrize("fmt,magic", [("wav", b"RIFF"), ("mid", b"MThd"), ("mp3", None)])
def test_live_export_formats(client, fmt, magic):
    from coil_prep.ffmpeg import FfmpegError, find_ffmpeg

    if fmt == "mp3":
        try:
            find_ffmpeg()
        except FfmpegError:
            pytest.skip("sem ffmpeg")
    r = client.post("/api/live/export", json={"format": fmt, "notes": live_notes()})
    assert r.status_code == 200, r.text
    assert f"soundpad.{fmt}" in r.headers["content-disposition"]
    if magic:
        assert r.content.startswith(magic)
    else:
        assert len(r.content) > 1000


def test_live_export_wav_trims_start_and_fits_range(client):
    import io
    import wave

    r = client.post("/api/live/export", json={"format": "wav", "notes": live_notes()})
    with wave.open(io.BytesIO(r.content)) as w:
        assert 0.75 < w.getnframes() / w.getframerate() < 0.85  # sem os 5 s iniciais


def test_live_export_midi_folds_pitch_unless_fit_off(client, tmp_path):
    import mido

    def pitches(fit):
        r = client.post(
            "/api/live/export", json={"format": "mid", "fit": fit, "notes": live_notes()}
        )
        path = tmp_path / f"{fit}.mid"
        path.write_bytes(r.content)
        return [m.note for m in mido.MidiFile(str(path)) if m.type == "note_on"]

    assert pitches(False) == [60, 95]
    assert pitches(True)[1] < 95  # 95 -> dobrado para dentro de 100-800 Hz


def test_live_export_keeps_chords(client, tmp_path):
    """Notas simultâneas (multitoque) saem juntas no MIDI e dentro do pico no WAV."""
    import io
    import wave

    import mido
    import numpy as np

    chord = [{"start": 0.0, "end": 0.5, "pitch": 60}, {"start": 0.1, "end": 0.4, "pitch": 64}]
    r = client.post("/api/live/export", json={"format": "mid", "notes": chord})
    path = tmp_path / "chord.mid"
    path.write_bytes(r.content)
    msgs = [m for m in mido.MidiFile(str(path)) if m.type in ("note_on", "note_off")]
    assert [(m.type, m.note) for m in msgs] == [
        ("note_on", 60),
        ("note_on", 64),
        ("note_off", 64),
        ("note_off", 60),
    ]
    r = client.post("/api/live/export", json={"format": "wav", "notes": chord})
    with wave.open(io.BytesIO(r.content)) as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767
    assert abs(len(data) / rate - 0.5) < 0.01
    assert np.abs(data).max() <= 10 ** (-8 / 20) + 1e-3
    assert len(np.unique(np.round(np.abs(data[int(0.2 * rate) : int(0.3 * rate)]), 2))) > 1


def test_live_export_merges_same_pitch_after_folding(client, tmp_path):
    import mido

    notes = [{"start": 0.0, "end": 0.5, "pitch": 72}, {"start": 0.2, "end": 0.6, "pitch": 96}]
    r = client.post("/api/live/export", json={"format": "mid", "notes": notes})
    assert r.status_code == 200, r.text
    path = tmp_path / "fold.mid"
    path.write_bytes(r.content)
    on = [m.note for m in mido.MidiFile(str(path)) if m.type == "note_on"]
    assert on == [72, 72]  # 96 dobra para 72; a anterior é cortada no início desta


def test_live_export_rejects_bad_input(client):
    assert (
        client.post("/api/live/export", json={"format": "ogg", "notes": live_notes()}).status_code
        == 422
    )
    assert client.post("/api/live/export", json={"format": "wav", "notes": []}).status_code == 422


def test_fractional_pitch_notes_can_be_saved(client, tmp_path):
    r = client.post(
        "/api/jobs", data={"stem": "mix"}, files={"file": ("a.mid", midi_bytes(tmp_path))}
    )
    job = wait_done(client, r.json()["id"])
    notes = [{"start": 0.0, "end": 0.5, "pitch": 57.3}, {"start": 0.5, "end": 1.0, "pitch": 60}]
    r = client.put(f"/api/jobs/{job['id']}/notes", json={"notes": notes})
    assert r.status_code == 200, r.text
    assert [n["pitch"] for n in r.json()["notes"]] == [57.3, 60]
    assert client.get(f"/api/jobs/{job['id']}/midi").content[:4] == b"MThd"


def test_recent_batches_lets_the_page_recover_a_lost_response(client, tmp_path):
    first = client.post("/api/batches", files={"file": ("a.mid", midi_bytes(tmp_path))}).json()
    r = client.get("/api/batches").json()
    assert r["batches"][0]["id"] == first["id"] and r["batches"][0]["jobs"] == 2
    assert 0 <= r["now"] - r["batches"][0]["created"] < 60


def test_cancel_batch_skips_queued_jobs(tmp_path):
    import threading

    from coil_prep.web.jobs import JobManager

    gate, ran = threading.Event(), []

    def slow(source, data_dir, profile, stem, progress=None, **kw):
        ran.append(stem)
        gate.wait(5)
        raise ValueError("fim")

    data = tmp_path / "data"
    c = TestClient(create_app(data, JobManager(data, slow)))
    b = c.post("/api/batches", data={"url": "https://a/b", "stems": "other,vocals,mix"}).json()
    time.sleep(0.3)  # a 1ª variação já começou
    assert c.post(f"/api/batches/{b['id']}/cancel").json() == {"canceled": 2}
    gate.set()
    wait_done(c, b["jobs"][0]["id"])
    time.sleep(0.3)
    status = [j["status"] for j in c.get(f"/api/batches/{b['id']}").json()["jobs"]]
    assert status == ["error", "canceled", "canceled"] and ran == ["other"]
    assert c.post("/api/batches/nada/cancel").status_code == 404


def test_old_finished_jobs_are_forgotten_before_a_running_batch(tmp_path):
    from coil_prep.web import jobs as jobs_mod

    m = jobs_mod.JobManager(tmp_path, lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    old = jobs_mod.MAX_JOBS
    jobs_mod.MAX_JOBS = 3
    try:
        first = [m.submit("https://a/b", "mix", batch="velho") for _ in range(2)]
        time.sleep(0.5)  # terminam (com erro)
        m._pool.submit(time.sleep, 1)  # segura a fila
        new = [m.submit("https://a/b", "mix", batch="novo") for _ in range(3)]
        assert all(m.get(j.id) for j in new)  # o lote novo inteiro continua
        assert not any(m.get(j.id) for j in first)
    finally:
        jobs_mod.MAX_JOBS = old
