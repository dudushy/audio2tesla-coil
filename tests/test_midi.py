import importlib.util
import itertools

import mido
import numpy as np
import pytest
from conftest import sine, write_wav

from coil_prep.analysis import analyze
from coil_prep.cli import main
from coil_prep.ffmpeg import decode_mono
from coil_prep.midi import (
    RATE,
    Note,
    contour_fold,
    fold_to_range,
    melody_line,
    merge_same_pitch,
    midi_to_hz,
    read_midi,
    render,
    skyline,
)

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


def test_melody_line_ignores_weak_high_noise_and_keeps_melody():
    melody = [Note(0, 1, 64, 0.8), Note(1, 2, 67, 0.8)]
    hihat = [Note(0.2, 0.7, 96, 0.1), Note(1.2, 1.6, 100, 0.1)]  # agudo mas fraco
    got = melody_line(melody + hihat, gap=0)
    assert [s[2] for s in got] == [(64,), (67,)]


def test_melody_line_prefers_loud_lead_over_loud_bass():
    notes = [Note(0, 2, 40, 0.7), Note(0, 2, 72, 0.7)]  # empate de amplitude: a aguda ganha
    assert [s[2] for s in melody_line(notes, gap=0)] == [(72,)]


def test_merge_same_pitch_fragments_but_not_repeated_notes():
    frag = [(0, 0.5, (64,)), (0.51, 1.0, (64,))]  # 10 ms de silêncio: nota quebrada
    assert merge_same_pitch(frag, 0.02) == [(0, 1.0, (64,))]
    assert merge_same_pitch(frag, 0) == frag  # desligado
    repeated = [(0, 0.5, (64,)), (0.5, 1.0, (64,))]  # encostadas: repetição intencional
    assert merge_same_pitch(repeated, 0.02) == repeated
    far = [(0, 0.5, (64,)), (0.6, 1.0, (64,))]  # 100 ms de pausa: duas notas
    assert merge_same_pitch(far, 0.02) == far


def test_contour_fold_avoids_false_octave_jumps_at_range_edge():
    # melodia cruzando 800 Hz (G5=784, A5=880): nota a nota, A5 viraria A4 (salto de 10 semitons)
    segs = [(0, 1, (79,)), (1, 2, (81,)), (2, 3, (83,))]
    got = [s[2][0] for s in contour_fold(segs, 100, 800)]
    assert got == [79, 69, 71]  # A5/B5 não cabem; ficam em A4/B4, mas sem saltar além do necessário
    assert all(100 <= midi_to_hz(p) <= 800 for p in got)
    # a diferença de classe de altura é preservada
    assert [(p - q) % 12 for p, q in zip(got, [79, 81, 83])] == [0, 0, 0]


def test_contour_fold_keeps_steps_small():
    # escala descendo até o piso (100 Hz ~ G2/Ab2): só o passo forçado pela borda pode passar de 6
    segs = [(i, i + 1, (p,)) for i, p in enumerate([60, 55, 50, 45, 40, 43, 47])]
    got = [s[2][0] for s in contour_fold(segs, 100, 800)]
    assert max(abs(b - a) for a, b in itertools.pairwise(got)) <= 7
    assert all(100 <= midi_to_hz(p) <= 800 for p in got)


def test_cli_select_skyline_keeps_old_behavior(tmp_path):
    mid = tmp_path / "two.mid"
    make_mid(mid, [(0, 60, 0, 2), (0, 72, 1, 1)])
    out = tmp_path / "o.wav"
    assert main(["midi", str(mid), "-o", str(out), "--select", "skyline"]) == 0
    x = decode_mono(out)
    assert abs(dominant_hz(x[int(0.6 * RATE) : int(0.9 * RATE)]) - midi_to_hz(72)) < 6


def test_cli_end_to_end_weak_noise_is_removed(tmp_path):
    # melodia forte (C4 E4 G4, notas de 0,3 s) + "chiado" agudo e curto; velocity vira amp
    path = tmp_path / "m.mid"
    track = mido.MidiTrack()
    mid = mido.MidiFile(ticks_per_beat=480)
    msgs = []
    for start, note, vel in [
        (0, 60, 100),
        (0.5, 64, 100),
        (1.0, 67, 100),
        (0.1, 100, 8),
        (0.6, 100, 8),
    ]:
        msgs.append((start, 1, mido.Message("note_on", note=note, velocity=vel)))
        msgs.append((start + 0.3, 0, mido.Message("note_off", note=note)))
    last = 0
    for beat, _, msg in sorted(msgs, key=lambda m: (m[0], m[1])):
        tick = round(beat * 2 * 480)  # `beat` aqui está em segundos; 120 bpm = 2 beats/s
        track.append(msg.copy(time=tick - last))
        last = tick
    mid.tracks.append(track)
    mid.save(str(path))
    out = tmp_path / "o.wav"
    assert main(["midi", str(path), "-o", str(out)]) == 0
    x = decode_mono(out)
    for t, note in [(0, 60), (0.5, 64), (1.0, 67)]:
        seg = x[int((t + 0.05) * RATE) : int((t + 0.25) * RATE)]
        assert abs(dominant_hz(seg) - midi_to_hz(note)) < 6, note


def test_transcribe_audio_passes_min_note_and_reads_amplitude(tmp_path, monkeypatch):
    import sys
    import types

    from coil_prep.midi import transcribe_audio

    seen = {}

    def fake_predict(path, **kw):
        seen.update(kw)
        return None, None, [(0.0, 0.5, 64, 0.7, None)]  # (início, fim, altura, amplitude, bends)

    fake = types.ModuleType("basic_pitch.inference")
    fake.predict = fake_predict
    monkeypatch.setitem(sys.modules, "basic_pitch", types.ModuleType("basic_pitch"))
    monkeypatch.setitem(sys.modules, "basic_pitch.inference", fake)
    src = tmp_path / "in.wav"
    write_wav(src, sine(440, 0.5))
    assert transcribe_audio(src, 90) == [Note(0.0, 0.5, 64, 0.7)]
    assert seen == {"minimum_note_length": 90}


def test_melody_line_sustained_bass_does_not_capture_the_melody():
    bass = Note(0, 2, 43, 0.6)  # nota grave longa, amplitude parecida com a da melodia
    lead = [Note(0.2 * i, 0.2 * i + 0.15, p, 0.6) for i, p in enumerate([72, 76, 79, 76, 72])]
    got = melody_line([bass, *lead], gap=0)
    assert [s[2][0] for s in got][:5] == [72, 76, 79, 76, 72]  # depois só resta o baixo
