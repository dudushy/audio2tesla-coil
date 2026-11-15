import json
import shutil
import wave

import numpy as np
import pytest
from conftest import sine, write_wav

from coil_prep.analysis import analyze
from coil_prep.calibrate import RATE, square_tones
from coil_prep.cli import main
from coil_prep.ffmpeg import decode_mono
from coil_prep.profile import Profile, load_profile


def test_square_tones_length_and_level():
    x = square_tones([200, 400], seconds=1.0, gap=0.5, peak_db=-12)
    assert len(x) == int(RATE * 1.5) * 2
    assert abs(20 * np.log10(np.abs(x).max()) - (-12)) < 0.01
    assert set(np.unique(np.abs(x[:RATE]))) == {np.abs(x).max()}  # só dois níveis: onda quadrada


def test_tones_command_writes_wav(tmp_path, capsys):
    out = tmp_path / "t.wav"
    assert (
        main(["calibrate", "tones", "-o", str(out), "--freqs", "200", "400", "--seconds", "0.2"])
        == 0
    )
    with wave.open(str(out)) as w:
        assert (w.getnchannels(), w.getframerate()) == (1, RATE)
    assert "200 Hz" in capsys.readouterr().out


def test_save_and_load_profile(tmp_path):
    path = tmp_path / "device.json"
    assert main(["calibrate", "save", "--min", "200", "--max", "3000", "--profile", str(path)]) == 0
    assert json.loads(path.read_text())["max_hz"] == 3000
    assert load_profile(path) == Profile(200, 3000)


@pytest.mark.parametrize("lo,hi", [("0", "100"), ("500", "500"), ("900", "300")])
def test_save_rejects_invalid_range(tmp_path, lo, hi):
    path = tmp_path / "device.json"
    assert main(["calibrate", "save", "--min", lo, "--max", hi, "--profile", str(path)]) == 1
    assert not path.exists()


def test_load_missing_profile_is_clear_error(tmp_path):
    with pytest.raises(ValueError, match="não encontrado"):
        load_profile(tmp_path / "nope.json")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg não instalado")
def test_prep_uses_profile_range(tmp_path):
    prof = tmp_path / "device.json"
    main(["calibrate", "save", "--min", "1000", "--max", "3000", "--profile", str(prof)])
    src = tmp_path / "in.wav"
    write_wav(src, sine(300, amp=0.5))  # 300 Hz: dentro do preset default, fora do perfil
    out_default, out_prof = tmp_path / "d.wav", tmp_path / "p.wav"
    assert main(["prep", str(src), "-o", str(out_default)]) == 0
    assert main(["prep", str(src), "-o", str(out_prof), "--profile", str(prof)]) == 0
    assert analyze(decode_mono(out_prof)).peak_db < analyze(decode_mono(out_default)).peak_db - 10
    # flag sobrescreve o perfil
    out_flag = tmp_path / "f.wav"
    assert (
        main(["prep", str(src), "-o", str(out_flag), "--profile", str(prof), "--highpass", "100"])
        == 0
    )
    assert analyze(decode_mono(out_flag)).peak_db > analyze(decode_mono(out_prof)).peak_db + 10


def test_prep_missing_profile_fails(tmp_path):
    src = tmp_path / "in.wav"
    write_wav(src, sine(300))
    assert (
        main(
            ["prep", str(src), "-o", str(tmp_path / "o.wav"), "--profile", str(tmp_path / "x.json")]
        )
        == 1
    )
