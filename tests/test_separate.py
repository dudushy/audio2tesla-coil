import subprocess

import pytest
from conftest import sine, write_wav

from coil_prep import separate as sep
from coil_prep.cli import main


def test_separate_runs_demucs_once_then_uses_cache(tmp_path, monkeypatch):
    src = tmp_path / "Minha Música [x].mp3"
    src.write_bytes(b"")
    calls = []

    def fake_run(cmd, check=False):
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


def test_cli_separate_error_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sep.importlib.util, "find_spec", lambda name: None)
    src = tmp_path / "in.wav"
    write_wav(src, sine(440))
    assert main(["prep", str(src), "-o", str(tmp_path / "o.wav"), "--separate"]) == 1
    assert "Demucs não instalado" in capsys.readouterr().err


def test_midi_separate_rejects_midi_input(tmp_path, capsys):
    mid = tmp_path / "a.mid"
    mid.write_bytes(b"")
    assert main(["midi", str(mid), "-o", str(tmp_path / "o.wav"), "--separate"]) == 1
    assert "só vale para áudio" in capsys.readouterr().err
