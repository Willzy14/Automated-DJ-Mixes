"""Check that the kicks of the two tracks line up inside every blend of a finished ALS, and draw them.

Usage:  python Tools/check_kick_alignment.py <ALS> --audio <dir> [--out <dir>] [--mode warn|fail]

One line per transition, then the folder with the pictures. Look at the pictures: the numbers are
a summary, the picture is the check.
  warn (default)  exit 0 whatever the verdicts; exit 2 only when the ALS cannot be read.
  fail            exit 1 when any transition is FAIL. CANNOT_JUDGE is printed as it is and never
                  turned into PASS; one that carries LARGE_LAG_FEW_KICKS is a lag seen on too few
                  kicks to judge, printed so it cannot hide.
The mode comes from --mode, else the env var KICK_ALIGNMENT_MODE, else warn (warn only for the first
two mixes; Sam flips it). Default --out: a folder "Kick Alignment" beside the ALS.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))
import kick_alignment_check as kac  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("als", help="the final ALS")
    ap.add_argument("--audio", required=True, help="folder holding the wavs the ALS plays")
    ap.add_argument("--out", help="folder for the pictures (default: 'Kick Alignment' beside the ALS)")
    ap.add_argument("--mode", choices=("warn", "fail"), default=os.environ.get("KICK_ALIGNMENT_MODE", "warn"))
    args = ap.parse_args(argv)
    if args.mode not in ("warn", "fail"):
        ap.error("mode must be warn or fail, got " + repr(args.mode))
    out = Path(args.out) if args.out else Path(args.als).resolve().parent / "Kick Alignment"
    try:
        results = kac.check_als(args.als, args.audio, out)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot read {args.als}: {exc}", file=sys.stderr)
        return 2
    for res in results:
        print(kac.summary_line(res))
    kac.write_json(results, out / "kick_alignment.json")
    print(f"LOOK AT THESE PICTURES: {out}")
    if args.mode == "fail" and any(r.verdict == "FAIL" for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
