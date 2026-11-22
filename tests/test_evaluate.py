"""Avaliação objetiva (coil_prep.evaluate): tocabilidade do WAV e comparação com a referência."""

import numpy as np
import pytest

from coil_prep import evaluate
from coil_prep.midi import RATE, render

TUNE = [(0.0, 0.2, 60), (0.25, 0.45, 62), (0.5, 0.7, 64), (0.75, 0.95, 60), (1.0, 1.4, 67)]


def wav(notes, lo=100, hi=1000, **kw):
    return render([(a, b, (p,)) for a, b, p in notes], lo, hi, -8.0, **kw)


def test_square_notes_reads_back_what_was_rendered():
    got = evaluate.square_notes(wav(TUNE))
    assert [p for _, _, p in got] == [p for _, _, p in TUNE]
    for (a, b, _), (ga, gb, _) in zip(TUNE, got, strict=True):
        assert abs(ga - a) < 0.01 and abs(gb - b) < 0.01


def test_square_notes_splits_repeated_notes_with_a_short_gap():
    """Regressão da medição: pYIN (quadros de 93 ms) juntava colcheias repetidas numa nota só."""
    repeated = [(i * 0.24, i * 0.24 + 0.16, 55) for i in range(8)]
    assert len(evaluate.square_notes(wav(repeated))) == 8


def test_square_notes_ignores_silence_and_noise_floor():
    assert evaluate.square_notes(np.zeros(RATE)) == []
    quiet = np.concatenate([wav(TUNE), 1e-4 * np.sin(np.arange(RATE) * 0.1)])
    assert len(evaluate.square_notes(quiet)) == len(TUNE)


def test_playability_passes_for_the_pipeline_render():
    p = evaluate.playability(wav(TUNE), 100, 1000)
    assert p.ok, p.problems
    assert abs(p.peak_db + 8) < 0.1 and abs(p.duty - 0.5) < 0.02 and p.two_level > 0.9


def test_playability_flags_each_problem():
    assert "pico" in evaluate.playability(wav(TUNE) * 2, 100, 1000).problems[0]
    assert any("fora" in s for s in evaluate.playability(wav(TUNE), 300, 1000).problems)
    assert any("duty" in s for s in evaluate.playability(wav(TUNE, duty=0.25), 100, 1000).problems)
    two = render([(0, 1, (60, 67))], 100, 1000, -8.0)  # duas vozes somadas
    assert any("voz" in s for s in evaluate.playability(two, 100, 1000).problems)
    assert not evaluate.playability(np.zeros(100), 100, 1000).ok


mir_eval = pytest.importorskip("mir_eval")


def test_compare_identical_is_perfect_and_finds_the_time_offset():
    late = [(a + 0.3, b + 0.3, p) for a, b, p in TUNE]
    c = evaluate.compare(TUNE, late)
    assert c.f1 == 1.0 and c.time_correct > 0.95 and c.octave_errors == 0
    assert abs(c.offset + 0.3) < 0.02


def test_compare_counts_octave_errors_separately():
    up = [(a, b, p + 12) for a, b, p in TUNE]
    c = evaluate.compare(TUNE, up)
    assert c.f1 == 0 and c.f1_any_octave == 1.0 and c.octave_errors == 1.0
    assert c.time_correct == 0 and c.time_any_octave > 0.95


def test_compare_penalises_missing_ghost_and_late_notes():
    missing = evaluate.compare(TUNE, TUNE[:3])
    assert missing.recall == pytest.approx(0.6) and missing.precision == 1.0
    ghost = evaluate.compare(TUNE, [*TUNE, (1.6, 1.8, 72)])
    assert ghost.precision < 1.0 and ghost.recall == 1.0 and ghost.extra_time > 0.1
    # 1 nota 150 ms atrasada: fora da tolerância de 50 ms
    late = [TUNE[0], (0.40, 0.48, 62), *TUNE[2:]]
    assert evaluate.compare(TUNE, late).f1 == pytest.approx(0.8)


def test_compare_empty_is_zero_not_an_error():
    assert evaluate.compare(TUNE, []).f1 == 0 and evaluate.compare([], TUNE).f1 == 0
