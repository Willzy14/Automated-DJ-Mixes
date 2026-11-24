"""Replay the owner's hand tweak of a planned mix through the blend planner.

Usage:
  python Tools/score_tweak.py --baseline PLANNED.als --tweak SAM.als --out OUTDIR
         (--project PROJECT_DIR | --content planner_content.json)
         [--decisions planned_decisions.json] [--pipeline-log phase2.log]

--project reads the pipeline's analysis cache (_Stem Analysis) the way Tools/plan_mix_decisions.py does;
--content takes the planner tracks that tool saved (Output/planner_content.json).  --decisions uses the
pipeline's decisions file as the round-trip reference instead of re-planning.  --pipeline-log reads
"Tn: dropped intro_loop|tail_loop" lines (Output/phase2.log): loops the pipeline's own quality gate removed
after planning, which the round trip must expect (a weaker check, and the report says so).  --owner-name sets the
name used for the tweaking owner (default "the owner").  A problem with the inputs prints one line and exits 2.

For every transition: the owner's placement in the planner's terms, the planner's top pick, whether they
are the same, where the owner's sits among the candidates (rank N of M, a listed alternative, or outside
the vocabulary and why), the cost of both term by term, and a trivial baseline (16 bars in, 16 out, swap on
the outgoing's outro start) beside it.  First the decoder is run on the planner's own set: it must give back
the planner's own plan to within one beat or nothing is scored.

Writes tweak_replay_report.txt and tweak_replay.json into --out and nothing else.  Inputs are only read.
Exit 0 on a report, 2 when the round trip refuses.
"""
import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
sys.path.insert(0, str(ROOT / "Tools"))
import tweak_replay as tr  # noqa: E402

REPORT, JSON_NAME = "tweak_replay_report.txt", "tweak_replay.json"


def planner_tracks_from_project(project, names):
    """{name: planner track} from the pipeline's cache; the same converter plan_mix_decisions uses."""
    import plan_mix_decisions as pmd
    stem_dir = Path(project) / "_Stem Analysis"
    found = {}
    for name in names:
        n = html.unescape(name)
        sections, env = stem_dir / ("SECTIONS_STEM_%s.json" % n), stem_dir / ("%s__stemenv.npz" % n)
        if not (sections.is_file() and env.is_file()):
            raise ValueError("no analysis cache for %r in %s" % (n, stem_dir))
        found[n] = pmd.convert_track(pmd.read_json(sections), env)
    return found


def _ascii(text):
    return text.encode("ascii", "backslashreplace").decode("ascii")


def write_outputs(out_dir, text, data, inputs):
    out_dir = Path(out_dir)
    targets = [out_dir / REPORT, out_dir / JSON_NAME]
    resolved_inputs = {Path(p).resolve() for p in inputs if p}
    for t in targets:
        if t.resolve() in resolved_inputs:
            raise ValueError("refusing to overwrite an input: %s" % t)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets[0].write_text(_ascii(text) + "\n", encoding="ascii")
    targets[1].write_text(json.dumps(data, indent=2, ensure_ascii=True, default=str) + "\n", encoding="ascii")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True, type=Path, help="the planner's own set (.als)")
    ap.add_argument("--tweak", required=True, type=Path, help="the owner's hand-edited copy (.als)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--project", type=Path, help="pipeline project folder holding _Stem Analysis")
    src.add_argument("--content", type=Path, help="planner tracks JSON ({'tracks': {name: track}})")
    ap.add_argument("--decisions", type=Path, help="decisions JSON as the round-trip reference")
    ap.add_argument("--pipeline-log", type=Path, help="phase log naming loops the pipeline dropped after planning")
    ap.add_argument("--owner-name", default="the owner", help="name used for the tweaking owner in the report")
    ap.add_argument("--out", required=True, type=Path, help="folder for the report and the JSON")
    args = ap.parse_args(argv)
    inputs = [args.baseline, args.tweak, args.content, args.decisions, args.pipeline_log]
    try:
        names = [t["name"] for t in tr.read_als(args.baseline)]
        if args.content:
            tracks = json.loads(args.content.read_text(encoding="utf-8"))["tracks"]
        else:
            tracks = planner_tracks_from_project(args.project, names)
        decisions = json.loads(args.decisions.read_text(encoding="utf-8")) if args.decisions else None
        dropped = (tr.parse_dropped_loops(args.pipeline_log.read_text(encoding="utf-8", errors="replace"))
                   if args.pipeline_log else None)
        result = tr.replay(args.baseline, args.tweak, tracks, decisions=decisions, dropped=dropped,
                           owner=args.owner_name)
        text = tr.format_report(result)
        write_outputs(args.out, text, result, inputs)
    except tr.RoundTripError as exc:
        text = tr.format_failed_round_trip(exc.rows, str(exc)) if exc.rows else "ROUND TRIP FAILED: %s" % exc
        try:
            write_outputs(args.out, text, {"round_trip": {"passed": False, "message": str(exc), "rows": exc.rows}}, inputs)
        except (OSError, ValueError) as werr:
            text += "\n(report not written: %s)" % werr
        print(_ascii(text))
        return 2
    except (ValueError, OSError, KeyError, IndexError) as exc:
        print(_ascii("score_tweak: %s" % " ".join(str(exc).split())))
        return 2
    print(_ascii(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
