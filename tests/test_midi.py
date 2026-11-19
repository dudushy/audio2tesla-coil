import importlib.util
import itertools

import mido
import numpy as np
import pytest
from conftest import sine, write_wav

from coil_prep.analysis import analyze
from coil_prep.ffmpeg import decode_mono
from coil_prep.midi import (
    RATE,
    Note,
    best_shift,
    contour_fold,
    fold_to_range,
    median_note_ms,
    melody_line,
    merge_same_pitch,
    midi_to_hz,
    out_of_range_fraction,
    read_midi,
    render,
    skyline,
    transpose,
)
from coil_prep.pipeline import process
from coil_prep.profile import Profile


def run(path, tmp_path, profile=None):
    return process(str(path), tmp_path / "work", profile, "mix")


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


def test_pipeline_scale_end_to_end(tmp_path):
    res = run(scale_mid(tmp_path), tmp_path)
    assert res.ok and res.original is None
    x = decode_mono(res.wav)
    for i, note in enumerate(C_MAJOR):  # cada 0,5 s toca a nota esperada
        seg = x[int((0.5 * i + 0.1) * RATE) : int((0.5 * i + 0.4) * RATE)]
        assert abs(dominant_hz(seg) - midi_to_hz(note)) < 4, note
    assert not analyze(x).clipped


def test_pipeline_folds_out_of_range_note_with_profile(tmp_path):
    mid = tmp_path / "low.mid"
    make_mid(mid, [(0, 36, 0, 2)])  # C2 = 65,4 Hz, abaixo da faixa
    x = decode_mono(run(mid, tmp_path, Profile(100, 800)).wav)
    assert abs(dominant_hz(x[RATE // 10 : RATE]) - 130.8) < 3  # subiu uma oitava


def test_pipeline_empty_midi_is_clear_error(tmp_path):
    mid = tmp_path / "empty.mid"
    make_mid(mid, [(9, 36, 0, 1)])  # só percussão
    with pytest.raises(ValueError, match="nenhuma nota"):
        run(mid, tmp_path)


def test_audio_without_basic_pitch_gives_clear_error(tmp_path, monkeypatch):
    src = tmp_path / "in.wav"
    write_wav(src, sine(440))
    monkeypatch.setitem(__import__("sys").modules, "basic_pitch.inference", None)
    with pytest.raises(RuntimeError, match="pip install"):
        run(src, tmp_path)


@pytest.mark.skipif(importlib.util.find_spec("basic_pitch") is None, reason="extra [midi] ausente")
def test_basic_pitch_transcribes_two_synthetic_notes(tmp_path):
    x = np.concatenate([sine(440, 1.0, 0.6), np.zeros(RATE // 2), sine(330, 1.0, 0.6)])
    src = tmp_path / "two.wav"
    write_wav(src, x)
    got = decode_mono(run(src, tmp_path).wav)
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


def test_pipeline_weak_noise_is_removed(tmp_path):
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
    x = decode_mono(run(path, tmp_path).wav)
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


def test_best_shift_is_zero_when_music_already_fits():
    segs = [(0, 1, (60,)), (1, 2, (64,))]
    assert best_shift(segs, 100, 800) == 0


def test_best_shift_moves_whole_song_into_range():
    # E5..B5 passam de 800 Hz (E5=659 ok, G5=784 ok, B5=988 fora); com -12 tudo cabe
    segs = [(0, 1, (88,)), (1, 2, (91,)), (2, 3, (95,))]  # E6, G6, B6: tudo acima de 1 kHz
    shift = best_shift(segs, 100, 800)
    moved = transpose(segs, shift)
    assert out_of_range_fraction(moved, 100, 800) == 0
    assert [b[2][0] - a[2][0] for a, b in zip(segs, moved)] == [shift] * 3  # contorno preservado


def test_out_of_range_fraction_and_median_note():
    segs = [(0, 1, (60,)), (1, 4, (100,))]  # 1 s dentro, 3 s fora
    assert out_of_range_fraction(segs, 100, 800) == 0.75
    assert median_note_ms([(0, 0.1, (60,)), (1, 1.3, (60,)), (2, 2.2, (60,))]) == pytest.approx(200)


# --- notas que sumiam: escolha da trilha, tempo, oitava x semitom, de-para com o teclado do site ---

MELODY = [60, 62, 64, 60, 64, 67, 65, 64, 62, 60, 62, 64, 60, 67, 64, 62]


def song_mid(tmp_path, with_backing=True):
    """Melodia no canal 0 + (opcional) arpejo agudo, baixo e acorde sustentado. Sem copyright."""
    ev = [(0, n, i * 0.5, 0.5) for i, n in enumerate(MELODY)]
    if with_backing:
        ev += [(1, 72 + (i % 3) * 4, i * 0.5 + 0.25, 0.5) for i in range(16)]  # arpejo acima
        ev += [(2, 36 + (i % 4) * 2, i * 0.5, 0.5) for i in range(16)]  # baixo
        ev += [(3, p, 0, 8) for p in (55, 59, 62)]  # acorde longo
    path = tmp_path / "song.mid"
    make_mid(path, ev)
    return path


def pitch_classes(wav_segments):
    return [p % 12 for _, _, (p,) in wav_segments]


def test_auto_part_picks_melody_not_backing(tmp_path):
    from coil_prep.midi import read_midi_parts, select_part

    parts = read_midi_parts(song_mid(tmp_path))
    assert len(parts) == 4
    assert select_part(parts, None).id == 0  # a melodia, não o arpejo agudo nem o acorde


def test_process_keeps_every_melody_note_with_backing(tmp_path):
    """Regressão: antes, baixo/arpejo/acorde 'ganhavam' da melodia e só 4-6 de 16 notas sobravam."""
    res = process(str(song_mid(tmp_path)), tmp_path / "w", Profile(100, 1000), "mix")
    from coil_prep import notes as notes_mod

    got = notes_mod.load(res.notes_file)["current"]
    assert [n.pitch % 12 for n in got] == [p % 12 for p in MELODY]
    assert res.part is None and len(res.parts) == 4  # automático = melodia combinada
    assert {p["id"] for p in res.parts if p["used"] > 0} == {0}  # nada do acompanhamento entrou


def test_process_part_override_and_all(tmp_path):
    path = song_mid(tmp_path)
    bass = process(str(path), tmp_path / "w", Profile(100, 1000), "mix", part=2)
    assert bass.part == 2 and bass.segments == 16
    every = process(str(path), tmp_path / "w2", Profile(100, 1000), "mix", part=-1)
    assert every.part == -1
    with pytest.raises(ValueError, match="não existe"):
        process(str(path), tmp_path / "w3", Profile(100, 1000), "mix", part=999)


def test_read_midi_parts_follows_tempo_changes(tmp_path):
    tpb = 480
    t0, t1 = mido.MidiTrack(), mido.MidiTrack()
    t0.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))  # 120 bpm: 1 beat = 0,5 s
    t0.append(mido.MetaMessage("set_tempo", tempo=1000000, time=tpb))  # 60 bpm a partir do beat 1
    t1 += [
        mido.Message("note_on", note=60, velocity=80, time=0),
        mido.Message("note_off", note=60, time=tpb * 2),  # 1 beat a 120 + 1 beat a 60 = 1,5 s
    ]
    mid = mido.MidiFile(ticks_per_beat=tpb)
    mid.tracks += [t0, t1]
    path = tmp_path / "tempo.mid"
    mid.save(str(path))
    from coil_prep.midi import read_midi_parts

    (part,) = read_midi_parts(path)
    assert part.id == 16 and round(part.notes[0].end, 3) == 1.5


def test_read_midi_retrigger_and_unterminated_note(tmp_path):
    track = mido.MidiTrack(
        [
            mido.Message("note_on", note=60, velocity=80, time=0),
            mido.Message("note_on", note=60, velocity=80, time=480),  # sem note_off: religa
            mido.Message("note_on", note=64, velocity=80, time=480),  # nunca fecha
            mido.Message("note_off", note=60, time=480),
        ]
    )
    mid = mido.MidiFile(ticks_per_beat=480)
    mid.tracks.append(track)
    path = tmp_path / "r.mid"
    mid.save(str(path))
    notes = read_midi(path)
    assert [(n.pitch, round(n.start, 2), round(n.end, 2)) for n in notes] == [
        (60, 0.0, 0.5),
        (60, 0.5, 1.5),
        (64, 1.0, 1.5),  # fechada no fim do arquivo (antes sumia)
    ]


def test_best_shift_only_moves_by_octaves():
    # melodia que "cabe mais" 3 semitons acima: a versão antiga mudava o tom; agora só oitavas
    segs = [(i * 0.5, i * 0.5 + 0.4, (p,)) for i, p in enumerate([83, 84, 85, 86, 87, 88])]
    assert best_shift(segs, 100, 1000) % 12 == 0


def test_every_teslacoil_keyboard_note_is_reachable_with_its_pitch_class():
    """De-para com o teclado de teslacoil.app (C2-B6, 60 notas, A4=440): nada é descartado.

    Notas fora da faixa calibrada são dobradas para outra oitava, mas mantêm a classe de altura.
    """
    from coil_prep.notes import pitch_range

    assert (round(midi_to_hz(36)), round(midi_to_hz(69)), round(midi_to_hz(95))) == (65, 440, 1976)
    lo, hi = pitch_range(100, 1000)
    assert (lo, hi) == (44, 83)  # G#2..B5: só C2-G2 e C6-B6 do site ficam fora de 100-1000 Hz
    for pitch in range(36, 96):
        seg = contour_fold([(0, 1, (pitch,))], 100, 1000)
        folded = seg[0][2][0]
        assert lo <= folded <= hi and (folded - pitch) % 12 == 0


# --- melodia que passa de uma trilha para outra (ex.: "Blue": riff de sintetizador + voz) ---

RIFF = [62, 67, 70, 72]  # trilha A: riff, toca nos compassos pares
VOICE = [57, 55, 53, 55, 57, 60]  # trilha B: voz, toca nos ímpares


def handoff_mid(tmp_path):
    """Trilha 0 (canal 0) toca em 0-4 s e 8-12 s; canal 1 em 4-8 s e 12-16 s; + baixo e pad
    (acordes) o tempo todo. Cada trilha sozinha deixa buracos de 4 s."""
    ev = []
    for base in (0, 16):  # em beats (1 beat = 0,5 s): 0-4 s e 8-12 s
        ev += [(0, RIFF[i % 4], base + i * 0.5, 0.5) for i in range(16)]
    for start_s in (4, 12):
        b = start_s * 2  # em beats
        ev += [(1, VOICE[i % 6], b + i * 0.5, 0.5) for i in range(16)]
    ev += [(2, 36 + (i % 3) * 2, i, 1) for i in range(32)]  # baixo contínuo
    ev += [(3, p, i * 2, 2) for i in range(16) for p in (65, 69, 72)]  # pad em acordes
    path = tmp_path / "handoff.mid"
    make_mid(path, ev)
    return path


def sounding_fraction(segments, total):
    return sum(b - a for a, b, _ in segments) / total


def test_combined_melody_fills_the_holes_of_a_single_track(tmp_path):
    from coil_prep.midi import combine_parts, read_midi_parts

    parts = read_midi_parts(handoff_mid(tmp_path))
    notes, used = combine_parts(parts)
    assert set(used) == {0, 1}  # baixo (2) e pad em acordes (3) não entram
    line = melody_line(notes, 0.06, 0.02, 0.02)
    assert sounding_fraction(line, 16) > 0.85  # antes: ~50% (só a trilha principal)
    # cada trecho tem as alturas da trilha certa
    first = {p % 12 for a, b, (p,) in line if a < 4}
    second = {p % 12 for a, b, (p,) in line if 4 <= a < 8}
    assert first <= {p % 12 for p in RIFF} and second <= {p % 12 for p in VOICE}


def test_combined_never_interrupts_the_main_track(tmp_path):
    from coil_prep.midi import combine_parts, read_midi_parts

    # canal 1 toca o tempo todo e canal 0 também (mais notas): a principal fica intacta
    ev = [(0, 60 + (i % 5), i * 0.5, 0.5) for i in range(16)]
    ev += [(1, 72 + (i % 4), i * 0.5, 0.5) for i in range(16)]
    path = tmp_path / "full.mid"
    make_mid(path, ev)
    notes, used = combine_parts(read_midi_parts(path))
    assert len(used) == 1 and len(notes) == 16


def test_short_pauses_are_not_filled(tmp_path):
    from coil_prep.midi import combine_parts, read_midi_parts

    # pausa de 0,5 s (< 1 s) na principal; a secundária toca nela: não deve entrar
    ev = [(0, 60 + i % 5, i * 0.5, 0.5) for i in range(8)]
    ev += [(0, 60 + i % 5, 5 + i * 0.5, 0.5) for i in range(8)]
    ev += [(1, 76 + i % 4, i * 0.5, 0.5) for i in range(24)]
    path = tmp_path / "pause.mid"
    make_mid(path, ev)
    _, used = combine_parts(read_midi_parts(path), rel=0)
    assert set(used) == {1}  # a principal é a que mais toca; nada entra em pausa curta


def test_pipeline_combined_melody_is_not_dropped_by_calibration_range(tmp_path):
    """Faixa estreita (100-600 Hz) só dobra de oitava; não descarta nem corta notas."""
    from coil_prep import notes as notes_mod

    path = handoff_mid(tmp_path)
    narrow = process(str(path), tmp_path / "n", Profile(100, 600), "mix")
    wide = process(str(path), tmp_path / "w", Profile(100, 2000), "mix")
    assert narrow.segments == wide.segments
    cur_n = notes_mod.load(narrow.notes_file)["current"]
    cur_w = notes_mod.load(wide.notes_file)["current"]
    assert [round(n.start, 3) for n in cur_n] == [round(n.start, 3) for n in cur_w]
    assert [n.pitch % 12 for n in cur_n] == [n.pitch % 12 for n in cur_w]  # mesma música
