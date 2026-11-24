"""Fix the grid phase of a finished ALS: a drift fit per track, then a slide per track, measured by the kick check.

Usage:  python Tools/fix_grid_phase.py <ALS> --audio <dir> [--out <new ALS>] [--report <dir>]

Writes a NEW ALS (default: "<name> Phased.als" beside the input; the input is never touched) and, in the report folder
(default: "Grid Phase" beside the ALS), a plain-text before/after report, a json copy of it and the kick check
pictures of the new file. The report lists every transition before and after (edge, click, main hit, verdict) and, per
track, the drift correction range and the slide. A mix whose judged blends are all under the kick check's WARN figure is
left alone (the new file is then a byte copy of the input).
When it writes a phased file it also writes "grid_phase.json" in the report folder: the record of exactly which tracks it
moved (their original markers and the largest shift), which "validate_mix_plan_als.py --grid-phase" checks the phased file
by. A run that writes no phased file (nothing to do, refused, failed) writes no record and leaves none behind. The json copy
of the report is "grid_phase_report.json".
Exit codes:
  0  done, or nothing to do.
  2  could not run (unreadable ALS or wav, missing library, any other error); the class and message go to stderr.
  4  the result was refused: a judged blend got worse or a blend became FAIL. The file is kept beside the output
     as "<name> REFUSED.als" for inspection and nothing is written at the --out path.
"""
import argparse
import json
import sys
from pathlib import Path

EXIT_OK, EXIT_COULD_NOT_RUN, EXIT_REFUSED = 0, 2, 4


def say(text, stream=None):
    """Print without ever crashing on a console that cannot show a character (cp1252 and the like)."""
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    print(text.encode(encoding, errors="replace").decode(encoding), file=stream)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("als", help="the final ALS")
    ap.add_argument("--audio", help="folder holding the wavs the ALS plays (else the paths stored in the ALS)")
    ap.add_argument("--out", help="the new ALS (default: '<name> Phased.als' beside the input)")
    ap.add_argument("--report", help="folder for the report and the pictures (default: 'Grid Phase' beside the ALS)")
    args = ap.parse_args(argv)
    src = Path(args.als)
    out = Path(args.out) if args.out else src.resolve().parent / (src.stem + " Phased.als")
    report = Path(args.report) if args.report else src.resolve().parent / "Grid Phase"
    if out.resolve() != src.resolve():             # nothing from an earlier run may pass for this run's result
        for old in (out, out.with_name(out.stem + " REFUSED.als"), out.with_name(out.name + ".partial")):
            old.unlink(missing_ok=True)
    for old in ("grid_phase.json", "grid_phase.json.partial"):   # a record without its phased file proves nothing
        (report / old).unlink(missing_ok=True)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))
        import grid_phase_fix as gpf
        import kick_alignment_check as kac
        report.mkdir(parents=True, exist_ok=True)
        kac.clear_old_outputs(report)
        outcome = gpf.run(src, args.audio, out, report)
        text = gpf.report_text(outcome, src)
        (report / "grid_phase_report.txt").write_text(text, encoding="ascii", errors="replace")
        (report / "grid_phase_report.json").write_text(json.dumps(gpf.report_json(outcome), indent=1), encoding="ascii")
        if outcome.after:
            kac.write_json(outcome.after, report / "kick_alignment.json")
    except Exception as exc:
        say(f"grid phase fix could not run on {args.als}: {type(exc).__name__}: {exc}", sys.stderr)
        return EXIT_COULD_NOT_RUN
    say(text.rstrip("\n"))
    if outcome.status == gpf.REFUSED_RESULT:
        say(f"RESULT REFUSED: {outcome.reason}")
        say(f"REPORT: {report}")
        return EXIT_REFUSED
    say("RESULT NOTHING TO DO: " + outcome.reason if outcome.status == gpf.NOTHING else f"RESULT PHASED: {out}")
    say(f"REPORT: {report}")
    if outcome.after:
        say(f"LOOK AT THESE PICTURES: {report}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
