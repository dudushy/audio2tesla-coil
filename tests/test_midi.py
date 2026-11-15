import importlib.util

import mido
import numpy as np
import pytest
from conftest import sine, write_wav

from coil_prep.analysis import analyze
from coil_prep.cli import main
from coil_prep.ffmpeg import decode_mono
from coil_prep.midi import RATE, Note, fold_to_range, midi_to_hz, read_midi, render, skyline

C_MAJOR = [60, 62, 64, 65, 67, 69, 71, 72]


def make_mid(path, events, tpb=480):
    """events: lista de (canal, nota, início_em_beats, duração_em_beats); 1 beat = 0,5 s."""
    track = mido.MidiTrack()
    msgs = []
    for ch, note, start, dur in events:
        msgs.append((start, 1, mido.Message("note_on", channel=ch, note=note, velocity=80)))
        msgs.append((start + dur, 0, mido.Message("note_off", channel=ch, note=note)))
    last = 0
    for beat, _, msg in sorted(msgs, key=lambda m: (m[0], m[1])):
        tick = round(beat * tpb)
        track.append(msg.copy(time=tick - last))
        last = tick
    mid = mido.MidiFile(ticks_per_beat=tpb)
    mid.tracks.append(track)
    mid.save(str(path))


def dominant_hz(x: np.ndarray) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=len(x) * 8))
    return float(np.argmax(spec) * RATE / (len(x) * 8))


def scale_mid(tmp_path, notes=C_MAJOR):
    path = tmp_path / "scale.mid"
    make_mid(path, [(0, n, i, 1) for i, n in enumerate(notes)])
    return path


def test_read_midi_scale_timing(tmp_path):
    notes = read_midi(scale_mid(tmp_path))
    assert [n.pitch for n in notes] == C_MAJOR
    assert [round(n.start, 3) for n in notes][:3] == [0.0, 0.5, 1.0]
    assert round(notes[0].end, 3) == 0.5


def test_read_midi_ignores_drums(tmp_path):
    path = tmp_path / "d.mid"
    make_mid(path, [(9, 36, 0, 1), (0, 60, 0, 1)])
    assert [n.pitch for n in read_midi(path)] == [60]


def test_skyline_picks_highest_note():
    notes = [Note(0, 2, 48), Note(1, 2, 72)]  # grave longa + aguda entrando em 1 s
    assert skyline(notes, gap=0) == [(0, 1, (48,)), (1, 2, (72,))]


def test_skyline_voices_and_merge_and_min_note():
    notes = [Note(0, 1, 60), Note(1, 2, 60), Note(0.5, 1.5, 67), Note(3, 3.02, 80)]
    assert skyline(notes, voices=1, gap=0) == [(0, 0.5, (60,)), (0.5, 1.5, (67,)), (1.5, 2, (60,))]
    assert skyline(notes, voices=2, gap=0)[1] == (0.5, 1.0, (67, 60))
    assert all(s[0] != 3 for s in skyline(notes, gap=0))  # nota de 20 ms descartada


def test_skyline_keeps_repeated_notes_separate_with_gap():
    notes = [Note(0, 0.5, 64), Note(0.5, 1.0, 64), Note(1.0, 1.5, 64)]  # três E4 seguidos
    assert skyline(notes, gap=0.02) == [(0, 0.48, (64,)), (0.5, 0.98, (64,)), (1.0, 1.5, (64,))]


def test_skyline_merges_one_note_split_by_a_lower_note():
    notes = [Note(0, 2, 72), Note(1, 1.5, 48)]
    assert skyline(notes) == [(0, 2, (72,))]


def test_render_has_silence_between_repeated_notes():
    segs = skyline([Note(0, 0.5, 64), Note(0.5, 1.0, 64)], gap=0.02)
    x = render(segs, 100, 800)
    assert np.all(x[int(0.485 * RATE) : int(0.495 * RATE)] == 0)  # meio do intervalo de 20 ms


def test_fold_always_fits_three_octave_range_and_keeps_pitch_class():
    for p in range(128):
        f = fold_to_range(p, 100, 800)
        assert 100 <= f <= 800
        assert abs(np.log2(f / midi_to_hz(p)) - round(np.log2(f / midi_to_hz(p)))) < 1e-9


def test_fold_rejects_range_under_one_octave():
    with pytest.raises(ValueError, match="oitava"):
        fold_to_range(60, 300, 500)


def test_render_is_square_with_requested_peak_and_pitch():
    x = render([(0, 1, (69,))], 100, 800, peak_db=-4)  # A4 = 440 Hz
    assert abs(20 * np.log10(np.abs(x).max()) + 4) < 0.01
    body = x[RATE // 10 : -RATE // 10]
    assert len(np.unique(np.round(body, 6))) == 2  # só dois níveis: onda quadrada
    assert abs(dominant_hz(body) - 440) < 3


def test_render_empty_raises():
    with pytest.raises(ValueError, match="nenhuma nota"):
        render([], 100, 800)


def test_cli_scale_end_to_end(tmp_path, capsys):
    out = tmp_path / "scale.wav"
    assert main(["midi", str(scale_mid(tmp_path)), "-o", str(out)]) == 0
    assert main(["check", str(out)]) == 0
    x = decode_mono(out)
    for i, note in enumerate(C_MAJOR):  # cada 0,5 s toca a nota esperada
        seg = x[int((0.5 * i + 0.1) * RATE) : int((0.5 * i + 0.4) * RATE)]
        assert abs(dominant_hz(seg) - midi_to_hz(note)) < 4, note
    assert not analyze(x).clipped


def test_cli_folds_out_of_range_note_with_profile(tmp_path):
    prof = tmp_path / "device.json"
    main(["calibrate", "save", "--min", "100", "--max", "800", "--profile", str(prof)])
    mid = tmp_path / "low.mid"
    make_mid(mid, [(0, 36, 0, 2)])  # C2 = 65,4 Hz, abaixo da faixa
    out = tmp_path / "o.wav"
    assert main(["midi", str(mid), "-o", str(out), "--profile", str(prof)]) == 0
    x = decode_mono(out)
    assert abs(dominant_hz(x[RATE // 10 : RATE]) - 130.8) < 3  # subiu uma oitava


def test_cli_empty_midi_is_clear_error(tmp_path, capsys):
    mid = tmp_path / "empty.mid"
    make_mid(mid, [(9, 36, 0, 1)])  # só percussão
    assert main(["midi", str(mid), "-o", str(tmp_path / "o.wav")]) == 1
    assert "nenhuma nota" in capsys.readouterr().err


def test_audio_without_basic_pitch_gives_clear_error(tmp_path, monkeypatch, capsys):
    src = tmp_path / "in.wav"
    write_wav(src, sine(440))
    monkeypatch.setitem(__import__("sys").modules, "basic_pitch.inference", None)
    assert main(["midi", str(src), "-o", str(tmp_path / "o.wav")]) == 1
    assert "pip install" in capsys.readouterr().err


@pytest.mark.skipif(importlib.util.find_spec("basic_pitch") is None, reason="extra [midi] ausente")
def test_basic_pitch_transcribes_two_synthetic_notes(tmp_path):
    x = np.concatenate([sine(440, 1.0, 0.6), np.zeros(RATE // 2), sine(330, 1.0, 0.6)])
    src = tmp_path / "two.wav"
    write_wav(src, x)
    out = tmp_path / "o.wav"
    assert main(["midi", str(src), "-o", str(out)]) == 0
    got = decode_mono(out)
    assert abs(dominant_hz(got[int(0.2 * RATE) : int(0.9 * RATE)]) - 440) < 6


def test_overlap_fraction():
    from coil_prep.midi import overlap_fraction

    assert overlap_fraction([Note(0, 1, 60), Note(1, 2, 62)]) == 0
    assert overlap_fraction([Note(0, 2, 60), Note(1, 2, 64)]) == 0.5
    assert overlap_fraction([]) == 0
