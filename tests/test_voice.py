import itertools

import numpy as np
import pytest

from coil_prep.midi import midi_to_hz
from coil_prep.notes import as_pitch, validate
from coil_prep.voice import notes_from_track, voice_notes

librosa = pytest.importorskip("librosa")
RATE = 22050


def sung(path, parts, vibrato=0.3):
    """Voz sintética: harmônicos, vibrato e notas fora do temperamento (em centésimos)."""
    out = []
    for pitch, secs in parts:
        t = np.arange(int(RATE * secs)) / RATE
        if pitch is None:
            out.append(np.zeros_like(t))
            continue
        f = midi_to_hz(pitch + vibrato * np.sin(2 * np.pi * 5.5 * t))
        phase = 2 * np.pi * np.cumsum(f) / RATE
        wave = sum(a * np.sin(k * phase) for k, a in ((1, 1), (2, 0.8), (3, 0.5), (4, 0.3)))
        env = np.minimum(1, np.minimum(t, t[::-1]) * 30)
        out.append(0.2 * wave * env + 0.002 * np.random.default_rng(0).standard_normal(len(t)))
    from coil_prep.calibrate import write_mono_wav

    write_mono_wav(path, np.concatenate(out), RATE)
    return path


def test_low_voice_keeps_its_exact_pitch(tmp_path):
    # voz grave (G#2 ~104 Hz) desafinada em +0,3 e -0,2 semitom, com vibrato e harmônicos fortes
    wav = sung(tmp_path / "v.wav", [(44.3, 0.6), (None, 0.15), (47.8, 0.6), (51.3, 0.8)])
    segs = voice_notes(wav)
    assert [round(p[0], 1) for _, _, p in segs] == pytest.approx([44.3, 47.8, 51.3], abs=0.15)
    assert segs[0][1] - segs[0][0] > 0.45  # vibrato não picota a nota


def test_noise_and_silence_are_not_notes(tmp_path):
    from coil_prep.calibrate import write_mono_wav

    noise = 0.01 * np.random.default_rng(1).standard_normal(RATE)
    write_mono_wav(tmp_path / "n.wav", np.r_[np.zeros(RATE), noise], RATE)
    assert voice_notes(tmp_path / "n.wav") == []


def test_short_blips_are_dropped_and_gaps_inside_a_note_are_bridged():
    times = np.arange(200) * 256 / RATE
    midi = np.full(200, 60.0)
    midi[50:53] = np.nan  # falha de 35 ms: mesma nota
    midi[120:124] = 72.0  # 46 ms fora: deslize, não nota
    midi[150:] = 64.0
    segs = notes_from_track(times, midi)
    assert [p[0] for _, _, p in segs] == [60, 64]


def test_fractional_pitch_survives_validation():
    assert as_pitch(60.0) == 60 and isinstance(as_pitch(60.0), int)
    assert as_pitch("61.257") == 61.26
    notes = validate([{"start": 0, "end": 1, "pitch": 57.3}], 100, 800)
    assert notes[0].pitch == 57.3


def test_pipeline_voice_mode_keeps_low_voice_in_place(tmp_path):
    from coil_prep.pipeline import process
    from coil_prep.profile import profile_from_notes

    wav = sung(tmp_path / "low.wav", [(44, 0.7), (None, 0.1), (47, 0.7), (49, 0.7)], vibrato=0.1)
    profile = profile_from_notes(list(range(27, 104)))  # faixa larga, como a calibragem real
    r = process(str(wav), tmp_path, profile, "mix", method="exact")
    import json

    pitches = [n["pitch"] for n in json.loads(r.notes_file.read_text())["current"]]
    assert r.shift == 0 and r.moved == 0
    assert pitches == pytest.approx([44, 47, 49], abs=0.15)


def test_repeated_syllables_on_the_same_note_stay_separate():
    """Regressão: "lá-lá-lá" na mesma altura virava uma nota longa (o ritmo sumia)."""
    times = np.arange(120) * 256 / RATE
    midi = np.full(120, 57.2)
    level = np.ones(120)
    for v in (40, 80):  # dois vales de volume entre três sílabas
        level[v - 2 : v + 3] = [0.6, 0.3, 0.2, 0.3, 0.6]
    segs = notes_from_track(times, midi, np.full(120, 0.9), level)
    assert len(segs) == 3 and all(p == (57.2,) for _, _, p in segs)
    assert all(b2 - a1 > 0 for (_, a1, _), (b2, _, _) in itertools.pairwise(segs))
