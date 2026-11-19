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


def test_calibration_tones_are_a_wav_with_timing(client):
    info = client.get("/api/calibrate").json()["tones"]
    assert info[0] == {"start": 0.0, "hz": 100} and info[1]["start"] == 2.0
    wav = client.get("/api/calibrate/tones.wav")
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"


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
    assert "/static/editor.js" in client.get("/").text


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
    assert res["part"] == 0 and len(res["parts"]) == 4
    assert [p["id"] for p in res["parts"] if p["chosen"]] == [0]
    second = wait_done(
        client,
        client.post(
            "/api/jobs", data={"stem": "mix", "part": "-1"}, files={"file": ("s.mid", data)}
        ).json()["id"],
    )
    assert second["result"]["part"] == -1


def test_state_exposes_live_keyboard_level(client):
    assert client.get("/api/state").json()["peak_db"] == -8.0
