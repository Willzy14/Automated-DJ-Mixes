"""Measure how often Sam's own blends overlap vocals and clash in key, beside the planner's pick.

Usage:
  python Tools/measure_vocal_key.py build-dftd [--env-dir DIR] [--out FILE]
      write the per-bar vocal flags and Camelot keys of the seven DFTD answer-key tracks
      (Documentation/Golden Sections/Vocal Key 2026-11-24 DFTD.json; score_blends reads it when present)
  python Tools/measure_vocal_key.py dftd [--old]
      the table for the seven answer-key blends
  python Tools/measure_vocal_key.py tweak --baseline A.als --tweak B.als --project DIR
         [--decisions D.json] [--pipeline-log LOG] [--old]
      the table for Sam's hand tweak of the Tech House mix
--old sets both clash weights to zero, which gives the planner's pick from before the clash terms.
Each row: vocal clash bars (overlap bars with a vocal on both tracks), tonal bars (both tracks have music),
the two Camelot keys and their step distance.  "n/a" means the data is missing, not that the number is zero.
"""
import argparse
import glob
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
sys.path.insert(0, str(ROOT / "Tools"))
import phrase_blend as pb  # noqa: E402
import score_blends as sb  # noqa: E402
from measure_section_content import ENV_DIR, MATCH  # noqa: E402

OUT = ROOT / "Documentation" / "Golden Sections" / "Vocal Key 2026-11-24 DFTD.json"


def _stem_file(env_dir, word):
    hits = [f for f in glob.glob(env_dir + "/SECTIONS_STEM_*.json") if word in f]
    if len(hits) != 1:
        raise SystemExit("expected one SECTIONS_STEM file for %r, found %d" % (word, len(hits)))
    return hits[0]


def answer_key_vocal_flags(regions, lead, spb, n_bars):
    """Per-bar vocal flags on the answer key's bar clock, from the regions of at least MIN_CLASH_VOCAL_BARS
    bars only (short stabs never count, the same rule as plan_mix_decisions.vocal_bars)."""
    import plan_mix_decisions as pmd
    regions = [r for r in regions if len(r) >= 4 and r[3] - r[2] >= pmd.MIN_CLASH_VOCAL_BARS]
    return [int(any(r[0] <= (b + 0.5 + lead) * spb < r[1] for r in regions))
            for b in range(int(math.ceil(n_bars)))]


def build_dftd(env_dir=ENV_DIR, out=OUT):
    """has_vocal on the answer key's bar clock: bar b spans seconds [(b + lead) * bar, (b + 1 + lead) * bar)
    exactly as has_kick does, and counts as vocal when its midpoint is inside a sustained vocal region."""
    import plan_mix_decisions as pmd
    key = json.loads(sb.KEY.read_text(encoding="utf-8"))
    result = {"what": "Per-bar vocal flags (detector signals vocal_regions, on the answer key's bar clock) and "
                      "Camelot keys (MIK tags then MIK database) of the DFTD answer-key tracks. Made by "
                      "Tools/measure_vocal_key.py build-dftd.", "tracks": {}}
    for name, t in key["tracks"].items():
        if name not in MATCH:
            continue
        detector = json.loads(Path(_stem_file(env_dir, MATCH[name])).read_text(encoding="utf-8"))
        lead = float(t.get("lead_in_bars", 0.0))
        spb = 240.0 / t["bpm"]
        flags = answer_key_vocal_flags(detector["signals"]["vocal_regions"], lead, spb, t["n_bars"])
        wavs = glob.glob(str(Path(env_dir).parent / "Audio" / ("*%s*.wav" % MATCH[name])))
        camelot = pmd.read_camelot(Path(wavs[0])) if len(wavs) == 1 else None
        result["tracks"][name] = {"has_vocal": flags, "key": camelot}
        print("%-60s vocal bars %3d of %d, key %s" % (name[:60], sum(flags), len(flags), camelot))
    Path(out).write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    print("wrote", out)


def _cell(value, fmt="%g"):
    return "n/a" if value is None else fmt % value


def _row(plan):
    if plan is None:
        return "no plan"
    keys = "%s>%s %s steps" % (plan.key_pair + (plan.key_steps,)) if plan.key_pair else "key n/a"
    return "vocal %s | tonal %s | %s | cost %.2f" % (
        _cell(plan.vocal_clash_bars), _cell(plan.key_tonal_bars if plan.key_pair else None), keys, plan.cost)


def dftd_table(out=print):
    key = json.loads(sb.KEY.read_text(encoding="utf-8"))
    content = sb._read_content()
    out("DFTD answer key: Sam's placement against the planner's pick (weights vocal %g, key %g)"
        % (pb.W_VOCAL_CLASH, pb.W_KEY_CLASH))
    rows = []
    for i, b in enumerate(key["blends"], 1):
        o = sb.prepare(key["tracks"][b["out"]], b["out"], "BOTTOM END", content)
        n = sb.prepare(key["tracks"][b["in"]], b["in"], "BOTTOM END", content)
        plan = pb.plan_blend(o, n, r6=pb.R6_BE)
        sam = sb._sam_plan(o, n, sb.sam_numbers(b, o), pb.R6_BE)
        out("%d. %s -> %s" % (i, b["out"][:28], b["in"][:28]))
        out("   Sam    : %s" % _row(sam))
        out("   planner: %s" % _row(plan))
        rows.append((sam, plan))
    return rows


def tweak_table(args, out=print):
    import score_tweak as st
    import tweak_replay as tr
    names = [t["name"] for t in tr.read_als(args.baseline)]
    tracks = st.planner_tracks_from_project(args.project, names)
    decisions = json.loads(args.decisions.read_text(encoding="utf-8")) if args.decisions else None
    dropped = (tr.parse_dropped_loops(args.pipeline_log.read_text(encoding="utf-8", errors="replace"))
               if args.pipeline_log else None)
    result = tr.replay(args.baseline, args.tweak, tracks, decisions=decisions, dropped=dropped, owner="Sam")
    out("Tech House tweak: Sam's placement against the planner's pick (weights vocal %g, key %g)"
        % (pb.W_VOCAL_CLASH, pb.W_KEY_CLASH))
    planner = tr.match_planner_names(names, tracks)
    for rec in result["transitions"]:
        out("T%d. %s -> %s%s" % (rec["transition"], rec["out"][:26], rec["in"][:26],
                                  "  (Sam kept the planner's placement)" if rec.get("kept") else ""))
        o, n = planner[rec["out"]], planner[rec["in"]]
        for label, placement in (("Sam    ", rec.get("sam_scored")), ("planner", rec.get("planner_top"))):
            if placement is None:
                out("   %s: not scored (outside the planner's terms)" % label)
                continue
            plan = pb._score(o, n, placement["entry"], placement["swap"], placement["end"],
                             placement["intro_loop_phrase_end"], placement["intro_loop_bars"],
                             pb.phrase_points(o), pb.phrase_points(n), pb.R6_BE, placement["intro_cut_bars"])
            out("   %s: %s" % (label, _row(plan)))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["build-dftd", "dftd", "tweak"])
    ap.add_argument("--env-dir", default=ENV_DIR)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--tweak", type=Path)
    ap.add_argument("--project", type=Path)
    ap.add_argument("--decisions", type=Path)
    ap.add_argument("--pipeline-log", type=Path)
    ap.add_argument("--old", action="store_true", help="clash weights zero: the pick from before the terms")
    args = ap.parse_args(argv)
    if args.old:
        pb.W_VOCAL_CLASH = pb.W_KEY_CLASH = 0.0
    if args.what == "build-dftd":
        build_dftd(args.env_dir, args.out)
    elif args.what == "dftd":
        dftd_table()
    else:
        if not (args.baseline and args.tweak and args.project):
            ap.error("tweak needs --baseline, --tweak and --project")
        tweak_table(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
