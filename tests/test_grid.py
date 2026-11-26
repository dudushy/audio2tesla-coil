"""Áudio -> melodia tocável: grade rítmica, oitava original quando cabe, graves soltos, trecho e .mid.

Cada teste reproduz um defeito medido contra uma melodia de referência em onda quadrada
(coil_prep.evaluate): ritmo torto, oitava acima, saltos para o baixo, notas quebradas.
"""

import importlib.util
from itertools import pairwise

import mido
import numpy as np
import pytest
from conftest import write_wav
from test_midi import make_mid

from coil_prep import evaluate
from coil_prep.ffmpeg import decode_mono, trim_audio
from coil_prep.midi import (
    GRID_GATE,
    RATE,
    Note,
    best_shift,
    clip_notes,
    lead_line,
    midi_to_hz,
    read_midi,
    render,
    tempo_grid,
    to_grid,
    write_midi,
)
from coil_prep.pipeline import apply_edit, process
from coil_prep.profile import Profile

STEP = 0.238  # colcheia a 126 BPM
TUNE = [67, 67, 70, 70, 74, 72, 77, 79, 72, 70, 69, 65, 67, None, 67, 74] * 8


def jittery(tune=TUNE, step=STEP, phase=0.11, seed=1, jitter=0.03):
    """Como o Basic Pitch devolve uma melodia em colcheias: início ±30 ms, duração variável."""
    rng = np.random.default_rng(seed)
    notes = []
    for k, p in enumerate(tune):
        if p is None:
            continue
        a = phase + k * step + rng.uniform(-jitter, jitter)
        notes.append(Note(a, a + step * rng.uniform(0.6, 0.95), p, rng.uniform(0.5, 0.8)))
    return notes


def test_tempo_grid_recovers_step_and_phase_from_a_rough_beat_estimate():
    """O rastreador de batidas erra ~2% (123 em vez de 126 BPM): a grade não pode herdar o erro."""
    step, phase = tempo_grid(jittery(), beat=2 * STEP * 1.023)
    assert abs(step - STEP) < 0.0005  # < 0,25%: menos de 1/4 de célula de deriva em 30 s
    assert min(abs(phase - 0.11), abs(phase - 0.11 + step), abs(phase - 0.11 - step)) < 0.015


@pytest.mark.parametrize("beat_factor", [0.5, 1, 2, 4])
def test_tempo_grid_step_does_not_depend_on_the_beat_octave(beat_factor):
    """Rastreador em 63, 126 ou 252 BPM: o passo é sempre a subdivisão em [140, 280) ms."""
    step, _ = tempo_grid(jittery(), beat=2 * STEP * beat_factor)
    assert abs(step - STEP) < 0.0005


def test_tempo_grid_is_none_without_a_steady_pulse():
    rng = np.random.default_rng(3)
    starts = np.cumsum(rng.uniform(0.1, 0.9, 120))  # rubato: sem período
    free = [Note(float(a), float(a) + 0.2, 60, 0.7) for a in starts]
    assert tempo_grid(free, beat=0.5) is None
    assert tempo_grid(jittery()[:8], beat=2 * STEP) is None  # notas de menos para medir
    assert tempo_grid(jittery(), beat=0) is None


def test_to_grid_puts_every_onset_on_the_grid_and_keeps_the_tune():
    """Regressão: inícios com ±30 ms de erro davam F1 de início ~50% contra a referência."""
    notes = jittery()
    segs = to_grid(lead_line(notes), *tempo_grid(notes, beat=2 * STEP))
    want = [p for p in TUNE if p is not None]
    assert [p[0] for _, _, p in segs] == want
    starts = np.array([a for a, _, _ in segs])
    cells = np.round((starts - starts[0]) / STEP)
    assert np.abs(starts - starts[0] - cells * STEP).max() < 0.012
    assert all(abs((b - a) - GRID_GATE * STEP) < 0.005 for a, b, _ in segs)


def test_to_grid_rearticulates_long_notes_and_drops_fragments():
    """Nota longa vira uma nota por célula (o Basic Pitch a quebrava ao acaso: 90, 140, 480 ms)."""
    segs = [(0.0, 1.0, (60,)), (1.0, 1.05, (72,)), (1.25, 1.5, (62,))]
    out = to_grid(segs, 0.25, 0.0)
    assert [(round(a, 2), p[0]) for a, _, p in out] == [
        (0, 60),
        (0.25, 60),
        (0.5, 60),
        (0.75, 60),
        (1.25, 62),
    ]
    # mesma nota repetida: sempre há pausa entre as células (a bobina rearticula)
    assert all(b < nxt[0] for (_, b, _), nxt in pairwise(out))


def test_to_grid_picks_the_pitch_that_fills_most_of_the_cell():
    out = to_grid([(0.0, 0.08, (60,)), (0.08, 0.25, (64,))], 0.25, 0.0)
    assert [p[0] for _, _, p in out] == [64]
    assert to_grid([], 0.25, 0.0) == []


def test_lead_line_ignores_bass_leaking_under_and_between_the_melody():
    """Regressão: no mix a 1ª escolha caía no baixo (2-3 oitavas abaixo) em 20% das notas."""
    lead = [Note(i * 0.25, i * 0.25 + 0.2, 72 + (i % 5), 0.6) for i in range(40) if i % 8 != 7]
    bass = [Note(i * 1.0, i * 1.0 + 0.95, 36, 0.6) for i in range(10)]  # tão forte quanto a melodia
    pitches = {p[0] for _, _, p in lead_line(lead + bass)}
    assert 36 not in pitches and pitches <= {72, 73, 74, 75, 76}
    # só graves: nada é descartado (a mediana é o próprio baixo)
    assert {p[0] for _, _, p in lead_line(bass)} == {36}


def test_best_shift_keeps_the_original_octave_when_it_fits():
    """Regressão: uma voz grave (~104 Hz) subia 2 oitavas para o centro de uma faixa larga."""
    high = [
        (i * 0.25, i * 0.25 + 0.2, (p,)) for i, p in enumerate([67, 70, 74, 79, 82, 81, 82, 67])
    ]
    assert best_shift(high, 100, 1000) == 0  # 392-932 Hz já cabe: fica onde está
    low_voice = [(a, b, (p - 24,)) for a, b, (p,) in high]  # 98-233 Hz
    assert best_shift(low_voice, 39, 3136) == 0
    low = [(a, b, (p - 36,)) for a, b, (p,) in high]  # não cabe: sobe o mínimo para caber
    assert best_shift(low, 100, 1000) == 24


def test_best_shift_still_prefers_fitting_in_range_over_centring():
    # C6 (1047 Hz) não cabe em 100-1000: a música inteira desce, mesmo se já estivesse "centrada"
    segs = [(0, 5, (64,)), (5, 6, (84,))]
    shift = best_shift(segs, 100, 1000)
    assert all(100 <= midi_to_hz(p + shift) <= 1000 for _, _, (p,) in segs)
    # uma nota perdida de 1% do tempo fora da faixa não arrasta a música inteira
    stray = [(0, 99, (62,)), (99, 100, (26,))]
    assert best_shift(stray, 100, 1000) == 0


def test_clip_notes_keeps_only_the_excerpt_and_rebases_time():
    notes = [Note(0, 1, 60), Note(1, 3, 62), Note(3, 4, 64), Note(5, 6, 65)]
    got = clip_notes(notes, 2, 5)
    assert [(n.start, n.end, n.pitch) for n in got] == [(0, 1, 62), (1, 2, 64)]
    assert [n.pitch for n in clip_notes(notes, 3.5, None)] == [64, 65]


def test_write_midi_roundtrips_notes_in_seconds(tmp_path):
    segs = [(0.0, 0.16, (55,)), (0.238, 0.4, (55,)), (0.476, 0.64, (58,))]
    path = tmp_path / "m.mid"
    write_midi(path, segs, bpm=126)
    back = read_midi(path)
    assert [n.pitch for n in back] == [55, 55, 58]
    for n, (a, b, _) in zip(back, segs, strict=True):
        assert abs(n.start - a) < 0.002 and abs(n.end - b) < 0.002
    mid = mido.MidiFile(str(path))
    kinds = {m.type for m in mid.tracks[0]}
    assert "pitchwheel" not in kinds and len(mid.tracks) == 1
    assert (
        round(mido.tempo2bpm(next(m.tempo for m in mid.tracks[0] if m.type == "set_tempo"))) == 126
    )


def test_pipeline_exports_midi_matching_the_wav_and_passes_playability(tmp_path):
    mid = tmp_path / "song.mid"
    make_mid(mid, [(0, n, i, 1) for i, n in enumerate([60, 62, 64, 65, 67, 69, 71, 72])])
    res = process(str(mid), tmp_path / "w", Profile(100, 1000), "mix")
    assert res.style == "classic" and res.duty == 0.5  # padrão: a quadrada de 50%
    wav = decode_mono(res.wav)
    play = evaluate.playability(wav, 100, 1000)
    assert play.ok, play.problems
    exported = [(n.start, n.end, n.pitch) for n in read_midi(res.midi)]
    assert [p for _, _, p in exported] == [p for _, _, p in evaluate.square_notes(wav)]
    # editar as notas regrava o .mid
    res, _ = apply_edit(res, [{"start": 0.0, "end": 0.5, "pitch": 60}])
    assert [n.pitch for n in read_midi(res.midi)] == [60]


def test_pipeline_start_end_on_midi(tmp_path):
    mid = tmp_path / "song.mid"
    make_mid(
        mid, [(0, n, i, 1) for i, n in enumerate([60, 62, 64, 65, 67, 69, 71, 72])]
    )  # 0,5 s cada
    res = process(str(mid), tmp_path / "w", Profile(100, 1000), "mix", start=1.0, end=2.5)
    got = evaluate.square_notes(decode_mono(res.wav))
    assert [p for _, _, p in got] == [64, 65, 67]
    assert got[0][0] < 0.02 and abs(got[-1][1] - 1.5) < 0.03
    assert "1-2.5" in res.wav.name  # não sobrescreve a saída da música inteira
    with pytest.raises(ValueError, match="trecho"):
        process(str(mid), tmp_path / "w", Profile(100, 1000), "mix", start=3, end=3.5)


def test_trim_audio_cuts_the_excerpt_and_reuses_the_file(tmp_path):
    t = np.arange(4 * RATE) / RATE
    src = tmp_path / "in.wav"
    write_wav(src, 0.5 * np.sin(2 * np.pi * np.where(t < 2, 220, 440) * t))
    cut = trim_audio(src, 1.5, 3.0, tmp_path / "cuts")
    x = decode_mono(cut)
    assert abs(len(x) / RATE - 1.5) < 0.02 and cut.name == "in.1.5-3.wav"
    assert trim_audio(src, 1.5, 3.0, tmp_path / "cuts") == cut
    assert trim_audio(src, 2.0, None, tmp_path / "cuts").name == "in.2-fim.wav"


def test_rendered_grid_melody_scores_high_against_itself_as_reference():
    """Ponta a ponta sem modelo: notas 'transcritas' tortas -> grade -> WAV x referência limpa."""
    notes = jittery(phase=0.3, jitter=0.07)  # inícios até 70 ms fora: além da tolerância de 50 ms
    segs = to_grid(lead_line(notes), *tempo_grid(notes, beat=2 * STEP * 0.98))
    shift = best_shift(segs, 100, 1000)
    out = render([(a, b, (p[0] + shift,)) for a, b, p in segs], 100, 1000, -8.0)
    ref = [(k * STEP, k * STEP + 0.16, p + shift) for k, p in enumerate(TUNE) if p is not None]
    pytest.importorskip("mir_eval")
    c = evaluate.compare(ref, evaluate.square_notes(out))
    assert c.f1 > 0.97 and c.octave_errors == 0 and c.onset_jitter_ms < 15
    # sem a grade (só a escolha da melodia), o ritmo torto derruba o F1
    raw = render([(a, b, (p[0] + shift,)) for a, b, p in lead_line(notes)], 100, 1000, -8.0)
    assert evaluate.compare(ref, evaluate.square_notes(raw)).f1 < c.f1 - 0.1


def test_to_grid_keeps_a_short_note_with_a_clear_attack():
    """Regressão (crítico, 50,3 s): D5 de 90 ms enchia 38% da célula e sumia da melodia."""
    segs = [(0.0, 0.16, (67,)), (0.24, 0.40, (67,)), (0.49, 0.58, (74,)), (0.72, 0.9, (72,))]
    out = to_grid(segs, 0.238, 0.0)
    assert [p[0] for _, _, p in out] == [67, 67, 74, 72]
    # fragmento sem ataque perto do ponto da grade (cauda de nota) continua não virando nota
    assert [p[0] for _, _, p in to_grid([(0.0, 0.2, (60,)), (0.2, 0.25, (64,))], 0.238, 0.0)] == [
        60
    ]


def test_playability_reports_a_single_out_of_range_note():
    """Regressão (crítico): o relatório usava percentis e escondia uma nota rara fora da faixa."""
    notes = [(i * 0.25, i * 0.25 + 0.2, 67) for i in range(80)] + [(20.0, 20.2, 53)]
    x = render([(a, b, (p,)) for a, b, p in notes], 100, 1000, -8.0)
    play = evaluate.playability(x, 180, 1000)
    assert play.min_hz < 180 and not play.ok
    # e o início de cada nota (ciclo parcial, vindo do silêncio) não vira frequência falsa
    assert abs(play.max_hz - midi_to_hz(67)) < 3


@pytest.mark.skipif(
    importlib.util.find_spec("basic_pitch") is None, reason="precisa do extra [midi]"
)
def test_excerpt_keeps_the_notes_right_before_its_end(tmp_path):
    """Regressão (crítico, 64,6 s): ao cortar o trecho, o Basic Pitch perdia o último ~0,5 s."""
    tune = [60, 64, 67, 72] * 4  # 0,5 s por nota, 8 s
    x = render([(i * 0.5, i * 0.5 + 0.45, (p,)) for i, p in enumerate(tune)], 100, 1000, -8.0)
    src = tmp_path / "tune.wav"
    write_wav(src, x)
    res = process(str(src), tmp_path / "w", Profile(100, 1000), "mix", start=1.0, end=5.0)
    got = evaluate.square_notes(decode_mono(res.wav))
    assert [p % 12 for _, _, p in got] == [p % 12 for p in tune[2:10]]
    assert got[0][0] < 0.05 and got[-1][0] > 3.4  # última nota (4,5-5 s) presente, tempo rebaseado
