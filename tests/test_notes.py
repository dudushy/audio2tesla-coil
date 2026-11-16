import pytest

from coil_prep import notes as nt
from coil_prep.midi import midi_to_hz


def n(start, end, pitch, id=None):
    return {"id": id, "start": start, "end": end, "pitch": pitch}


def test_pitch_range_fits_the_coil_band():
    low, high = nt.pitch_range(100, 800)
    assert midi_to_hz(low) >= 100 > midi_to_hz(low - 1)
    assert midi_to_hz(high) <= 800 < midi_to_hz(high + 1)
    assert (low, high) == (44, 79)  # G#2 (103,8 Hz) .. G5 (784 Hz)


def test_from_and_to_segments_roundtrip():
    segs = [(0.0, 0.5, (60,)), (0.5, 1.0, (64,))]
    notes = nt.from_segments(segs)
    assert [x.id for x in notes] == [0, 1]
    assert nt.to_segments(notes) == segs


def test_validate_sorts_assigns_ids_and_keeps_existing():
    out = nt.validate([n(1, 2, 64, id=5), n(0, 0.5, 60), n(2, 3, 62, id=5)], 100, 800)
    assert [x.start for x in out] == [0, 1, 2]
    assert len({x.id for x in out}) == 3 and 5 in {x.id for x in out}  # id duplicado ganha novo


def test_validate_trims_overlap_to_one_voice():
    a, b = nt.validate([n(0, 1, 60), n(0.5, 1.5, 64)], 100, 800)
    assert (a.start, a.end) == (0, 0.5) and (b.start, b.end) == (0.5, 1.5)


def test_validate_drops_previous_note_when_it_would_be_too_short():
    out = nt.validate([n(0, 1, 60), n(0.01, 1, 64)], 100, 800)
    assert [x.pitch for x in out] == [64]


@pytest.mark.parametrize(
    "bad",
    [
        n(0, 1, 43),  # 98 Hz, abaixo da faixa
        n(0, 1, 100),  # acima da faixa
        n(1, 1.005, 60),  # curta demais
        n(-1, 1, 60),
        n(2, 1, 60),
        {"start": "x", "end": 1, "pitch": 60},
        {"start": float("nan"), "end": 1, "pitch": 60},
    ],
)
def test_validate_rejects_bad_notes(bad):
    with pytest.raises(ValueError):
        nt.validate([bad], 100, 800)


def test_validate_rejects_too_many_notes():
    many = [n(i * 0.1, i * 0.1 + 0.05, 60) for i in range(nt.MAX_NOTES + 1)]
    with pytest.raises(ValueError, match="notas demais"):
        nt.validate(many, 100, 800)


def test_save_and_load_roundtrip(tmp_path):
    gen = nt.from_segments([(0.0, 0.5, (60,))])
    cur = [nt.EditNote(0, 0.1, 0.6, 62)]
    nt.save(tmp_path / "a.json", 100, 800, gen, cur)
    data = nt.load(tmp_path / "a.json")
    assert data["generated"] == gen and data["current"] == cur and data["max_hz"] == 800
