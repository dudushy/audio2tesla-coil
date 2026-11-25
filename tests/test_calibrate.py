import json

import pytest

from coil_prep.profile import Profile, load_profile, profile_from_notes, save_profile


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


def test_profile_from_heard_notes_keeps_edges_and_roundtrips(tmp_path):
    from coil_prep.notes import pitch_range

    p = profile_from_notes([60, 43, 55, 72, 43])
    assert p.notes == (43, 55, 60, 72)
    assert pitch_range(p.min_hz, p.max_hz) == (43, 72)
    save_profile(p, tmp_path / "d.json")
    assert load_profile(tmp_path / "d.json") == p
    octave = profile_from_notes([48, 60])  # exatamente 1 oitava
    assert octave.max_hz >= 2 * octave.min_hz


@pytest.mark.parametrize("notes", [[], [60], [60, 71], [10, 60], [60, 120]])
def test_profile_from_notes_rejects_bad_lists(notes):
    with pytest.raises(ValueError):
        profile_from_notes(notes)


def test_folding_uses_only_heard_octaves():
    from coil_prep.midi import contour_fold
    from coil_prep.notes import fold_pitch

    heard = (48, 60, 62, 74)  # o D da oitava de baixo (50) não foi ouvido
    assert fold_pitch(50, 48, 74, heard) == 62
    assert fold_pitch(52, 48, 74, heard) == 52  # nenhum E ouvido: fica na faixa
    p = profile_from_notes(list(heard))
    out = contour_fold([(0, 1, (50,)), (1, 2, (60,))], p.min_hz, p.max_hz, p.notes)
    assert [s[2][0] for s in out] == [62, 60]
