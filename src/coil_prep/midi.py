"""Modo melodia: notas (de .mid ou de áudio) -> 1 voz -> faixa da bobina -> onda quadrada."""

import os
import tempfile
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np

from .ffmpeg import run_ffmpeg

RATE = 44100
MIDI_EXTS = {".mid", ".midi"}
DRUM_CHANNEL = 9  # canal 10 do MIDI (índice 9) é percussão


@dataclass(frozen=True)
class Note:
    start: float  # s
    end: float  # s
    pitch: int  # número MIDI
    amp: float = 1.0  # 0..1; força da nota (amplitude do Basic Pitch, velocity do .mid)


def midi_to_hz(pitch: float) -> float:
    return 440.0 * 2 ** ((pitch - 69) / 12)


def read_midi(path: Path) -> list[Note]:
    """Lê um .mid (ignora percussão). mido já resolve tempo -> segundos."""
    import mido

    try:
        mid = mido.MidiFile(str(path))
    except (OSError, ValueError, EOFError) as e:
        raise ValueError(f"MIDI inválido: {path} ({e})") from None
    notes, active, now = [], {}, 0.0
    for msg in mid:  # iterar já une as trilhas; msg.time é delta em segundos
        now += msg.time
        if msg.type not in ("note_on", "note_off") or msg.channel == DRUM_CHANNEL:
            continue
        key = (msg.channel, msg.note)
        if msg.type == "note_on" and msg.velocity > 0:
            active.setdefault(key, (now, msg.velocity))
        elif key in active:
            start, vel = active.pop(key)
            notes.append(Note(start, now, msg.note, vel / 127))
    return notes


def transcribe_audio(path: Path, min_note_ms: float = 80.0) -> list[Note]:
    """Áudio -> notas via Basic Pitch (extra [midi]). Decodifica antes com o ffmpeg.

    `min_note_ms`: o padrão do Basic Pitch é 127,7 ms, longo demais para melodias rápidas
    (o Mario de referência tem notas de ~140 ms); o ruído que entra é limpo depois.
    """
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")  # TensorFlow é muito verboso
    try:
        from basic_pitch.inference import predict
    except ImportError as e:
        raise RuntimeError(
            f"Não foi possível importar o Basic Pitch: {e}\n"
            "Instale/atualize o extra: pip install -e '.[midi]' "
            "(precisa de Python 3.10 ou 3.11 no Linux; veja o README)"
        ) from None
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "in.wav"
        run_ffmpeg(["-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "22050", str(wav)])
        _, _, events = predict(str(wav), minimum_note_length=min_note_ms)
    return [Note(float(s), float(e), int(p), float(a)) for s, e, p, a, *_ in events]


def skyline(
    notes: list[Note], voices: int = 1, min_note: float = 0.06, gap: float = 0.02
) -> list[tuple]:
    """Em cada instante fica com as `voices` notas mais agudas ativas.

    Retorna segmentos (início, fim, (alturas...)) sem sobreposição. Trechos menores que
    `min_note` s são descartados (ruído de transcrição). Notas repetidas (mesma altura,
    uma logo após a outra) continuam separadas: o fim de cada trecho que encosta no
    próximo é encurtado em `gap` s para a bobina articular as duas, em vez de segurar uma nota.
    """
    if not notes:
        return []
    cuts = sorted({t for n in notes for t in (n.start, n.end)})
    segments = []  # (início, fim, ids das notas, alturas)
    for a, b in pairwise(cuts):
        mid = (a + b) / 2
        active = sorted(
            (i for i, n in enumerate(notes) if n.start <= mid < n.end),
            key=lambda i: (-notes[i].pitch, i),
        )[:voices]
        if not active:
            continue
        ids = tuple(active)
        if segments and segments[-1][1] == a and segments[-1][2] == ids:
            segments[-1] = (segments[-1][0], b, ids)  # mesma(s) nota(s) cortada(s) por outra
        else:
            segments.append((a, b, ids))
    out = [(a, b, tuple(notes[i].pitch for i in ids)) for a, b, ids in segments]
    return finish(out, min_note, gap)


def finish(out: list[tuple], min_note: float, gap: float) -> list[tuple]:
    """Descarta trechos < `min_note` e abre `gap` s antes do trecho seguinte que encosta."""
    out = [s for s in out if s[1] - s[0] >= min_note]
    for k in range(len(out) - 1):
        a, b, p = out[k]
        if abs(out[k + 1][0] - b) < 1e-6:  # encosta no próximo trecho
            out[k] = (a, b - min(gap, (b - a) / 2), p)
    return out


# Pesos da escolha da melodia (heurística; ajuste de ouvido). Pontuação = amplitude
# + ALTURA*altura + CONTINUIDADE*proximidade da nota anterior + bônus por continuar a mesma nota.
W_HIGH, W_CONT, W_STAY = 0.5, 0.15, 0.05


def _score(notes: list[Note], i: int, prev: int | None) -> float:
    n = notes[i]
    score = n.amp + W_HIGH * min(max((n.pitch - 40) / 40, 0), 1)
    if prev is not None:
        score += W_CONT * (1 - min(abs(n.pitch - notes[prev].pitch), 12) / 12)
        score += W_STAY if i == prev else 0
    return score


def melody_line(
    notes: list[Note], min_note: float = 0.06, gap: float = 0.02, merge_gap: float = 0.02
) -> list[tuple]:
    """Uma voz por instante, escolhida por pontuação (amplitude, altura, continuidade).

    Melhor que "a mais aguda" em mixagem transcrita: ignora chiados fracos agudos (hi-hat) e
    não pula entre notas distantes sem motivo. Depois junta fragmentos da mesma altura
    separados por menos de `merge_gap` s (uma nota quebrada pelo transcritor).
    """
    if not notes:
        return []
    cuts = sorted({t for n in notes for t in (n.start, n.end)})
    segments, prev = [], None  # (início, fim, id da nota)
    for a, b in pairwise(cuts):
        mid = (a + b) / 2
        active = [i for i, n in enumerate(notes) if n.start <= mid < n.end]
        if not active:
            continue

        best = max(active, key=lambda i: (_score(notes, i, prev), notes[i].pitch, -i))
        if segments and segments[-1][1] == a and segments[-1][2] == best:
            segments[-1] = (segments[-1][0], b, best)
        else:
            segments.append((a, b, best))
        prev = best
    out = [(a, b, (notes[i].pitch,)) for a, b, i in segments]
    return finish(merge_same_pitch(out, merge_gap), min_note, gap)


def merge_same_pitch(segments: list[tuple], merge_gap: float) -> list[tuple]:
    """Junta trechos da mesma altura separados por 0 < silêncio < `merge_gap` s.

    Trechos que encostam (silêncio 0) são notas repetidas de propósito e ficam separados.
    """
    out = []
    for seg in segments:
        if out and out[-1][2] == seg[2] and 1e-6 < seg[0] - out[-1][1] < merge_gap:
            out[-1] = (out[-1][0], seg[1], seg[2])
        else:
            out.append(seg)
    return out


def best_shift(segments: list[tuple], min_hz: float, max_hz: float, max_shift: int = 24) -> int:
    """Semitons (inteiro) para transpor a música INTEIRA e caber o máximo de tempo na faixa.

    Melhor que dobrar nota a nota: mantém o contorno da melodia (só muda a tonalidade).
    Prefere deslocar só por oitavas (preserva a tonalidade); semitons só se couber >5% mais tempo.
    Desempate: menor |deslocamento| (0 se a música já cabe).
    """

    def in_range(shift: int) -> float:
        return sum(b - a for a, b, (p,) in segments if min_hz <= midi_to_hz(p + shift) <= max_hz)

    def best(shifts):
        return max(shifts, key=lambda s: (round(in_range(s), 3), -abs(s)))

    anywhere = best(range(-max_shift, max_shift + 1))
    octave = best(range(-max_shift // 12 * 12, max_shift // 12 * 12 + 1, 12))
    # prefere só mudar de oitava (mantém a tonalidade) se perder menos de 5% do tempo
    return octave if in_range(octave) >= in_range(anywhere) * 0.95 else anywhere


def transpose(segments: list[tuple], shift: int) -> list[tuple]:
    return [(a, b, tuple(p + shift for p in ps)) for a, b, ps in segments]


def out_of_range_fraction(segments: list[tuple], min_hz: float, max_hz: float) -> float:
    """Fração do tempo de som em notas fora de [min_hz, max_hz] (antes de dobrar oitavas)."""
    total = sum(b - a for a, b, _ in segments)
    bad = sum(b - a for a, b, (p, *_) in segments if not min_hz <= midi_to_hz(p) <= max_hz)
    return bad / total if total else 0.0


def median_note_ms(segments: list[tuple]) -> float:
    durs = sorted(b - a for a, b, _ in segments)
    return 1000 * durs[len(durs) // 2] if durs else 0.0


def contour_fold(segments: list[tuple], min_hz: float, max_hz: float) -> list[tuple]:
    """Escolhe a oitava de cada nota (1 voz) pelo contorno: a mais próxima da nota anterior.

    Dobrar nota a nota (fold_to_range) cria saltos de oitava falsos quando a melodia cruza
    a borda da faixa. A primeira nota usa fold_to_range; as seguintes ficam, dentro da faixa,
    na oitava que menos pula em relação à anterior. Preserva a classe de altura.
    """
    out, prev = [], None
    for a, b, (p,) in segments:
        if prev is None:
            q = round(69 + 12 * np.log2(fold_to_range(p, min_hz, max_hz) / 440.0))
        else:
            cands = [
                p + 12 * k for k in range(-10, 11) if min_hz <= midi_to_hz(p + 12 * k) <= max_hz
            ]
            q = min(cands, key=lambda c: (abs(c - prev), c))
        out.append((a, b, (int(q),)))
        prev = q
    return out


def fold_to_range(pitch: int, min_hz: float, max_hz: float) -> float:
    """Move a nota de oitava até caber em [min_hz, max_hz]. Exige faixa >= 1 oitava."""
    if max_hz < 2 * min_hz:
        raise ValueError(f"faixa {min_hz:g}-{max_hz:g} Hz tem menos de 1 oitava")
    f = midi_to_hz(pitch)
    while f < min_hz:
        f *= 2
    while f > max_hz:
        f /= 2
    return f


def render(
    segments: list[tuple], min_hz: float, max_hz: float, peak_db: float = -4.0
) -> np.ndarray:
    """Onda quadrada, uma frequência (ou soma de vozes) por segmento; silêncio fora das notas."""
    if not segments:
        raise ValueError("nenhuma nota encontrada")
    total = int(np.ceil(segments[-1][1] * RATE)) + 1
    out = np.zeros(total)
    ramp = int(0.002 * RATE)  # 2 ms evita estalo na troca de nota
    for start, end, pitches in segments:
        i, j = int(start * RATE), int(end * RATE)
        t = np.arange(i, j) / RATE  # tempo absoluto: fase contínua entre segmentos iguais
        wave = sum(
            np.where(np.sin(2 * np.pi * fold_to_range(p, min_hz, max_hz) * t) >= 0, 1.0, -1.0)
            for p in pitches
        )
        wave = wave / len(pitches)
        r = min(ramp, len(wave) // 2)
        if r:
            wave[:r] *= np.linspace(0, 1, r)
            wave[-r:] *= np.linspace(1, 0, r)
        out[i:j] = wave
    return out * 10 ** (peak_db / 20)


def overlap_fraction(notes: list[Note]) -> float:
    """Fração do tempo com notas soando em que há 2 ou mais notas ao mesmo tempo."""
    cuts = sorted({t for n in notes for t in (n.start, n.end)})
    active = poly = 0.0
    for a, b in pairwise(cuts):
        mid = (a + b) / 2
        count = sum(n.start <= mid < n.end for n in notes)
        if count:
            active += b - a
            if count > 1:
                poly += b - a
    return poly / active if active else 0.0


def notes_from(path: Path, min_note_ms: float = 80.0) -> list[Note]:
    if path.suffix.lower() in MIDI_EXTS:
        return read_midi(path)
    return transcribe_audio(path, min_note_ms)
