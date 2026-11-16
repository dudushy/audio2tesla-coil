import argparse
import sys
from pathlib import Path

from . import __version__
from .analysis import Report, analyze
from .calibrate import DEFAULT_FREQS, square_tones, write_mono_wav
from .fetch import DEFAULT_DIR as FETCH_DIR
from .fetch import FetchError, fetch
from .ffmpeg import FfmpegError, decode_mono, prep_file
from .midi import (
    MIDI_EXTS,
    RATE,
    contour_fold,
    melody_line,
    notes_from,
    overlap_fraction,
    render,
    skyline,
)
from .presets import PRESETS
from .profile import DEFAULT_PATH, Profile, load_profile, save_profile
from .separate import DEFAULT_DIR, DEFAULT_STEM, STEMS, separate

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
OVERLAP_WARN = 0.3  # acima disso a transcrição é muito polifônica para uma bobina
DEFAULT_MAX_PEAK = -3.0
FALLBACK_RANGE = (100.0, 800.0)  # sem --profile: faixa que soou bem na calibragem de teste


def format_report(r: Report) -> str:
    return f"pico {r.peak_db:.1f} dBFS | RMS {r.rms_db:.1f} dBFS | clipping: {'SIM' if r.clipped else 'não'}"


def is_ok(r: Report, max_peak: float) -> bool:
    return r.peak_db <= max_peak and not r.clipped


def cmd_check(args) -> int:
    report = analyze(decode_mono(args.file))
    ok = is_ok(report, args.max_peak)
    print(
        f"{args.file}: {format_report(report)} -> {'OK' if ok else 'FALHOU'} (meta pico <= {args.max_peak:g} dBFS)"
    )
    return 0 if ok else 1


def cmd_prep(args) -> int:
    preset = PRESETS[args.preset]
    base_hp, base_lp = preset.highpass, preset.lowpass
    if args.profile:  # perfil do dispositivo substitui os cortes do preset
        try:
            profile = load_profile(args.profile)
        except ValueError as e:
            print(f"erro: {e}", file=sys.stderr)
            return 1
        base_hp, base_lp = profile.min_hz, profile.max_hz
    highpass = args.highpass if args.highpass is not None else base_hp
    lowpass = args.lowpass if args.lowpass is not None else base_lp
    limit = args.limit if args.limit is not None else preset.limit

    src = args.input
    if src.is_dir():
        files = sorted(p for p in src.iterdir() if p.suffix.lower() in AUDIO_EXTS)
        if not files:
            print(f"Nenhum áudio ({', '.join(sorted(AUDIO_EXTS))}) em {src}", file=sys.stderr)
            return 1
        if args.output.exists() and not args.output.is_dir():
            print(f"Em lote, -o deve ser uma pasta: {args.output}", file=sys.stderr)
            return 1
        jobs = [(f, args.output / f"{f.stem}.wav") for f in files]
    elif src.is_file():
        jobs = [(src, args.output)]
    else:
        print(f"Entrada não encontrada: {src}", file=sys.stderr)
        return 1

    failed = 0
    for s, d in jobs:
        if s.resolve() == d.resolve():
            print(f"{s}: saída igual à entrada, pulando", file=sys.stderr)
            failed += 1
            continue
        try:
            origin = separate(s, args.stem, args.stems_dir) if args.separate else s
            prep_file(origin, d, highpass, lowpass, limit, args.rate)
            report = analyze(decode_mono(d))
        except (FfmpegError, ValueError, RuntimeError) as e:
            print(f"{s}: erro: {e}", file=sys.stderr)
            failed += 1
            continue
        status = "OK" if is_ok(report, DEFAULT_MAX_PEAK) else "AVISO: acima da meta"
        print(f"{s} -> {d}: {format_report(report)} [{status}]")
        if status != "OK":
            failed += 1
    return 1 if failed else 0


def cmd_calibrate_tones(args) -> int:
    freqs = args.freqs or DEFAULT_FREQS
    write_mono_wav(args.output, square_tones(freqs, args.seconds, args.gap, args.peak_db))
    step = args.seconds + args.gap
    print(
        f"{args.output}: {len(freqs)} tons quadrados, pico {args.peak_db:g} dBFS. Comece com volume BAIXO."
    )
    for i, f in enumerate(freqs):
        print(f"  {i * step:6.1f}s  {f:g} Hz")
    print(
        "Anote quais soam como nota clara e depois rode: coil-prep calibrate save --min HZ --max HZ"
    )
    return 0


def cmd_calibrate_save(args) -> int:
    try:
        profile = Profile(args.min, args.max, args.name)
    except ValueError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    save_profile(profile, args.profile)
    print(f"Perfil salvo em {args.profile}: {profile.min_hz:g}-{profile.max_hz:g} Hz")
    return 0


def cmd_midi(args) -> int:
    min_hz, max_hz = FALLBACK_RANGE
    if args.profile:
        try:
            profile = load_profile(args.profile)
        except ValueError as e:
            print(f"erro: {e}", file=sys.stderr)
            return 1
        min_hz, max_hz = profile.min_hz, profile.max_hz
    min_hz = args.min_hz if args.min_hz is not None else min_hz
    max_hz = args.max_hz if args.max_hz is not None else max_hz
    if not args.input.is_file():
        print(f"Entrada não encontrada: {args.input}", file=sys.stderr)
        return 1
    is_midi = args.input.suffix.lower() in MIDI_EXTS
    if args.separate and is_midi:
        print("erro: --separate só vale para áudio, não para .mid", file=sys.stderr)
        return 1
    try:
        origin = separate(args.input, args.stem, args.stems_dir) if args.separate else args.input
        notes = notes_from(origin, args.detect_min_note)
        if args.voices == 1 and args.select == "weighted":
            segments = melody_line(notes, args.min_note, args.gap, args.merge_gap)
            if segments:
                segments = contour_fold(segments, min_hz, max_hz)
        else:
            segments = skyline(notes, args.voices, args.min_note, args.gap)
        samples = render(segments, min_hz, max_hz, args.peak_db)
    except (ValueError, RuntimeError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    if not is_midi and (frac := overlap_fraction(notes)) > OVERLAP_WARN:
        hint = (
            "tente outro --stem, ou um .mid de melodia"
            if args.separate
            else "tente --separate (isola a voz com Demucs) ou um .mid de melodia"
        )
        print(
            f"aviso: {frac:.0%} do tempo tem notas sobrepostas (mixagem polifônica); "
            f"a melodia pode sair errada: {hint}.",
            file=sys.stderr,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_mono_wav(args.output, samples, RATE)
    report = analyze(decode_mono(args.output))
    print(
        f"{args.input} -> {args.output}: {len(notes)} notas -> {len(segments)} trechos "
        f"({args.voices} voz), faixa {min_hz:g}-{max_hz:g} Hz | {format_report(report)}"
    )
    return 0 if is_ok(report, DEFAULT_MAX_PEAK) else 1


def cmd_fetch(args) -> int:
    wav = fetch(args.url, args.out_dir, mp3=args.mp3)
    print(f"{args.url} -> {wav}" + (f" (+ {wav.with_suffix('.mp3')})" if args.mp3 else ""))
    print("aviso: baixe só conteúdo que você tem direito de usar; não versione áudio.")
    return 0


def add_separate_args(parser) -> None:
    parser.add_argument(
        "--separate", action="store_true", help="isola um stem com Demucs antes (extra [separate])"
    )
    parser.add_argument(
        "--stem", choices=STEMS, default=DEFAULT_STEM, help=f"stem usado (padrão {DEFAULT_STEM})"
    )
    parser.add_argument(
        "--stems-dir", type=Path, default=DEFAULT_DIR, help="cache dos stems (padrão separated/)"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coil-prep", description="Prepara áudio para plasma speaker.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prep", help="converte para mono, filtra e limita o pico")
    prep.add_argument("input", type=Path, help="arquivo de áudio ou pasta (lote)")
    prep.add_argument(
        "-o", "--output", type=Path, required=True, help="WAV de saída (ou pasta, em lote)"
    )
    prep.add_argument("--preset", choices=sorted(PRESETS), default="default")
    prep.add_argument("--highpass", type=float, help="Hz (sobrescreve o preset)")
    prep.add_argument("--lowpass", type=float, help="Hz (sobrescreve o preset)")
    prep.add_argument("--limit", type=float, help="limite linear 0.0625..1 (0.7 ~ -3 dBFS)")
    prep.add_argument(
        "--profile", type=Path, help="device.json (calibrate save): usa min/max Hz como filtros"
    )
    prep.add_argument("--rate", type=int, default=44100, help="sample rate de saída (padrão 44100)")
    add_separate_args(prep)
    prep.set_defaults(func=cmd_prep)

    fetch_p = sub.add_parser(
        "fetch", help="baixa o áudio de uma URL (YouTube) como WAV (extra [youtube])"
    )
    fetch_p.add_argument("url", help="URL do vídeo")
    fetch_p.add_argument(
        "-d", "--out-dir", type=Path, default=FETCH_DIR, help="pasta de saída (padrão input/)"
    )
    fetch_p.add_argument("--mp3", action="store_true", help="gera também um .mp3 ao lado do WAV")
    fetch_p.set_defaults(func=cmd_fetch)

    check = sub.add_parser("check", help="reporta pico, RMS e clipping")
    check.add_argument("file", type=Path)
    check.add_argument("--max-peak", type=float, default=DEFAULT_MAX_PEAK, help="dBFS (padrão -3)")
    check.set_defaults(func=cmd_check)

    midi = sub.add_parser("midi", help="melodia: .mid ou áudio -> onda quadrada monofônica")
    midi.add_argument("input", type=Path, help=".mid/.midi ou áudio (áudio exige extra [midi])")
    midi.add_argument("-o", "--output", type=Path, required=True, help="WAV de saída")
    midi.add_argument("--profile", type=Path, help="device.json: faixa de Hz da bobina")
    midi.add_argument("--min-hz", type=float, help="sobrescreve o mínimo do perfil")
    midi.add_argument("--max-hz", type=float, help="sobrescreve o máximo do perfil")
    midi.add_argument("--voices", type=int, default=1, help="vozes simultâneas (padrão 1)")
    midi.add_argument("--min-note", type=float, default=0.06, help="descarta notas < N s")
    midi.add_argument(
        "--gap", type=float, default=0.02, help="silêncio entre notas coladas, em s (padrão 0.02)"
    )
    midi.add_argument(
        "--select",
        choices=("weighted", "skyline"),
        default="weighted",
        help="1 voz: weighted = amplitude+altura+continuidade, oitava pelo contorno (padrão); "
        "skyline = sempre a mais aguda, oitava nota a nota (comportamento antigo)",
    )
    midi.add_argument(
        "--merge-gap",
        type=float,
        default=0.02,
        help="junta fragmentos da mesma nota separados por menos de N s (padrão 0.02; 0 desliga)",
    )
    midi.add_argument(
        "--detect-min-note",
        type=float,
        default=80.0,
        help="ms: menor nota que o Basic Pitch detecta (padrão 80; o dele é 127.7)",
    )
    midi.add_argument("--peak-db", type=float, default=-8.0, help="pico de saída em dBFS")
    add_separate_args(midi)
    midi.set_defaults(func=cmd_midi)

    cal = sub.add_parser("calibrate", help="descobre a faixa de Hz que a bobina toca")
    cal_sub = cal.add_subparsers(dest="cal_command", required=True)
    tones = cal_sub.add_parser("tones", help="gera WAV de tons quadrados para ouvir na bobina")
    tones.add_argument("-o", "--output", type=Path, default=Path("calibrate.wav"))
    tones.add_argument("--freqs", type=float, nargs="+", help="Hz (padrão: 100..2000)")
    tones.add_argument("--seconds", type=float, default=1.5, help="duração de cada tom")
    tones.add_argument("--gap", type=float, default=0.5, help="silêncio entre tons")
    tones.add_argument("--peak-db", type=float, default=-8.0, help="volume do teste (dBFS)")
    tones.set_defaults(func=cmd_calibrate_tones)
    save = cal_sub.add_parser("save", help="salva o perfil (faixa mín/máx em Hz)")
    save.add_argument("--min", type=float, required=True, help="menor Hz que toca bem")
    save.add_argument("--max", type=float, required=True, help="maior Hz que toca bem")
    save.add_argument("--name", default="plasma-speaker")
    save.add_argument(
        "--profile", type=Path, default=DEFAULT_PATH, help="arquivo (padrão device.json)"
    )
    save.set_defaults(func=cmd_calibrate_save)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FfmpegError, FetchError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
