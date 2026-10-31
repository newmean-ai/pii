"""python -m newmean_pii download [--size medium] [--variant q4e] [--cache-dir DIR]
   python -m newmean_pii redact [--size medium] [--mode tag] [--model-dir DIR] [--regex] < input.txt"""
import argparse
import sys

from . import DEFAULT_SIZE, SIZES, VARIANTS, create_redactor, fetch_model


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m newmean_pii", description="newmean-pii: on-device PII detection and masking")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="download and verify model files into the cache (for offline use)")
    r = sub.add_parser("redact", help="mask the text read from stdin")
    for a in (d, r):
        a.add_argument("--size", default=DEFAULT_SIZE, choices=SIZES)
        a.add_argument("--variant", choices=VARIANTS)
        a.add_argument("--cache-dir")
    r.add_argument("--model-dir")
    r.add_argument("--mode", default="tag", choices=("tag", "mask", "blank"))
    r.add_argument("--sensitivity", type=float, default=0.0)
    r.add_argument("--regex", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "download":
        print(fetch_model(args.size, args.variant, cache_dir=args.cache_dir))
        return 0
    redactor = create_redactor(
        args.size, args.variant, sensitivity=args.sensitivity, regex=args.regex, model_dir=args.model_dir, cache_dir=args.cache_dir
    )
    # UTF-8 in and out whatever the locale (Windows pipes default to the ANSI code page); newline="" keeps CRLF as is
    sys.stdin.reconfigure(encoding="utf-8", newline="")
    sys.stdout.reconfigure(encoding="utf-8", newline="")
    sys.stdout.write(redactor.redact(sys.stdin.read(), args.mode).text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
