import numpy as np
from conftest import sine

from coil_prep.analysis import analyze


def test_sine_half_scale_peak_is_minus_6_db():
    r = analyze(sine(440, amp=0.5).astype("float32"))
    assert abs(r.peak_db - (-6.02)) < 0.1
    assert not r.clipped


def test_detects_clipping():
    r = analyze(np.clip(sine(440, amp=3.0), -1, 1).astype("float32"))
    assert r.clipped


def test_isolated_full_scale_samples_are_not_clipping():
    x = np.zeros(1000, dtype="float32")
    x[100] = x[500] = 1.0
    assert not analyze(x).clipped
