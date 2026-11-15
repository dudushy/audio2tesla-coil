import argparse
import sys
from pathlib import Path

from . import __version__
from .analysis import Report, analyze
from .ffmpeg import FfmpegError, decode_mono, prep_file
from .presets import PRESETS

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
DEFAULT_MAX_PEAK = -3.0


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
    highpass = args.highpass if args.highpass is not None else preset.highpass
    lowpass = args.lowpass if args.lowpass is not None else preset.lowpass
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
            prep_file(s, d, highpass, lowpass, limit, args.rate)
            report = analyze(decode_mono(d))
        except (FfmpegError, ValueError) as e:
            print(f"{s}: erro: {e}", file=sys.stderr)
            failed += 1
            continue
        status = "OK" if is_ok(report, DEFAULT_MAX_PEAK) else "AVISO: acima da meta"
        print(f"{s} -> {d}: {format_report(report)} [{status}]")
        if status != "OK":
            failed += 1
    return 1 if failed else 0


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
    prep.add_argument("--rate", type=int, default=44100, help="sample rate de saída (padrão 44100)")
    prep.set_defaults(func=cmd_prep)

    check = sub.add_parser("check", help="reporta pico, RMS e clipping")
    check.add_argument("file", type=Path)
    check.add_argument("--max-peak", type=float, default=DEFAULT_MAX_PEAK, help="dBFS (padrão -3)")
    check.set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FfmpegError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
