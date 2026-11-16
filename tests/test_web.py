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
