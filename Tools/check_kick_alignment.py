"""Check that the kicks of the two tracks line up inside every blend of a finished ALS, and draw them.

Usage:  python Tools/check_kick_alignment.py <ALS> [--audio <dir>] [--out <dir>] [--mode warn|fail]

One line per transition, a count line, then the folder with the pictures. Look at the pictures: the
numbers are a summary, the picture is the check. Each line gives three lags in ms (positive = the
incoming kick is later): edge (leading edge of the click), click (cross-correlation of the >150 Hz
envelopes) and main (steepest rise of the >40 Hz envelope).
Exit codes:
  0  done. In warn mode this is the exit code whatever the verdicts or the loud line.
  1  fail mode only: at least one transition is FAIL (or OFF_GRID).
  2  the check could not run (unreadable ALS or wav, missing library, any other error); the class
     and message go to stderr.
  3  fail mode only: every transition is CANNOT_JUDGE, so the check says nothing about the kicks.
When every transition is CANNOT_JUDGE the line NOTHING COULD BE JUDGED is printed in either mode.
CANNOT_JUDGE is printed as it is and never turned into PASS; one that carries LARGE_LAG_FEW_KICKS
is a lag seen on too few kicks to judge, printed so it cannot hide.
The mode comes from --mode, else the env var KICK_ALIGNMENT_MODE, else warn (warn only for the first
two mixes; Sam flips it). --audio is looked at first; without it (or for a file not in it) the sample
paths stored in the ALS are used, relative ones from the ALS's own folder. Default --out: a folder
"Kick Alignment" beside the ALS (the pipeline passes Output/Kick Alignment). Pictures and the json
an earlier run left in --out are deleted first.
"""
import argparse
import os
import sys
from pathlib import Path

EXIT_OK, EXIT_FAIL, EXIT_COULD_NOT_RUN, EXIT_NOTHING_JUDGED = 0, 1, 2, 3


def say(text, stream=None):
    """Print without ever crashing on a console that cannot show a character (cp1252 and the like)."""
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    print(text.encode(encoding, errors="replace").decode(encoding), file=stream)


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
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))
        import kick_alignment_check as kac
        results = kac.check_als(args.als, args.audio, out)
        for res in results:
            say(kac.summary_line(res))
        say(kac.count_line(results))
        kac.write_json(results, out / "kick_alignment.json")
        nothing = kac.nothing_judged(results)
        if nothing:
            say("NOTHING COULD BE JUDGED: no transition could be measured, so this check says nothing about the kicks")
        if any(r.picture for r in results):
            say(f"LOOK AT THESE PICTURES: {out}")
        else:
            say("no pictures were made")
        failed = any(r.verdict == "FAIL" for r in results)
    except Exception as exc:
        say(f"kick alignment could not run on {args.als}: {type(exc).__name__}: {exc}", sys.stderr)
        return EXIT_COULD_NOT_RUN
    if args.mode == "fail" and failed:
        return EXIT_FAIL
    if args.mode == "fail" and nothing:
        return EXIT_NOTHING_JUDGED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
