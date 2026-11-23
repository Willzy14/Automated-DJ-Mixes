"""Check that the kicks of the two tracks line up inside every blend of a finished ALS, and draw them.

Usage:  python Tools/check_kick_alignment.py <ALS> [--audio <dir>] [--out <dir>] [--mode warn|fail]

One line per transition, a count line, then the folder with the pictures. Look at the pictures: the
numbers are a summary, the picture is the check.
  warn (default)  exit 0 whatever the verdicts.
  fail            exit 1 when any transition is FAIL, or when nothing could be judged at all.
                  CANNOT_JUDGE is printed as it is and never turned into PASS; one that carries
                  LARGE_LAG_FEW_KICKS is a lag seen on too few kicks to judge, printed so it cannot hide.
  both            exit 2 when the check could not run (unreadable ALS or wav, any other error); the
                  class and message go to stderr. The pipeline tells exit 1 (a verdict) from exit 2.
When every transition is CANNOT_JUDGE the line NOTHING COULD BE JUDGED is printed in either mode.
The mode comes from --mode, else the env var KICK_ALIGNMENT_MODE, else warn (warn only for the first
two mixes; Sam flips it). --audio is looked at first; without it (or for a file not in it) the sample
paths stored in the ALS are used, relative ones from the ALS's own folder. Default --out: a folder
"Kick Alignment" beside the ALS (the pipeline passes Output/Kick Alignment).
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
    ap.add_argument("--audio", help="folder holding the wavs the ALS plays (else the paths stored in the ALS)")
    ap.add_argument("--out", help="folder for the pictures (default: 'Kick Alignment' beside the ALS)")
    ap.add_argument("--mode", choices=("warn", "fail"), default=os.environ.get("KICK_ALIGNMENT_MODE", "warn"))
    args = ap.parse_args(argv)
    if args.mode not in ("warn", "fail"):
        ap.error("mode must be warn or fail, got " + repr(args.mode))
    out = Path(args.out) if args.out else Path(args.als).resolve().parent / "Kick Alignment"
    try:
        results = kac.check_als(args.als, args.audio, out)
        for res in results:
            print(kac.summary_line(res))
        print(kac.count_line(results))
        kac.write_json(results, out / "kick_alignment.json")
    except Exception as exc:
        print(f"kick alignment could not run on {args.als}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    nothing = kac.nothing_judged(results)
    if nothing:
        print("NOTHING COULD BE JUDGED: no transition could be measured, so this check says nothing about the kicks")
    if any(r.picture for r in results):
        print(f"LOOK AT THESE PICTURES: {out}")
    else:
        print("no pictures were made")
    if args.mode == "fail" and (nothing or any(r.verdict == "FAIL" for r in results)):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
