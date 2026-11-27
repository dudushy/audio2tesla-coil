import io

import numpy as np
import pytest
from fastapi.testclient import TestClient

from coil_prep.calibrate import RATE, write_mono_wav
from coil_prep.ffmpeg import decode_mono
from coil_prep.recording import to_wav
from coil_prep.web.app import create_app
from coil_prep.web.jobs import JobManager


def tone(path, seconds=2.0, amp=0.05):
    t = np.arange(int(RATE * seconds)) / RATE
    write_mono_wav(path, amp * np.sin(2 * np.pi * 220 * t), RATE)
    return path


def test_recording_is_normalized(tmp_path):
    out = to_wav(tone(tmp_path / "quiet.wav"), tmp_path / "out" / "r.wav")
    assert np.abs(decode_mono(out)).max() == pytest.approx(10 ** (-1 / 20), abs=0.01)


@pytest.mark.parametrize("seconds,amp,msg", [(0.3, 0.5, "curta"), (2.0, 0.0, "silêncio")])
def test_empty_recording_is_rejected(tmp_path, seconds, amp, msg):
    with pytest.raises(ValueError, match=msg):
        to_wav(tone(tmp_path / "x.wav", seconds, amp), tmp_path / "r.wav")


def test_recording_upload_becomes_wav_jobs(tmp_path):
    seen = []

    def fake(source, data_dir, profile, stem, progress=None, **kw):
        seen.append((source, stem))
        raise ValueError("fim do teste")

    data = tmp_path / "data"
    client = TestClient(create_app(data, JobManager(data, fake)))
    audio = tone(tmp_path / "a.wav").read_bytes()

    def send(name, content):
        return client.post(
            "/api/batches",
            data={"stems": "mix", "recording": "true"},
            files={"file": (name, io.BytesIO(content))},
        )

    r = send("gravacao.webm", audio)  # o ffmpeg reconhece pelo conteúdo
    assert r.status_code == 200, r.text
    source = r.json()["jobs"][0]["source"]
    assert source.startswith("gravacao-") and source.endswith(".rec.wav")
    assert (data / "input" / source).is_file()
    assert send("lixo.m4a", b"isto nao e audio").status_code == 422
    assert send("musica.mid", audio).status_code == 422


def test_voice_recording_is_one_exact_pitch_job(tmp_path):
    seen = []

    def fake(source, data_dir, profile, stem, progress=None, **kw):
        seen.append(kw)
        raise ValueError("fim do teste")

    data = tmp_path / "data"
    client = TestClient(create_app(data, JobManager(data, fake)))
    r = client.post(
        "/api/batches",
        data={"stems": "mix,other,vocals", "recording": "true", "voice": "true"},
        files={"file": ("g.webm", tone(tmp_path / "a.wav").read_bytes())},
    )
    assert [j["variant"] for j in r.json()["jobs"]] == ["afinação exata"]
    import time

    for _ in range(100):
        if seen:
            break
        time.sleep(0.05)
    assert seen and seen[0].get("method") == "exact"
