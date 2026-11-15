import shutil

import pytest
from conftest import sine, write_wav

from coil_prep.analysis import analyze
from coil_prep.cli import main
from coil_prep.ffmpeg import decode_mono

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg não instalado")


def test_prep_hot_stereo_gives_mono_without_clipping(hot_stereo, tmp_path, capsys):
    out = tmp_path / "out.wav"
    assert main(["check", str(hot_stereo)]) == 1  # entrada clipada falha no check
    assert main(["prep", str(hot_stereo), "-o", str(out)]) == 0
    assert main(["check", str(out)]) == 0
    report = analyze(decode_mono(out))
    assert report.peak_db <= -3.0
    assert not report.clipped


def test_prep_batch_folder(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    write_wav(src / "a.wav", sine(440, amp=2.0))
    write_wav(src / "b.wav", sine(880, amp=2.0))
    (src / "notes.txt").write_text("ignorar")
    out = tmp_path / "out"
    assert main(["prep", str(src), "-o", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["a.wav", "b.wav"]


def test_highpass_attenuates_low_frequency(tmp_path):
    src, out = tmp_path / "low.wav", tmp_path / "out.wav"
    write_wav(src, sine(40, amp=0.5))
    assert main(["prep", str(src), "-o", str(out)]) == 0
    assert analyze(decode_mono(out)).peak_db < -20  # 40 Hz << 150 Hz de corte


def test_flags_override_preset(tmp_path):
    src, out = tmp_path / "low.wav", tmp_path / "out.wav"
    write_wav(src, sine(40, amp=0.5))
    assert main(["prep", str(src), "-o", str(out), "--highpass", "10"]) == 0
    assert analyze(decode_mono(out)).peak_db > -12


def test_missing_ffmpeg_gives_clear_error(hot_stereo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert main(["check", str(hot_stereo)]) == 2
    assert "ffmpeg não encontrado" in capsys.readouterr().err


def test_missing_input(tmp_path, capsys):
    assert main(["prep", str(tmp_path / "nao_existe.mp3"), "-o", str(tmp_path / "o.wav")]) == 1
