"""Note Block do Minecraft: arquivos .nbs (Open Note Block Studio) e a conversão "estilo Minecraft".

Um note block toca uma nota curta (batida, sem sustentar), só nos tiques do jogo (10 por segundo
em redstone) e só em 25 notas: F#3 a F#5 na harpa. As músicas de fãs são arranjos feitos à mão
nesse formato; aqui elas são lidas como as trilhas de um .mid (cada camada vira uma trilha).

Os conversores automáticos de áudio para .nbs medem a nota mais forte em cada tique (FFT) e
encaixam tudo nas 25 notas; `fft_notes` + `noteblock_style` fazem o mesmo.
"""

from pathlib import Path

import numpy as np

from .ffmpeg import decode_mono
from .midi import MIDI_EXTS, MidiPart, Note, read_midi_parts

NBS_EXTS = {".nbs"}
NOTE_EXTS = MIDI_EXTS | NBS_EXTS  # arquivos que já trazem as notas (sem transcrever áudio)
TICK = 0.1  # s; 10 tiques por segundo, o ritmo da redstone
MC_LOW, MC_HIGH = 54, 78  # F#3 a F#5: as 25 notas de um note block (harpa)
KEY_TO_MIDI = 21  # tecla 0 do .nbs = A0 (MIDI 21); tecla 33 = F#3
NB_MAX = 0.3  # s; o som do note block some rápido: a nota não sustenta mais que isso
NB_GAP = 0.02  # s de silêncio antes da nota seguinte (cada nota é uma batida)
# soma harmônica de `fft_notes`: pesos dos 5 primeiros harmônicos e energia mínima da fundamental
# (calibrados em 11 gravações reais: 81% das batidas na nota certa; com 3 harmônicos, 75% e
# voz grave em celular, de fundamental fraca, saía uma oitava acima)
HARMONICS = ((1, 1.0), (2, 0.9), (3, 0.8), (4, 0.7), (5, 0.6))
FUNDAMENTAL_FLOOR = 0.05
MAX_RING = 0.5  # s; ao ler um .nbs, quanto uma nota dura no máximo (antes da próxima da camada)

# oitavas de cada instrumento em relação à harpa (o .nbs guarda a tecla como se fosse harpa)
INSTRUMENT_OCTAVE = {
    0: 0,  # harpa
    1: -2,  # contrabaixo
    5: -1,  # violão
    6: 1,  # flauta
    7: 2,  # sino
    8: 2,  # carrilhão
    9: 2,  # xilofone
    10: 0,  # xilofone de ferro
    11: 1,  # chocalho de vaca
    12: -2,  # didgeridoo
    13: 0,  # bit
    14: 0,  # banjo
    15: 0,  # pling
}
PERCUSSION = {2, 3, 4}  # bumbo, caixa, clique: sem altura, ficam de fora como a bateria do .mid
INSTRUMENT_NAMES = {
    0: "harpa", 1: "contrabaixo", 5: "violão", 6: "flauta", 7: "sino", 8: "carrilhão",
    9: "xilofone", 10: "xilofone de ferro", 11: "chocalho", 12: "didgeridoo", 13: "bit",
    14: "banjo", 15: "pling",
}  # fmt: skip


def _pynbs():
    try:
        import pynbs
    except ImportError as e:
        raise RuntimeError(f"pynbs não instalado ({e}): pip install pynbs") from None
    return pynbs


def read_nbs_parts(path: Path) -> list[MidiPart]:
    """Lê um .nbs: cada camada + instrumento vira uma trilha (como trilha+canal num .mid)."""
    pynbs = _pynbs()
    try:
        song = pynbs.read(str(path))
    except Exception as e:  # noqa: BLE001 - arquivo corrompido gera erros variados no parser
        raise ValueError(f".nbs inválido: {path.name} ({e!r})") from None
    tempo = song.header.tempo if song.header.tempo and song.header.tempo > 0 else 10.0
    names = {layer.id: layer.name.strip() for layer in song.layers}
    by_part: dict[int, list] = {}
    for n in song.notes:
        if n.instrument in PERCUSSION:
            continue
        by_part.setdefault(n.layer * 16 + min(n.instrument, 15), []).append(n)
    parts = []
    for pid, raw in sorted(by_part.items()):
        raw.sort(key=lambda n: n.tick)
        layer, inst = divmod(pid, 16)
        octave = INSTRUMENT_OCTAVE.get(raw[0].instrument, 0)
        notes = []
        for i, n in enumerate(raw):
            start = n.tick / tempo
            nxt = next((m.tick / tempo for m in raw[i + 1 :] if m.tick > n.tick), None)
            end = start + MAX_RING if nxt is None else min(nxt, start + MAX_RING)
            pitch = n.key + KEY_TO_MIDI + 12 * octave + n.pitch / 100
            pitch = int(pitch) if float(pitch).is_integer() else round(pitch, 2)
            notes.append(Note(start, end, pitch, n.velocity / 100, pid))
        title = names.get(layer) or f"Camada {layer + 1}"
        parts.append(MidiPart(pid, f"{title} ({INSTRUMENT_NAMES.get(inst, 'instrumento')})", notes))
    return parts


def read_parts(path: Path) -> list[MidiPart]:
    """Trilhas de um arquivo de notas (.mid ou .nbs)."""
    return read_nbs_parts(path) if path.suffix.lower() in NBS_EXTS else read_midi_parts(path)


def nbs_tick(path: Path) -> float:
    """Duração do tique de um .nbs (s); para .mid, o tique da redstone."""
    if path.suffix.lower() not in NBS_EXTS:
        return TICK
    tempo = _pynbs().read(str(path)).header.tempo
    return 1.0 / tempo if tempo and tempo > 0 else TICK


def fold_mc(pitch: float) -> float:
    """Leva a nota para as 25 notas do note block (F#3-F#5) só por oitavas."""
    while pitch < MC_LOW:
        pitch += 12
    while pitch > MC_HIGH:
        pitch -= 12
    return pitch


def noteblock_style(segments: list[tuple], tick: float = TICK) -> list[tuple]:
    """Regras do note block: início no tique, 25 notas, nota curta e batida (sem sustentar)."""
    struck: dict[int, tuple] = {}
    for a, _b, (p, *_) in segments:
        k = round(a / tick)
        struck.setdefault(k, (k * tick, fold_mc(p)))  # 2 notas no mesmo tique: fica a 1ª
    starts = sorted(struck.values())
    out = []
    for i, (a, p) in enumerate(starts):
        nxt = starts[i + 1][0] if i + 1 < len(starts) else None
        end = a + NB_MAX if nxt is None else min(a + NB_MAX, nxt - NB_GAP)
        if end - a >= 0.03:
            out.append((a, end, (p,)))
    return out


def fft_notes(path: Path, tick: float = TICK) -> list[tuple]:
    """Áudio -> uma nota por tique, como os conversores de áudio para .nbs.

    Em cada tique mede o espectro (FFT) e escolhe a frequência fundamental mais forte por soma
    harmônica (a nota cujos harmônicos também soam, para não pegar um harmônico no lugar dela). Uma nova batida sai quando a nota muda ou o som volta a crescer (nova sílaba/ataque).
    """
    rate = 22050
    y = decode_mono(path, rate)
    win = 4096
    hop = max(1, round(tick * rate))
    if len(y) < win:
        return []
    window = np.hanning(win)
    freqs = np.fft.rfftfreq(win, 1 / rate)
    bins = np.flatnonzero((freqs >= 50) & (freqs <= 2000))
    frames = range(0, len(y) - win + 1, hop)
    pitches, levels = [], []
    for i in frames:
        frame = y[i : i + win]
        spec = np.abs(np.fft.rfft(frame * window))
        near = np.maximum(spec, np.maximum(np.r_[0, spec[:-1]], np.r_[spec[1:], 0]))  # ±1 bin
        # soma harmônica: a fundamental é a que tem os harmônicos somando mais (pesos caindo);
        # robusta para som com poucos harmônicos (assobio), onde o produto harmônico falha
        score = sum(w * near[np.minimum(bins * h, len(near) - 1)] for h, w in HARMONICS)
        # a fundamental precisa ter um mínimo de energia: sem isso um harmônico vira "nota"
        score = np.where(near[bins] >= FUNDAMENTAL_FLOOR * near.max(), score, 0)
        k = int(bins[np.argmax(score)])
        if 0 < k < len(spec) - 1:  # interpolação parabólica: precisão abaixo de 1 bin nos graves
            a_, b_, c_ = np.log(spec[k - 1 : k + 2] + 1e-12)
            k = k + 0.5 * (a_ - c_) / (a_ - 2 * b_ + c_) if a_ - 2 * b_ + c_ < 0 else k
        f0 = k * rate / win
        pitches.append(69 + 12 * np.log2(f0 / 440) if f0 > 0 else np.nan)
        levels.append(float(np.sqrt(np.mean(frame**2))))
    levels = np.array(levels)
    # mediana de 3 tiques: um salto isolado de oitava (harmônico por um instante) não vira batida
    raw = np.array(pitches)
    for j in range(1, len(raw) - 1):
        trio = raw[j - 1 : j + 2]
        if np.isfinite(trio).all():
            pitches[j] = float(np.median(trio))
    loud = levels > levels.max() * 10 ** (-30 / 20) if len(levels) else levels
    out, prev, low, peak = [], None, 0.0, 0.0
    for j, i in enumerate(frames):
        p, level = pitches[j], levels[j]
        if not loud[j] or not np.isfinite(p):
            prev = None
            continue
        note = round(p)
        t = (i + win / 2) / rate  # centro da janela
        low = min(low, level)
        # nova batida: a nota mudou, ou o som enfraqueceu (< metade do pico) e voltou a crescer
        if note != prev or (low < 0.5 * peak and level > 1.5 * low):
            out.append((t, t + tick, (note,)))
            low = peak = level
        elif out:
            out[-1] = (out[-1][0], t + tick, out[-1][2])  # mesma nota continua soando
        peak = max(peak, level)
        prev = note
    return out


def write_nbs(path: Path, segments: list[tuple], name: str = "", tempo: float = 10.0) -> None:
    """Grava as notas como .nbs para o Minecraft: harpa, 25 notas, uma batida por nota.

    Notas fora de F#3-F#5 mudam de oitava (o jogo não toca fora disso sem pacote de recursos);
    a afinação fracionária vai no campo de ajuste fino (centésimos). Notas no mesmo tique ficam
    em camadas diferentes.
    """
    pynbs = _pynbs()
    song = pynbs.new_file(song_name=name[:60], tempo=tempo, song_origin="coil-prep")
    used: dict[int, int] = {}  # tique -> camadas ocupadas
    for a, _b, ps in sorted(segments):
        tick = max(0, round(a * tempo))
        for p in ps:
            p = fold_mc(float(p))
            key = round(p)
            layer = used.get(tick, 0)
            used[tick] = layer + 1
            fine = round((p - key) * 100)
            song.notes.append(pynbs.Note(tick, layer, 0, key - KEY_TO_MIDI, 100, 0, fine))
    layers = max(used.values(), default=1)
    song.layers = [
        pynbs.Layer(i, f"Melodia {i + 1}" if layers > 1 else "Melodia") for i in range(layers)
    ]
    song.notes.sort(key=lambda n: (n.tick, n.layer))
    song.save(str(path))
