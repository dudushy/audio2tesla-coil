import numpy as np
import pytest

from coil_prep import mt3, rmvpe
from coil_prep.pipeline import process


def test_decode_weighted_average_and_silence():
    sal = np.zeros((3, 360), np.float32)
    sal[0, 100] = 1.0  # bin 100 = 1997.38 + 2000 cents
    sal[1, 200], sal[1, 201] = 0.5, 0.5  # meio caminho entre dois bins
    # quadro 2: abaixo do limiar -> sem voz
    sal[2, 50] = 0.02
    f0 = rmvpe._decode(sal)
    assert f0[0] == pytest.approx(10 * 2 ** ((1997.3794 + 2000) / 1200), rel=1e-4)
    assert f0[1] == pytest.approx(10 * 2 ** ((1997.3794 + 4010) / 1200), rel=1e-4)
    assert f0[2] == 0


def test_rmvpe_cache_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert rmvpe.model_path() == tmp_path / "coil-prep" / "rmvpe.onnx"


def test_vocalpitch_method_keeps_measured_pitch(tmp_path, monkeypatch):
    from conftest import sine, write_wav

    src = tmp_path / "song.wav"
    write_wav(src, sine(220, 2.0))
    monkeypatch.setattr(rmvpe, "voice_notes", lambda p: [(0.1, 0.9, (57.3,)), (1.0, 1.8, (60.0,))])
    r = process(str(src), tmp_path / "w", None, "mix", method="vocalpitch")
    import json

    got = [n["pitch"] for n in json.loads(r.notes_file.read_text())["current"]]
    assert got == [57.3, 60] and r.shift == 0
    assert r.wav.name.endswith(".mix.vocalpitch.wav")


def test_mt3_method_uses_its_notes(tmp_path, monkeypatch):
    from conftest import sine, write_wav

    from coil_prep.midi import Note

    src = tmp_path / "song.wav"
    write_wav(src, sine(220, 2.0))
    monkeypatch.setattr(
        mt3,
        "transcribe_notes",
        lambda p: [Note(0.0, 0.5, 64), Note(0.5, 1.0, 67), Note(0.0, 1.0, 40)],
    )
    r = process(str(src), tmp_path / "w", None, "mix", method="mt3")
    assert r.segments == 2  # a melodia (mais aguda), sem o baixo


def test_audio_only_methods_reject_note_files(tmp_path):
    from test_midi import make_mid

    mid = tmp_path / "a.mid"
    make_mid(mid, [(0, 60, 0, 1)])
    for m in ("vocalpitch", "mt3", "exact"):
        with pytest.raises(ValueError, match="é para áudio"):
            process(str(mid), tmp_path / "w", None, "mix", method=m)


def test_mt3_missing_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(mt3.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match="mt3-infer"):
        mt3.transcribe_notes(tmp_path / "x.wav")


def test_fetch_resumes_after_dropped_connection(tmp_path, monkeypatch):
    import io

    from coil_prep import downloads

    data = bytes(range(256)) * 40
    calls = []

    class Resp(io.BytesIO):
        def __init__(self, body, status):
            super().__init__(body)
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=0):
        rng = req.get_header("Range")
        start = int(rng.split("=")[1].rstrip("-")) if rng else 0
        calls.append(start)
        if len(calls) == 1:  # 1ª conexão cai no meio
            part = tmp_path / "m.bin.part"
            part.write_bytes(data[:3000])
            raise OSError("conexão caiu")
        return Resp(data[start:], 206)

    monkeypatch.setattr(downloads.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(downloads.time, "sleep", lambda s: None)
    out = downloads.fetch("http://x/m.bin", tmp_path / "m.bin", len(data))
    assert out.read_bytes() == data and calls == [0, 3000]
    assert downloads.fetch("http://x/m.bin", tmp_path / "m.bin", len(data)) == out  # já completo
