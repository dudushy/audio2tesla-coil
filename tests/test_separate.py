import subprocess

import pytest
from conftest import sine, write_wav

from coil_prep import separate as sep
from coil_prep.pipeline import process


def test_separate_runs_demucs_once_then_uses_cache(tmp_path, monkeypatch):
    src = tmp_path / "Minha Música [x].mp3"
    src.write_bytes(b"")
    calls = []

    def fake_run(cmd, check=False, **kwargs):
        calls.append(cmd)
        out = sep.stem_path(src, "vocals", tmp_path / "out")  # arquivo que o Demucs criaria
        out.parent.mkdir(parents=True)
        out.write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sep.subprocess, "run", fake_run)
    monkeypatch.setattr(sep.importlib.util, "find_spec", lambda name: object())
    first = sep.separate(src, "vocals", tmp_path / "out")
    again = sep.separate(src, "vocals", tmp_path / "out")
    assert first == again == tmp_path / "out" / "htdemucs" / "Minha Música [x]" / "vocals.wav"
    assert len(calls) == 1
    cmd = calls[0]
    assert (
        cmd[1:3] == ["-m", "demucs"]
        and "cpu" in cmd
        and cmd[cmd.index("--two-stems") + 1] == "vocals"
    )


def test_separate_without_demucs_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sep.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match=r"pip install -e '\.\[separate\]'"):
        sep.separate(tmp_path / "a.mp3", "vocals", tmp_path / "out")


def test_pipeline_separate_error_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(sep.importlib.util, "find_spec", lambda name: None)
    src = tmp_path / "in.wav"
    write_wav(src, sine(440))
    with pytest.raises(RuntimeError, match="Demucs não instalado"):
        process(str(src), tmp_path / "work", None, "other")


def test_pipeline_midi_ignores_stem(tmp_path):
    from test_midi import make_mid

    mid = tmp_path / "a.mid"
    make_mid(mid, [(0, 60, 0, 2)])
    res = process(str(mid), tmp_path / "work", None, "other")  # .mid não passa pelo Demucs
    assert res.stem == "mix"


def test_default_stem_is_other():
    assert sep.DEFAULT_STEM == "other"
