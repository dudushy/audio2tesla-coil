"""Modo melodia: notas (de .mid ou de áudio) -> 1 voz -> faixa da bobina -> onda quadrada."""

import math
import os
import re
import tempfile
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import numpy as np

from .ffmpeg import decode_mono, run_ffmpeg

RATE = 44100
MIDI_EXTS = {".mid", ".midi"}
DRUM_CHANNEL = 9  # canal 10 do MIDI (índice 9) é percussão


@dataclass(frozen=True)
class Note:
    start: float  # s
    end: float  # s
    pitch: int  # número MIDI
    amp: float = 1.0  # 0..1; força da nota (amplitude do Basic Pitch, velocity do .mid)
    part: int = 0  # .mid: trilha*16+canal de origem (ver MidiPart); 0 no áudio


@dataclass
class MidiPart:
    """Notas de um canal de uma trilha do .mid. Cada instrumento (melodia, baixo, acordes...) vira uma."""

    id: int  # trilha*16+canal
    name: str
    notes: list[Note] = field(default_factory=list)


def midi_to_hz(pitch: float) -> float:
    return 440.0 * 2 ** ((pitch - 69) / 12)


def read_midi_parts(path: Path) -> list[MidiPart]:
    """Lê um .mid separando por trilha+canal (percussão ignorada), na ordem em que aparecem.

    Tempo -> segundos usa o mapa de tempos do arquivo inteiro (set_tempo vive na trilha 0).
    Nota repetida sem note_off religa (fecha a anterior); nota sem note_off fecha no fim do arquivo.
    """
    import mido

    try:
        mid = mido.MidiFile(str(path))
    except (OSError, ValueError, EOFError) as e:
        raise ValueError(f"MIDI inválido: {path} ({e})") from None
    tpb = mid.ticks_per_beat
    if not tpb:
        raise ValueError("MIDI com tempo em SMPTE não é suportado")
    changes = sorted(
        {(t, m.tempo) for t, m in _absolute(mid) if m.type == "set_tempo"}, key=lambda c: c[0]
    )
    ticks, secs, tempos = [0], [0.0], [500000]  # padrão do MIDI: 120 bpm
    for tick, tempo in changes:
        if tick == ticks[-1]:
            tempos[-1] = tempo
            continue
        secs.append(secs[-1] + (tick - ticks[-1]) * tempos[-1] / 1e6 / tpb)
        ticks.append(tick)
        tempos.append(tempo)

    def to_sec(tick: int) -> float:
        i = bisect_right(ticks, tick) - 1
        return secs[i] + (tick - ticks[i]) * tempos[i] / 1e6 / tpb

    parts: dict[int, MidiPart] = {}
    for ti, track in enumerate(mid.tracks):
        title = next((m.name.strip() for m in track if m.type == "track_name" and m.name), "")
        active: dict[tuple[int, int], tuple[float, int]] = {}
        tick = 0
        last = 0

        def close(key, end_tick, active=active, ti=ti, title=title):
            start, vel = active.pop(key)
            end = to_sec(end_tick)
            if end > start:
                pid = ti * 16 + key[0]
                part = parts.setdefault(pid, MidiPart(pid, _part_name(title, ti, key[0])))
                part.notes.append(Note(start, end, key[1], vel / 127, pid))

        for msg in track:
            tick += msg.time
            last = tick
            if msg.type not in ("note_on", "note_off") or msg.channel == DRUM_CHANNEL:
                continue
            key = (msg.channel, msg.note)
            if msg.type == "note_on" and msg.velocity > 0:
                if key in active:
                    close(key, tick)
                active[key] = (to_sec(tick), msg.velocity)
            elif key in active:
                close(key, tick)
        for key in list(active):
            close(key, last)
    for part in parts.values():
        part.notes.sort(key=lambda n: (n.start, n.pitch))
    return sorted(parts.values(), key=lambda p: p.id)


def _absolute(mid):
    """(tick absoluto, mensagem) de todas as trilhas."""
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            yield tick, msg


def _part_name(title: str, track: int, channel: int) -> str:
    return f"{title or f'Trilha {track + 1}'} (canal {channel + 1})"


def read_midi(path: Path) -> list[Note]:
    """Todas as notas do .mid (todas as trilhas misturadas, sem percussão), por início."""
    notes = [n for p in read_midi_parts(path) for n in p.notes]
    return sorted(notes, key=lambda n: (n.start, n.pitch))


# "usar todas as trilhas": o comportamento antigo (mistura tudo e deixa a heurística)
ALL_PARTS = -1
_MELODY_NAME = re.compile(
    r"melod|lead|vocal|voice|voz|solo|sing|canto|main|principal", re.IGNORECASE
)
_BACKING_NAME = re.compile(
    r"bass|baix|drum|bater|perc|pad|chord|acord|comp|rhythm|rit", re.IGNORECASE
)


def chordiness(notes: list[Note]) -> float:
    """Fração dos "momentos de ataque" que são acordes (3+ notas começando juntas, ~30 ms).

    Pads e acompanhamento em acordes dão ~1; melodia/riff dá perto de 0, mesmo com 1-2 notas
    de reforço (baixo dobrando) aqui e ali.
    """
    onsets: dict[int, int] = {}
    for n in notes:
        k = round(n.start / 0.03)
        onsets[k] = onsets.get(k, 0) + 1
    return sum(c >= 3 for c in onsets.values()) / len(onsets) if onsets else 0.0


def part_score(part: MidiPart) -> float:
    """Quão provável é a trilha ser a melodia (heurística; o usuário pode trocar na UI).

    Melodia = toca bastante, não é só acorde, em registro médio/agudo e com variedade de alturas
    (baixo e arpejos repetem poucas notas; pads são acordes). O nome da trilha ajuda.
    """
    notes = part.notes
    if not notes:
        return 0.0
    sounding, _ = coverage(notes)
    pitches = sorted(n.pitch for n in notes)
    median = pitches[len(pitches) // 2]
    register = min(max((median - 40) / 24, 0.15), 1.0)
    variety = min(len(set(pitches)) / 8, 1.0)
    name = 1.5 if _MELODY_NAME.search(part.name) else 0.5 if _BACKING_NAME.search(part.name) else 1
    return sounding * (1 - chordiness(notes)) ** 2 * register * variety * name


def pick_part(parts: list[MidiPart]) -> MidiPart:
    """Trilha mais provável de ser a melodia; empate vai para a primeira (convenção dos .mid)."""
    return max(parts, key=lambda p: (round(part_score(p), 6), -p.id))


def select_part(parts: list[MidiPart], part: int | None) -> MidiPart:
    """`part` None = automático; ALL_PARTS = todas misturadas; senão o id (trilha*16+canal)."""
    if not parts:
        raise ValueError("nenhuma nota encontrada no MIDI")
    if part is None:
        return pick_part(parts)
    if part == ALL_PARTS:
        notes = sorted((n for p in parts for n in p.notes), key=lambda n: (n.start, n.pitch))
        return MidiPart(ALL_PARTS, "Todas as trilhas", notes)
    for p in parts:
        if p.id == part:
            return p
    raise ValueError(f"trilha {part} não existe neste MIDI")


def silent_regions(notes: list[Note], end: float, min_gap: float) -> list[tuple[float, float]]:
    """Trechos de [0, end] sem nenhuma nota por pelo menos `min_gap` s (pausas curtas não contam)."""
    regions, cursor = [], 0.0
    for n in sorted(notes, key=lambda n: n.start):
        if n.start - cursor >= min_gap:
            regions.append((cursor, n.start))
        cursor = max(cursor, n.end)
    if end - cursor >= min_gap:
        regions.append((cursor, end))
    return regions


def combine_parts(
    parts: list[MidiPart], min_gap: float = 1.0, rel: float = 0.4
) -> tuple[list[Note], dict[int, float]]:
    """Melodia de várias trilhas: a melhor trilha manda; as outras só preenchem as pausas dela.

    Num .mid real a melodia costuma passar de um instrumento para outro (riff de sintetizador,
    voz, ponte...), então uma trilha só deixa buracos onde ela cala. Aqui a trilha mais provável
    de ser a melodia fica inteira; as demais que também parecem melodia (pontuação >= `rel` x a
    da melhor; baixo e pads ficam de fora) entram só dentro de pausas de >= `min_gap` s, cortadas
    nas bordas da pausa: nunca interrompem a trilha principal, e o resultado continua em 1 voz.
    Retorna (notas, segundos soando por trilha).
    """
    ranked = sorted(((part_score(p), p) for p in parts), key=lambda t: (-t[0], t[1].id))
    ranked = [(sc, p) for sc, p in ranked if sc > 0]
    if not ranked:
        raise ValueError("nenhuma nota encontrada no MIDI")
    end = max(n.end for _, p in ranked for n in p.notes)
    best = ranked[0][0]
    notes = list(ranked[0][1].notes)
    used = {ranked[0][1].id: coverage(notes)[0]}
    for score, part in ranked[1:]:
        if score < rel * best:
            continue
        added = []
        for lo, hi in silent_regions(notes, end, min_gap):
            for n in part.notes:
                start, stop = max(n.start, lo), min(n.end, hi)
                if stop - start >= 0.02:
                    added.append(Note(start, stop, n.pitch, n.amp, n.part))
        if added:
            used[part.id] = coverage(added)[0]
            notes += added
    return sorted(notes, key=lambda n: (n.start, n.pitch)), used


def coverage(notes: list[Note]) -> tuple[float, float]:
    """(tempo com alguma nota soando, fração desse tempo com 2+ notas), por varredura O(n log n)."""
    events = sorted([(n.start, 1) for n in notes] + [(n.end, -1) for n in notes])
    sounding = poly = 0.0
    count, prev = 0, 0.0
    for t, d in events:
        if count:
            sounding += t - prev
            if count > 1:
                poly += t - prev
        count += d
        prev = t
    return sounding, (poly / sounding if sounding else 0.0)


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


def drop_ghost_notes(notes: list[Note], rel: float = 0.25) -> list[Note]:
    """Tira notas de .mid com velocity < `rel` x a mediana da própria trilha (ruído/notas fantasma)."""
    by_part: dict[int, list[float]] = {}
    for n in notes:
        by_part.setdefault(n.part, []).append(n.amp)
    floor = {k: rel * sorted(v)[len(v) // 2] for k, v in by_part.items()}
    return [n for n in notes if n.amp >= floor[n.part]]


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


LOW_STRAY = 15  # semitons abaixo da mediana da melodia: é o baixo vazando, não a melodia


def lead_line(notes: list[Note], min_note: float = 0.06, gap: float = 0.02) -> list[tuple]:
    """`melody_line` em duas passadas, para notas transcritas de áudio.

    Quando a melodia cala (ou o baixo está forte), a 1ª passada pega notas do baixo, 2+ oitavas
    abaixo, e a bobina salta para o grave. A 2ª passada refaz a escolha sem as notas mais de
    `LOW_STRAY` semitons abaixo da mediana (ponderada pelo tempo) do que a 1ª escolheu.
    """
    segments = melody_line(notes, min_note, gap)
    if not segments:
        return segments
    floor = _weighted_pitch(segments, 0.5) - LOW_STRAY
    kept = [n for n in notes if n.pitch >= floor]
    return melody_line(kept, min_note, gap) if len(kept) < len(notes) else segments


def _weighted_pitch(segments: list[tuple], q: float) -> int:
    """Altura no quantil `q` (0..1) do tempo de som: 0.5 = mediana ponderada pela duração."""
    by_pitch = sorted((p[0], b - a) for a, b, p in segments)
    target, acc = q * sum(d for _, d in by_pitch), 0.0
    for pitch, dur in by_pitch:
        acc += dur
        if acc >= target:
            return pitch
    return by_pitch[-1][0]


MIN_STEP = (
    0.14  # s; passo mínimo da grade: nota mais curta que a bobina articula bem (Mario ~140 ms)
)
GRID_FILL = 0.4  # fração da célula com melodia para ela virar nota
GRID_SEARCH = 0.04  # ± fração do passo em que o tempo do rastreador é refinado
GRID_GATE = 0.68  # fração da célula que soa; o resto é a pausa que rearticula a nota seguinte


def beat_period(path: Path) -> float | None:
    """Duração da batida (s) do áudio, pelo rastreador do librosa (dependência do Basic Pitch).

    Só o nível métrico importa aqui (o valor exato é refinado em `tempo_grid`). None se o librosa
    não está instalado ou não achou batida.
    """
    try:
        import librosa
    except ImportError:
        return None
    tempo, _ = librosa.beat.beat_track(y=decode_mono(path, 22050), sr=22050)
    bpm = float(np.atleast_1d(tempo)[0])
    return 60.0 / bpm if bpm > 0 else None


def tempo_grid(notes: list[Note], beat: float) -> tuple[float, float] | None:
    """(passo, fase) em s da grade rítmica das notas transcritas, ou None se não há pulso firme.

    O passo é a subdivisão binária de `beat` que cai em [MIN_STEP, 2*MIN_STEP): colcheia a
    126 BPM (238 ms), semicolcheia a 100 BPM (150 ms). `beat` vem de um rastreador de batidas e
    tem erro de até ~3% (ele mede em quadros de 23 ms), o que em 1 min de música vira várias notas
    de atraso; por isso o passo é refinado (±`GRID_SEARCH`) e a fase escolhida pelos inícios das
    próprias notas: o melhor passo é o que
    mais concentra os inícios (peso = amplitude) num ponto do ciclo, somando o nível acima (2x).
    Música sem tempo constante espalha os inícios pelo ciclo e devolve None (fica como transcrita).
    """
    if len(notes) < 16 or not beat or beat <= 0:
        return None
    step = beat
    while step < MIN_STEP:
        step *= 2
    while step >= 2 * MIN_STEP:
        step /= 2
    onsets = np.array([n.start for n in notes])
    weights = np.array([n.amp for n in notes])
    span = max(float(onsets.max() - onsets.min()), 1.0)
    cands, s = [], step * (1 - GRID_SEARCH)
    while s < step * (1 + GRID_SEARCH):
        cands.append(s)
        s += 0.1 * s * s / span  # passo fino: no máximo 1/10 de ciclo de deriva no fim da música
    cands = np.array(cands)

    def concentration(steps: np.ndarray) -> np.ndarray:
        phases = np.exp(2j * np.pi * onsets[None, :] / steps[:, None])
        return (weights[None, :] * phases).sum(axis=1) / weights.sum()

    z = concentration(cands)
    best = int(np.argmax(np.abs(z) + np.abs(concentration(2 * cands))))
    if abs(z[best]) < 4 / math.sqrt(len(notes)):  # ~4 desvios acima de inícios ao acaso
        return None
    step = float(cands[best])
    return step, float(np.angle(z[best]) / (2 * np.pi) * step) % step


def to_grid(segments: list[tuple], step: float, phase: float) -> list[tuple]:
    """Encaixa a melodia (1 voz) na grade: uma nota por célula, no início da célula.

    A transcrição de áudio erra o início das notas em dezenas de ms e quebra/junta notas ao acaso;
    numa onda quadrada isso soa como ritmo torto. Aqui cada célula [fase + k*passo, +passo) com
    melodia em pelo menos `GRID_FILL` dela vira uma nota com a altura que mais soa na célula,
    durando `GRID_GATE` da célula. Também vira nota a célula em cujo ponto de grade mais próximo
    começa uma nota (mesmo curta, >= 60 ms): o Basic Pitch encurta notas rápidas e uma nota com
    ataque claro não pode sumir só por soar pouco. Nota longa vira notas repetidas (o arco
    rearticula a cada célula), fragmento sem ataque na célula some, e todo início cai na grade.
    """
    if not segments:
        return []
    out = []
    starts = [s for s, _, _ in segments]
    k = math.floor((segments[0][0] - phase) / step)
    end = segments[-1][1]
    i = 0
    while phase + k * step < end:
        a = phase + k * step
        b = a + step
        while i < len(segments) and segments[i][1] <= a:
            i += 1
        sounding: dict[int, float] = {}
        for s, e, p in segments[i:]:
            if s >= b:
                break
            sounding[p[0]] = sounding.get(p[0], 0.0) + min(e, b) - max(s, a)
        # ataque perto deste ponto da grade (mais perto dele que dos vizinhos)
        lo, hi = bisect_left(starts, a - step / 2), bisect_left(starts, a + step / 2)
        struck = [p[0] for s, e, p in segments[lo:hi] if e - s >= 0.06]
        start = max(a, 0.0)
        if sounding and (sum(sounding.values()) >= GRID_FILL * step or struck):
            pitch = max(sounding, key=lambda p: (sounding[p], p))
            out.append((start, a + GRID_GATE * step, (pitch,)))
        elif struck:  # nota curta que começa logo antes do ponto e termina antes dele
            out.append((start, a + GRID_GATE * step, (struck[-1],)))
        k += 1
    return [seg for seg in out if seg[1] - seg[0] >= 0.02]


SHIFT_SLACK = 0.02  # deslocamentos que cabem quase o mesmo tempo (2%) contam como empate


def best_shift(segments: list[tuple], min_hz: float, max_hz: float, max_shift: int = 24) -> int:
    """Semitons (múltiplo de 12) para transpor a música INTEIRA para a faixa da bobina.

    Só oitavas: preserva todas as classes de altura (a tonalidade). 1º critério: caber o máximo de
    tempo na faixa (o que sobra fora é dobrado depois por `contour_fold`). Entre os deslocamentos
    que cabem (quase) o mesmo, ganha o que deixa a tessitura da melodia (meio entre os quantis
    5% e 95% do tempo) mais perto do centro geométrico da faixa: a bobina soa mais clara no meio
    do que nas bordas, e uma melodia em 400-930 Hz que cabe inteira em 200-465 Hz desce uma oitava.
    Desempate final: menor |deslocamento|.
    """
    if not segments:
        return 0

    def in_range(shift: int) -> float:
        return sum(b - a for a, b, (p,) in segments if min_hz <= midi_to_hz(p + shift) <= max_hz)

    reach = max_shift // 12 * 12
    shifts = range(-reach, reach + 1, 12)
    fits = {s: in_range(s) for s in shifts}
    total = sum(b - a for a, b, _ in segments)
    good = [s for s in shifts if fits[s] >= max(fits.values()) - SHIFT_SLACK * total]
    middle = (_weighted_pitch(segments, 0.05) + _weighted_pitch(segments, 0.95)) / 2
    center = 69 + 12 * math.log2(math.sqrt(min_hz * max_hz) / 440)
    return min(good, key=lambda s: (round(abs(middle + s - center), 3), abs(s)))


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


OCTAVE_COST = 1.0  # custo de tirar uma nota da oitava original (1 por oitava) no contour_fold
LEAP_COST = 12.0  # custo de mudar o deslocamento de oitava entre duas notas (= 1 oitava de salto)


def contour_fold(
    segments: list[tuple], min_hz: float, max_hz: float, allowed: tuple[int, ...] = ()
) -> list[tuple]:
    """Escolhe a oitava de cada nota (1 voz) para caber na faixa sem quebrar a melodia.

    Preserva a classe de altura. Minimiza (por programação dinâmica, ótimo exato): saltos de
    oitava introduzidos entre notas vizinhas (LEAP_COST por oitava) + notas fora da oitava
    original (OCTAVE_COST). Assim, nota dentro da faixa não sai do lugar sem motivo, e quando um
    trecho cruza a borda a frase inteira sobe/desce junto em vez de uma nota virar um salto falso.
    (A versão antiga ia nota a nota pela "oitava mais próxima da anterior": um erro se acumulava e
    35% das notas da Blue saíam uma oitava fora, mesmo com só 3% do tempo fora da faixa.)

    `allowed` (notas ouvidas na calibragem): só usa oitavas em que a nota está na lista; se
    nenhuma oitava dela foi ouvida, vale qualquer uma dentro da faixa.
    """
    if not segments:
        return []
    if max_hz < 2 * min_hz:
        raise ValueError(f"faixa {min_hz:g}-{max_hz:g} Hz tem menos de 1 oitava")
    options = []  # por nota: deslocamentos k (em oitavas) que cabem na faixa
    for _, _, (p, *_) in segments:
        ks = [k for k in range(-10, 11) if min_hz <= midi_to_hz(p + 12 * k) <= max_hz]
        options.append([k for k in ks if p + 12 * k in allowed] or ks)
    cost = {k: OCTAVE_COST * abs(k) for k in options[0]}
    back = []
    for ks in options[1:]:
        new, prev_of = {}, {}
        for k in ks:
            best = min(cost, key=lambda j: cost[j] + LEAP_COST * abs(k - j))
            new[k] = cost[best] + LEAP_COST * abs(k - best) + OCTAVE_COST * abs(k)
            prev_of[k] = best
        cost = new
        back.append(prev_of)
    k = min(cost, key=lambda j: (cost[j], abs(j)))
    chosen = [k]
    for prev_of in reversed(back):
        k = prev_of[k]
        chosen.append(k)
    chosen.reverse()
    return [(a, b, (p + 12 * k,)) for (a, b, (p, *_)), k in zip(segments, chosen, strict=True)]


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


def square(freq: float, t: np.ndarray, duty: float = 0.5) -> np.ndarray:
    """Onda quadrada em ±1 com ciclo de trabalho `duty` (0.5 = simétrica)."""
    if duty == 0.5:
        return np.where(np.sin(2 * np.pi * freq * t) >= 0, 1.0, -1.0)
    return np.where((freq * t) % 1.0 < duty, 1.0, -1.0)


def gated(segments: list[tuple], gate: float, min_on: float = 0.03) -> list[tuple]:
    """Mantém só a fração `gate` do início de cada trecho (staccato); 1.0 = legato, como veio.

    Nunca deixa o trecho com menos de `min_on` s (ou o trecho inteiro, se já for menor).
    """
    if gate >= 1.0:
        return segments
    return [(a, a + max(gate * (b - a), min(b - a, min_on)), p) for a, b, p in segments]


def render(
    segments: list[tuple],
    min_hz: float,
    max_hz: float,
    peak_db: float = -4.0,
    duty: float = 0.5,
    gate: float = 1.0,
    rate: int = RATE,
) -> np.ndarray:
    """Onda quadrada, uma frequência (ou soma de vozes) por segmento; silêncio fora das notas.

    `duty` é o ciclo de trabalho e `gate` a fração de cada nota que soa (ver `gated`); o padrão
    (0.5, 1.0) é a onda que a bobina já tocou bem. Com duty != 0.5 tira-se o nível DC (o
    transformador/placa não passa DC) e o pico é normalizado, então o resultado é pulso estreito e alto.
    """
    if not segments:
        raise ValueError("nenhuma nota encontrada")
    end = segments[-1][1]
    segments = gated(segments, gate)
    total = int(np.ceil(end * rate)) + 1
    out = np.zeros(total)
    ramp = int(0.002 * rate)  # 2 ms evita estalo na troca de nota
    for start, stop, pitches in segments:
        i, j = int(start * rate), int(stop * rate)
        t = np.arange(i, j) / rate  # tempo absoluto: fase contínua entre segmentos iguais
        wave = sum(square(fold_to_range(p, min_hz, max_hz), t, duty) for p in pitches)
        wave = wave / len(pitches)
        if duty != 0.5:
            wave = (wave - (2 * duty - 1)) / (2 * max(duty, 1 - duty))
        r = min(ramp, len(wave) // 2)
        if r:
            wave[:r] *= np.linspace(0, 1, r)
            wave[-r:] *= np.linspace(1, 0, r)
        out[i:j] = wave
    return out * 10 ** (peak_db / 20)


def overlap_fraction(notes: list[Note]) -> float:
    """Fração do tempo com notas soando em que há 2 ou mais notas ao mesmo tempo."""
    return coverage(notes)[1]


def notes_from(path: Path, min_note_ms: float = 80.0, part: int | None = None) -> list[Note]:
    if path.suffix.lower() in MIDI_EXTS:
        return select_part(read_midi_parts(path), part).notes
    return transcribe_audio(path, min_note_ms)


def clip_notes(notes: list[Note], start: float, end: float | None) -> list[Note]:
    """Só o trecho [start, end) s das notas, com o tempo recontado a partir de `start`."""
    stop = math.inf if end is None else end
    out = []
    for n in notes:
        a, b = max(n.start, start), min(n.end, stop)
        if b - a >= 0.02:
            out.append(Note(a - start, b - start, n.pitch, n.amp, n.part))
    return out


MIDI_PPQ = 480
SQUARE_LEAD = 80  # programa General MIDI "Lead 1 (square)"


def write_midi(path: Path, segments: list[tuple], bpm: float | None = None) -> None:
    """Grava a melodia (1 voz) como .mid: 1 trilha, canal 1, sem pitch bend, tempo fixo `bpm`.

    Os tempos em segundos são preservados: `bpm` (padrão 120) só define como o editor que abrir o
    arquivo desenha os compassos.
    """
    import mido

    bpm = bpm or 120.0
    ticks = lambda sec: round(sec * MIDI_PPQ * bpm / 60)
    events = []
    for a, b, p in segments:
        on, off = ticks(a), max(ticks(b), ticks(a) + 1)
        events += [(on, 1, "note_on", p[0]), (off, 0, "note_off", p[0])]
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Coil melody", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    track.append(mido.Message("program_change", program=SQUARE_LEAD, time=0))
    now = 0
    for tick, _, kind, pitch in sorted(events):  # note_off antes de note_on no mesmo tick
        track.append(
            mido.Message(
                kind, note=pitch, velocity=100 if kind == "note_on" else 0, time=tick - now
            )
        )
        now = tick
    mid = mido.MidiFile(type=0, ticks_per_beat=MIDI_PPQ)
    mid.tracks.append(track)
    mid.save(str(path))
