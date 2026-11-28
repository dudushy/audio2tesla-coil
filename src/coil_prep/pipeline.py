"""Fluxo único: URL ou arquivo -> (stem) -> melodia -> onda quadrada -> WAV para a bobina."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import fidelity, mt3, rmvpe
from . import notes as notes_mod
from .analysis import analyze
from .calibrate import write_mono_wav
from .fetch import fetch, is_url
from .ffmpeg import decode_mono, trim_audio
from .midi import (
    ALL_PARTS,
    RATE,
    Note,
    beat_period,
    best_shift,
    clip_notes,
    combine_parts,
    contour_fold,
    coverage,
    drop_ghost_notes,
    lead_line,
    median_note_ms,
    notes_from,
    out_of_range_fraction,
    overlap_fraction,
    render,
    select_part,
    skyline,
    tempo_grid,
    to_grid,
    transpose,
    write_midi,
)
from .noteblock import NOTE_EXTS, fft_notes, nbs_tick, noteblock_style, read_parts
from .profile import Profile
from .separate import SEPARATOR_CHOICES, separate
from .voice import voice_notes

STEM_CHOICES = ("other", "vocals", "piano", "guitar", "mix")  # "mix" = sem separação
STYLE_CHOICES = ("auto", *fidelity.PRESETS)
# formas de converter: melody = transcrição de notas (áudio) / trilhas do arquivo (.mid, .nbs);
# exact = frequência exata de uma voz ou instrumento solo (só áudio);
# noteblock = como os conversores de Minecraft: uma batida por tique, nas 25 notas do note block
# vocalpitch = melodia cantada medida direto na música pelo RMVPE (só áudio)
# mt3 = transcrição de vários instrumentos com o YourMT3+ (só áudio)
METHOD_CHOICES = ("melody", "exact", "noteblock", "vocalpitch", "mt3")
AUDIO_ONLY = ("exact", "vocalpitch", "mt3")
KEEP_PITCH = (
    "exact",
    "vocalpitch",
)  # altura medida na voz: nenhuma nota sai do lugar se cabe  # "auto" = melhor score da busca duty x gate
PEAK_DB = -8.0  # nível que a bobina tocou bem (referência Mario)
FALLBACK_RANGE = (100.0, 800.0)
TRIM_PAD = 1.0  # s de áudio além do trecho pedido, de cada lado, para separar e transcrever
OVERLAP_WARN = 0.3  # acima disso a transcrição é muito polifônica para uma bobina
MAX_PEAK = -3.0
OUT_OF_RANGE_WARN = 0.2  # mesmo após transpor, muita nota fora da faixa vira salto de oitava
MOVED_WARN = 0.05  # acima disso avisa que parte da melodia mudou de oitava para caber na faixa
FAST_NOTE_MS = 100  # mediana abaixo disso: o arco não tem tempo de formar (Mario ~140 ms)

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Result:
    wav: Path
    notes_file: Path  # JSON com as notas geradas e as editadas
    original: Path | None  # áudio de origem (None se a entrada foi .mid)
    stem: str
    min_hz: float
    max_hz: float
    notes: int
    segments: int
    overlap: float
    shift: int  # semitons aplicados à música inteira para caber na faixa
    out_of_range: float  # fração do tempo fora da faixa depois do shift (antes de dobrar)
    median_note_ms: float
    moved: float  # fração do tempo em notas que saíram da oitava original para caber na faixa
    peak_db: float
    clipped: bool
    parts: tuple = ()  # .mid: trilhas disponíveis (dicts id/name/notes/lo/hi/chosen) para o usuário trocar
    part: int | None = None  # .mid: id da trilha usada
    duty: float = 0.5  # ciclo de trabalho da onda (0.5 = quadrada simétrica)
    gate: float = 1.0  # fração de cada nota que soa (1.0 = legato)
    style: str = "classic"  # preset escolhido (STYLE_CHOICES)
    fidelity: dict = field(default_factory=dict)  # ver `fidelity_info`
    bpm: float | None = (
        None  # áudio com pulso firme: tempo da grade em que as notas foram encaixadas
    )

    @property
    def midi(self) -> Path:
        """.mid das notas atuais (regravado a cada edição), ao lado do WAV."""
        return self.wav.with_suffix(".mid")

    @property
    def ok(self) -> bool:
        return self.peak_db <= MAX_PEAK and not self.clipped


def process(
    source: str,
    workdir: Path,
    profile: Profile | None,
    stem: str = "other",
    progress: Progress = lambda _msg: None,
    part: int | None = None,
    style: str = "classic",
    start: float = 0.0,
    end: float | None = None,
    method: str = "melody",
    separator: str = "demucs",
) -> Result:
    """`source` é URL http(s) ou caminho de áudio/.mid. Saída em workdir/output/<nome>.wav.

    `part` (só .mid): id da trilha (trilha*16+canal); -1 = todas; None = combina automaticamente
    as trilhas que parecem melodia (a melhor manda, as outras preenchem as pausas dela).
    `start`/`end` (s): processa só esse trecho da música (o tempo da saída começa em `start`).
    `style`: o padrão é a quadrada de 50% que a bobina já tocou bem; "auto" usa a busca de estilo.
    `method` (ver METHOD_CHOICES): "exact" é para uma voz só (cantarolar, assobiar) e mede a
    frequência exata de cada nota (sem transcritor, grade rítmica ou arredondar para o semitom);
    "noteblock" converte como os conversores de músicas para Minecraft.
    """
    if method not in METHOD_CHOICES:
        raise ValueError(f"forma de conversão inválida: {method}")
    if separator not in SEPARATOR_CHOICES:
        raise ValueError(f"separador inválido: {separator}")
    if stem not in STEM_CHOICES:
        raise ValueError(f"stem inválido: {stem}")
    if style not in STYLE_CHOICES:
        raise ValueError(f"estilo inválido: {style}")
    if start < 0 or (end is not None and end - start < 1):
        raise ValueError("trecho inválido: o fim precisa ser pelo menos 1 s depois do início")
    trimmed = start > 0 or end is not None
    min_hz, max_hz = (profile.min_hz, profile.max_hz) if profile else FALLBACK_RANGE

    if is_url(source):
        progress("Baixando áudio...")
        original = fetch(source, workdir / "input", cookies=workdir / "cookies.txt")
    else:
        original = Path(source).expanduser()
        if not original.is_file():
            raise ValueError(f"arquivo não encontrado: {original}")
    is_midi = original.suffix.lower() in NOTE_EXTS  # .mid ou .nbs: já traz as notas
    source_audio, pre = original, 0.0  # áudio que é separado/transcrito; margem antes do trecho
    if trimmed and not is_midi:  # corta antes de separar: o Demucs só roda no trecho
        progress("Cortando o trecho...")
        # com margem: o Demucs e o Basic Pitch perdem notas no último ~0,5 s de um arquivo
        pre = min(TRIM_PAD, start)
        stop = None if end is None else end + TRIM_PAD
        source_audio = trim_audio(original, start - pre, stop, workdir / "input")
        original = trim_audio(original, start, end, workdir / "input")  # o que o usuário ouve

    origin = source_audio
    if method in AUDIO_ONLY and is_midi:
        raise ValueError(f"'{method}' é para áudio, não para arquivo de notas")
    if stem != "mix" and not is_midi:
        engine = {"demucs": "Demucs", "demucs_hq": "Demucs fino", "roformer": "RoFormer"}[separator]
        if stem in ("piano", "guitar"):
            engine = "Demucs de 6 partes"
        elif separator == "roformer" and stem != "vocals":
            engine = "Demucs"
        progress(f"Isolando '{stem}' com {engine} (CPU, pode levar minutos na 1ª vez)...")
        origin = separate(source_audio, stem, workdir / "separated", separator)

    progress("Extraindo a melodia...")
    parts_info, chosen_part, bpm = (), None, None
    if is_midi:
        parts = read_parts(origin)
        if trimmed:
            for p in parts:
                p.notes = clip_notes(p.notes, start, end)
            parts = [p for p in parts if p.notes]
        if part is None:  # automático: melodia combinada de várias trilhas
            notes, used = combine_parts(parts)
        else:
            chosen = select_part(parts, part)
            chosen_part, notes = chosen.id, chosen.notes
            used = {p.id: coverage(p.notes)[0] for p in parts} if part == ALL_PARTS else {}
            used = used or {chosen.id: coverage(notes)[0]}
        parts_info = tuple(
            {
                "id": p.id,
                "name": p.name,
                "notes": len(p.notes),
                "lo": min((n.pitch for n in p.notes), default=0),
                "hi": max((n.pitch for n in p.notes), default=0),
                "used": round(used.get(p.id, 0.0), 1),  # segundos desta trilha na melodia final
            }
            for p in parts
        )
        # .mid tem notas limpas e a voz mais aguda é a melodia (baixo/raiz sustentado fica
        # abaixo); a pontuação por amplitude serve para notas transcritas de áudio, não para
        # velocity de MIDI (raízes de violão, longas e fortes, ganhavam do riff: 29% do tempo no Blue)
        segments = skyline(drop_ghost_notes(notes), 1, 0.06, 0.02)
        if method == "noteblock":
            segments = noteblock_style(segments, nbs_tick(origin))
    elif method in ("exact", "noteblock", "vocalpitch"):
        if method == "exact":
            segments = voice_notes(origin)
        elif method == "vocalpitch":
            progress("Medindo a melodia cantada (RMVPE; 1ª vez baixa o modelo, 362 MB)...")
            segments = rmvpe.voice_notes(origin)
        else:
            segments = noteblock_style(fft_notes(origin))
        notes = [Note(a, b, p[0]) for a, b, p in segments]
        if pre or end is not None:  # tira a margem do trecho, como no áudio
            stop = math.inf if end is None else end - start
            segments = [
                (max(a - pre, 0.0), min(b - pre, stop), p)
                for a, b, p in segments
                if min(b - pre, stop) - max(a - pre, 0.0) >= 0.03
            ]
    else:
        if method == "mt3":
            progress("Transcrevendo com o YourMT3+ (lento em CPU; 1ª vez baixa o modelo)...")
            notes = mt3.transcribe_notes(origin)
        else:
            notes = notes_from(origin)
        segments = lead_line(notes)
        # transcrição de áudio: o ritmo sai torto (início das notas ±50 ms, notas quebradas ao
        # acaso); se a música tem pulso firme, as notas são encaixadas na grade dele
        grid = tempo_grid(notes, beat_period(source_audio)) if segments else None
        if grid:
            segments = to_grid(segments, *grid)
            bpm = 60.0 / grid[0]
            while bpm >= 180:  # a grade é uma subdivisão da batida; BPM plausível para o .mid
                bpm /= 2
        if pre or end is not None:  # tira a margem: o tempo volta a contar do início do trecho
            stop = math.inf if end is None else end - start
            segments = [
                (max(a - pre, 0.0), min(b - pre, stop), p)
                for a, b, p in segments
                if min(b - pre, stop) - max(a - pre, 0.0) >= 0.03
            ]
    if not segments:
        raise ValueError("nenhuma nota detectada; tente outro som (stem) ou outro arquivo")
    shift = best_shift(segments, min_hz, max_hz)
    segments = transpose(segments, shift)
    out_of_range = out_of_range_fraction(segments, min_hz, max_hz)
    shifted = segments
    if out_of_range or method not in KEEP_PITCH:  # voz dentro da faixa: nenhuma nota sai do lugar
        segments = contour_fold(segments, min_hz, max_hz, profile.notes if profile else ())
    moved = _moved_fraction(shifted, segments)

    progress("Gerando a onda quadrada...")
    suffix = f".t{chosen_part}" if chosen_part is not None and part is not None else ""
    if method != "melody":  # cada forma de conversão tem o próprio arquivo
        suffix += f".{method}"
    if separator != "demucs" and stem != "mix" and not is_midi:
        suffix += f".{separator}"
    if trimmed and is_midi:  # no áudio o trecho já está no nome do arquivo cortado
        suffix += f".{start:g}-{'fim' if end is None else format(end, 'g')}"
    out = workdir / "output" / f"{original.stem}.{stem}{suffix}.wav"  # um arquivo por stem/trilha
    out.parent.mkdir(parents=True, exist_ok=True)
    progress("Medindo a fidelidade ao original...")
    ref = (
        fidelity.reference_from_notes(skyline(notes, 1, 0.06, 0.0))
        if is_midi
        else fidelity.reference_from_audio(
            decode_mono(origin, RATE)[int(pre * RATE) :], min_hz, max_hz
        )
    )
    chosen, table = fidelity.search(ref, segments, min_hz, max_hz)
    info = fidelity_info(table, chosen)
    key, duty, gate = pick_style(info, style)
    write_mono_wav(out, render(segments, min_hz, max_hz, PEAK_DB, duty, gate), RATE)
    editable = notes_mod.from_segments(segments)
    notes_file = out.with_suffix(".notes.json")
    notes_mod.save(notes_file, min_hz, max_hz, editable, editable)
    write_midi(out.with_suffix(".mid"), segments, bpm)
    report = analyze(decode_mono(out))
    return Result(
        wav=out,
        notes_file=notes_file,
        original=None if is_midi else original,
        stem="mix" if (stem == "mix" or is_midi) else stem,
        min_hz=min_hz,
        max_hz=max_hz,
        notes=len(notes),
        segments=len(segments),
        overlap=0.0 if is_midi else overlap_fraction(notes),
        shift=shift,
        out_of_range=out_of_range,
        median_note_ms=median_note_ms(segments),
        moved=moved,
        peak_db=report.peak_db,
        clipped=report.clipped,
        parts=parts_info,
        part=chosen_part,
        duty=duty,
        gate=gate,
        style=key,
        fidelity=info,
        bpm=bpm,
    )


def fidelity_info(table: list, auto: "fidelity.Style") -> dict:
    """Resultado da busca em formato JSON: o estilo automático e o score de cada preset."""
    rows = {(st.duty, st.gate): sc for st, sc in table}
    presets = [
        {
            "id": "auto",
            "label": "Automático (melhor score)",
            "duty": auto.duty,
            "gate": auto.gate,
            "scores": rows[(auto.duty, auto.gate)].to_dict(),
        }
    ]
    for key, (label, duty, gate) in fidelity.PRESETS.items():
        sc = rows.get((duty, gate))
        presets.append(
            {
                "id": key,
                "label": label,
                "duty": duty,
                "gate": gate,
                "scores": sc.to_dict() if sc else None,
            }
        )
    return {
        "auto": {"duty": auto.duty, "gate": auto.gate},
        "presets": presets,
        "measured_brightness": any(sc.brightness is not None for _, sc in table),
    }


def pick_style(info: dict, style: str) -> tuple[str, float, float]:
    """(preset, duty, gate) do preset pedido; 'auto' usa o melhor da busca."""
    for p in info["presets"]:
        if p["id"] == style:
            return style, p["duty"], p["gate"]
    raise ValueError(f"estilo inválido: {style}")


def restyle(result: Result, style: str) -> Result:
    """Re-renderiza as notas atuais (editadas ou não) com outro preset de duty/gate."""
    if style not in STYLE_CHOICES:
        raise ValueError(f"estilo inválido: {style}")
    key, duty, gate = pick_style(result.fidelity, style)
    current = notes_mod.load(result.notes_file)["current"]
    samples = render(
        notes_mod.to_segments(current), result.min_hz, result.max_hz, PEAK_DB, duty, gate
    )
    write_mono_wav(result.wav, samples, RATE)
    report = analyze(decode_mono(result.wav))
    return replace(
        result, duty=duty, gate=gate, style=key, peak_db=report.peak_db, clipped=report.clipped
    )


def _moved_fraction(before: list[tuple], after: list[tuple]) -> float:
    total = sum(b - a for a, b, _ in before)
    moved = sum(b - a for (a, b, p), (_, _, q) in zip(before, after, strict=True) if p != q)
    return moved / total if total else 0.0


def apply_edit(result: Result, raw_notes: list[dict]) -> tuple[Result, list[notes_mod.EditNote]]:
    """Valida as notas editadas, re-renderiza o WAV (mesmo `render` do pipeline) e salva o JSON."""
    saved = notes_mod.load(result.notes_file)
    cleaned = notes_mod.validate(raw_notes, result.min_hz, result.max_hz)
    if not cleaned:
        raise ValueError("nenhuma nota: o resultado ficaria em silêncio")
    samples = render(
        notes_mod.to_segments(cleaned),
        result.min_hz,
        result.max_hz,
        PEAK_DB,
        result.duty,
        result.gate,
    )
    write_mono_wav(result.wav, samples, RATE)
    notes_mod.save(result.notes_file, result.min_hz, result.max_hz, saved["generated"], cleaned)
    write_midi(result.midi, notes_mod.to_segments(cleaned), result.bpm)
    report = analyze(decode_mono(result.wav))
    updated = replace(result, segments=len(cleaned), peak_db=report.peak_db, clipped=report.clipped)
    return updated, cleaned
