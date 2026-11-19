"""Verificação objetiva: o WAV final toca as notas certas? (harness em coil_prep.verify)"""

import importlib.util

import pytest
from test_midi import make_mid

from coil_prep import verify
from coil_prep.ffmpeg import decode_mono
from coil_prep.midi import RATE, Note, midi_to_hz, render, skyline
from coil_prep.pipeline import process
from coil_prep.profile import Profile


def run_report(path, tmp_path, lo, hi, reference):
    res = process(str(path), tmp_path / f"w{lo}-{hi}", Profile(lo, hi), "mix")
    return verify.compare(reference, decode_mono(res.wav))


@pytest.mark.parametrize("pitch", [43, 48, 57, 60, 69, 72, 75, 83])
def test_render_plays_the_exact_frequency(pitch):
    x = render([(0, 0.5, (pitch,))], 90, 1000)
    got = verify.detect_midi_pitch(x, 0.25)
    assert got is not None and abs(got - pitch) < 0.03  # < 3 cents
    assert abs(440 * 2 ** ((got - 69) / 12) - midi_to_hz(pitch)) < 0.5


def test_detect_returns_none_on_silence():
    assert verify.detect_midi_pitch(render([(0, 0.2, (60,))], 90, 1000), 0.9) is None


def guitar_like(path, melody, root=43):
    """Uma trilha só (canal 0), como um violão: riff agudo curto + raiz grave longa e FORTE
    por baixo. Regressão: a raiz ganhava do riff e 29% do tempo da Blue tocava o baixo."""
    ev = []
    for i, n in enumerate(melody):
        ev.append((0, n, i * 0.5, 0.5))
    for b in range(0, len(melody) // 2, 2):
        ev.append((0, root, b, 1.9))  # raiz longa
    path.parent.mkdir(exist_ok=True)
    make_mid(path, ev)  # velocity fixa de make_mid: raiz e riff com a mesma força
    return [Note(i * 0.25, i * 0.25 + 0.25, n) for i, n in enumerate(melody)]


RIFF = [62, 67, 70, 72, 70, 67, 65, 69, 72, 74, 72, 69] * 2


def test_riff_over_sustained_root_is_played_not_the_root(tmp_path):
    ref = guitar_like(tmp_path / "g" / "guitar.mid", RIFF)
    rep = run_report(tmp_path / "g" / "guitar.mid", tmp_path, 100, 2000, ref)
    assert rep.pitch_class >= 0.95 and rep.exact_octave >= 0.95 and rep.contour >= 0.95


def test_narrow_range_keeps_the_tune_and_the_pitch_names(tmp_path):
    """Faixa 100-600 Hz: D#5 (622 Hz) não cabe. Só a oitava muda; nota e desenho se mantêm."""
    riff = [62, 67, 70, 75, 70, 67, 62, 67, 70, 75, 70, 67] * 3
    ref = guitar_like(tmp_path / "n" / "narrow.mid", riff)
    rep = run_report(tmp_path / "n" / "narrow.mid", tmp_path, 100, 600, ref)
    assert rep.pitch_class == 1.0  # nunca troca a nota (dó, ré...) por outra
    assert rep.contour >= 0.95 and rep.false_leaps == 0
    # e com a faixa larga o resultado fica 100% igual ao original
    wide = run_report(tmp_path / "n" / "narrow.mid", tmp_path, 100, 1000, ref)
    assert wide.exact_octave == 1.0 and wide.contour == 1.0


def test_long_melody_does_not_drift_octaves(tmp_path):
    """Regressão do contour_fold antigo (oitava 'mais próxima da anterior' acumulava erro):
    melodia dentro da faixa, com uma única nota alta fora, não pode deslocar o resto."""
    mel = [62, 70, 67, 70] * 10
    mel[20] = 75  # D#5 = 622 Hz: única nota fora de 100-600 Hz
    segs = [(i, i + 1, (p,)) for i, p in enumerate(mel)]
    from coil_prep.midi import contour_fold

    got = [s[2][0] for s in contour_fold(segs, 100, 600)]
    moved = [i for i, (a, b) in enumerate(zip(mel, got, strict=True)) if a != b]
    assert moved == [20] and (got[20] - 75) % 12 == 0  # antes: a nota seguinte já descia 1 oitava


def test_report_lines_are_printable():
    rep = verify.compare([Note(0, 1, 60)], render([(0, 1, (60,))], 100, 1000))
    assert rep.exact_octave == 1.0 and "contorno" in "\n".join(rep.lines())


@pytest.mark.skipif(importlib.util.find_spec("basic_pitch") is None, reason="sem Basic Pitch")
def test_synthetic_audio_melody_keeps_pitch_classes(tmp_path):
    """Caminho áudio -> notas (Basic Pitch): escala em onda quadrada sintética."""
    from conftest import write_wav

    notes = [60, 62, 64, 65, 67, 69, 71, 72]
    wav = tmp_path / "scale.wav"
    write_wav(wav, render([(i * 0.5, i * 0.5 + 0.4, (p,)) for i, p in enumerate(notes)], 50, 2000))
    ref = [Note(i * 0.5, i * 0.5 + 0.4, p) for i, p in enumerate(notes)]
    res = process(str(wav), tmp_path / "w", Profile(100, 1000), "mix")
    rep = verify.compare(ref, decode_mono(res.wav))
    assert rep.heard >= 0.75 and rep.pitch_class >= 0.9 and rep.contour >= 0.9


def test_skyline_reference_is_the_highest_voice():
    notes = [Note(0, 2, 40), Note(0.5, 1, 70)]
    assert [p[0] for _, _, p in skyline(notes, 1, 0.06, 0.0)] == [40, 70, 40]
    assert RATE == 44100
