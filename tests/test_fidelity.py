import numpy as np
import pytest

from coil_prep import fidelity as F
from coil_prep.midi import RATE, midi_to_hz, render

SEGS = [(i * 0.25, i * 0.25 + 0.22, (p,)) for i, p in enumerate([69, 71, 72, 74] * 8)]


def duty_of(wave):
    return float((wave > 0).mean())


def test_render_default_is_unchanged_square():
    w = render([(0.0, 1.0, (69,))], 100, 600, 0.0)
    assert abs(duty_of(w[100:-100]) - 0.5) < 0.01 and set(np.unique(np.round(w[100:-100], 3))) == {
        -1,
        1,
    }


@pytest.mark.parametrize("duty", [0.25, 0.125])
def test_render_duty_sets_pulse_width_without_dc_and_peaks_at_one(duty):
    w = render([(0.0, 1.0, (69,))], 100, 600, 0.0, duty=duty)[200:-200]
    assert abs(w.mean()) < 0.01  # sem nível DC
    assert abs(np.abs(w).max() - 1) < 1e-9
    assert abs(duty_of(w) - duty) < 0.01  # o pulso largo é o lado alto
    assert w.max() == 1.0 and w.min() == pytest.approx(-duty / (1 - duty))  # pulso estreito alto


def test_gate_shortens_notes_but_keeps_pitch_and_minimum():
    w = render([(0.0, 0.2, (69,))], 100, 600, 0.0, gate=0.5)
    assert np.abs(w[: int(0.09 * RATE)]).max() > 0.9 and np.abs(w[int(0.12 * RATE) :]).max() == 0
    short = render([(0.0, 0.02, (69,))], 100, 600, 0.0, gate=0.1)
    assert np.abs(short).max() > 0.9  # nota curta não some


def _ref_audio(decay, harmonics):
    out = np.zeros(int(len(SEGS) * 0.25 * RATE) + RATE)
    for a, _, (p,) in SEGS:
        t = np.arange(int(0.25 * RATE)) / RATE
        f = midi_to_hz(p)
        w = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, harmonics + 1))
        out[int(a * RATE) : int(a * RATE) + len(t)] += w * np.exp(-t * decay)
    return out / np.abs(out).max()


def test_search_prefers_brighter_duty_for_a_bright_reference():
    best, _ = F.search(F.reference_from_audio(_ref_audio(0.5, 25), 100, 600), SEGS, 100, 600)
    assert best.duty < 0.5


def test_search_keeps_classic_for_a_dull_sustained_reference():
    best, table = F.search(F.reference_from_audio(_ref_audio(0.5, 2), 100, 600), SEGS, 100, 600)
    assert (best.duty, best.gate) == F.CLASSIC
    assert table[0][1].total >= table[-1][1].total  # tabela ordenada por score


def test_note_score_detects_wrong_notes():
    ref = F.reference_from_audio(_ref_audio(0.5, 3), 100, 600)
    wrong = [(a, b, (p + 1,)) for a, b, (p,) in SEGS]
    right = F.score(ref, SEGS, 100, 600, F.Style(*F.CLASSIC)).note
    assert right > 0.8 and F.score(ref, wrong, 100, 600, F.Style(*F.CLASSIC)).note < 0.3


def test_midi_reference_has_no_brightness_and_prefers_matching_articulation():
    # o .mid original é bem staccato (nota soa 50% do tempo): o gate menor deve ganhar do legato
    notes = [(a, a + 0.11, p) for a, _, p in SEGS]
    ref = F.reference_from_notes(notes)
    best, table = F.search(ref, SEGS, 100, 600)
    assert ref.centroid is None and all(s.brightness is None for _, s in table)
    assert best.duty == 0.5 and best.gate < 1.0
