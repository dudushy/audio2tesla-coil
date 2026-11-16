import json

import numpy as np
import pytest

from coil_prep.calibrate import RATE, square_tones
from coil_prep.profile import Profile, load_profile, save_profile


def test_square_tones_length_and_level():
    x = square_tones([200, 400], seconds=1.0, gap=0.5, peak_db=-12)
    assert len(x) == int(RATE * 1.5) * 2
    assert abs(20 * np.log10(np.abs(x).max()) - (-12)) < 0.01
    assert set(np.unique(np.abs(x[:RATE]))) == {np.abs(x).max()}  # só dois níveis: onda quadrada


def test_save_and_load_profile(tmp_path):
    path = tmp_path / "device.json"
    save_profile(Profile(200, 3000), path)
    assert json.loads(path.read_text())["max_hz"] == 3000
    assert load_profile(path) == Profile(200, 3000)


@pytest.mark.parametrize("lo,hi", [(0, 100), (500, 500), (900, 300)])
def test_profile_rejects_invalid_range(lo, hi):
    with pytest.raises(ValueError, match="faixa inválida"):
        Profile(lo, hi)


def test_load_missing_profile_is_clear_error(tmp_path):
    with pytest.raises(ValueError, match="não encontrado"):
        load_profile(tmp_path / "nope.json")
