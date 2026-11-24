"""Measure what the listener hears at every join of a bounced mix, on the bounce itself, and draw each join.

Usage:  python Tools/check_bounce.py <bounce.wav> [--report ARRANGEMENT_REPORT.json --plan MIX_PLAN.json | --als <ALS>]
                                     [--out <dir>] [--ranges <ranges.json>] [--no-flam]
        python Tools/check_bounce.py --calibrate <manifest.json> --corpus-root <dir> --out <dir> [--only label,label]

One line per join (loudness step and blend peak in LU, worst tonal band step in dB, 40-150 Hz level against the sides with
its L/R correlation and worst bar, stereo width step, kick flam), a JSON file and a picture per join in --out (default: a
folder "Bounce Check" beside the bounce). Look at the pictures: the numbers are a summary.
The join positions come from the plan and report (default: the two files beside the bounce), or from an ALS when the
arrangement was edited by hand (a hand-edited set has no plan that matches it). The bounce must start at arrangement beat 0.
With --ranges (default: Config/bounce_check_ranges.json when it exists) each join also gets the words "inside the observed
range" or "outside by X" against the joins Sam accepted. The tool never decides pass or fail from a limit nobody has set.
--calibrate runs every bounce of a manifest, writes one json per bounce and the ranges file (the min, median and max of each
measurement across the joins of each group, and how many joins that is); a bounce whose json is already there is not redone.
Report only, never a verdict on the build: every run that completes writes ATTENTION.md beside the pictures (and the same text in the
json under "attention"): one NEEDS A LOOK line per join with a measure of direct audible meaning outside the accepted range by a
margin (bounce_check.ATTENTION_MARGINS) or a kick flam SEEN, warnings when the numbers cannot be trusted (START WARNING, map does not
fit, a measure that could not be computed for a join, other per-join notes, no or thin ranges, no flam run or judgeable), or "no join needs
a look". The last stdout line is "BOUNCE CHECK: ...". Once the tool is past argument parsing ATTENTION.md always exists: a check that
could not run writes "bounce check did not run (reason): no measurements".
Exit codes: 0 done (whatever was found); 2 the check could not run (message to stderr); 3 the beat to seconds map does not fit the bounce
(the report and ATTENTION.md are still written).
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXIT_OK, EXIT_COULD_NOT_RUN, EXIT_BAD_MAP = 0, 2, 3
DEFAULT_RANGES = REPO / "Config" / "bounce_check_ranges.json"


def say(text, stream=None):
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    print(text.encode(encoding, errors="replace").decode(encoding), file=stream)


def load_inputs(bc, wav, report=None, plan=None, als=None):
    wav = Path(wav)
    if als:
        return bc.joins_from_als(als), "ALS " + str(als)
    here = wav.resolve().parent
    plan = Path(plan) if plan else here / "MIX_PLAN.json"
    report = Path(report) if report else here / "ARRANGEMENT_REPORT.json"
    for f in (plan, report):
        if not f.exists():
            raise FileNotFoundError(f"{f} not found: pass --plan and --report, or --als for a hand-edited arrangement")
    return bc.joins_from_plan(json.loads(plan.read_text(encoding="utf-8")),
                              json.loads(report.read_text(encoding="utf-8"))), f"plan {plan.name} + report {report.name}"


def run_one(bc, wav, out, ranges=None, flam=True, report=None, plan=None, als=None, say_progress=True):
    (tempo, joins, last), source = load_inputs(bc, wav, report, plan, als)
    result = bc.check_bounce(wav, tempo, joins, last, out_dir=out, flam=flam, ranges=ranges,
                             progress=(lambda m: say("  ... " + m)) if say_progress else None)
    result["join_source"] = source
    return result


def print_report(bc, result, ranges):
    m = result["mapping"]
    say(f"{Path(result['bounce']).name}: {result['duration_s']:.1f} s, joins from {result['join_source']}")
    say(f"map: arrangement ends at {m['map_end_s']:.1f} s, bounce is {m['bounce_s']:.1f} s long ({m['tail_s']:+.1f} s tail), "
        f"{m['tempo_points']} tempo points: " + ("fits" if m["ok"] else "DOES NOT FIT"))
    for problem in m["problems"]:
        say("MAP PROBLEM: " + problem)
    start = m.get("start", {})
    if start.get("first_sound_s") is not None or start.get("grid_phase_ms") is not None:
        say("start: first sound at " + ("none" if start.get("first_sound_s") is None else f"{start['first_sound_s']:.1f} s") +
            ", kick grid " + ("not clear" if start.get("grid_phase_ms") is None else f"{start['grid_phase_ms']:+.0f} ms") + " from the beat lines")
    for warning in start.get("warnings", []):
        say("START WARNING: " + warning)
    for res in result["joins"]:
        say(bc.summary_line(res, ranges))
    if result.get("peak_memory_mb"):
        say(f"peak memory {result['peak_memory_mb']:.0f} MB")


def did_not_run(folder, reason):
    """ATTENTION.md for a check that could not run, so the file exists whenever the tool was invoked and says there are no measurements.
    Returns the summary line for stdout."""
    try:
        from bounce_check import ascii_text
    except Exception:
        def ascii_text(text):
            return text.encode("ascii", "replace").decode("ascii")
    text = ascii_text(f"bounce check did not run ({reason}): no measurements")
    path = Path(folder) / "ATTENTION.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="ascii")
    except OSError:
        return f"BOUNCE CHECK: did not run, and {path} could not be written: no measurements"
    return f"BOUNCE CHECK: did not run: no measurements: see {path}"


def load_ranges(path, group="accepted"):
    if not path or not Path(path).exists():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if group not in data.get("groups", {}):
        raise ValueError(f"{path} has no group '{group}' (it has: {', '.join(data.get('groups', {})) or 'none'}): "
                         "rebuild it with --calibrate from a manifest that has an accepted group, or pass --range-group")
    return data["groups"][group]["measurements"]


def calibrate(bc, args):
    manifest = json.loads(Path(args.calibrate).read_text(encoding="utf-8"))
    root = Path(args.corpus_root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    for entry in manifest["entries"]:
        label = entry["label"]
        if only and label not in only:
            continue
        jpath = out / (label.replace(" ", "_").replace("'", "") + ".json")
        if jpath.exists():
            say(f"{label}: already measured ({jpath.name})")
            continue
        say(f"{label}: measuring")
        wav = root / entry["bounce"]
        kw = {"als": root / entry["als"]} if "als" in entry else {"plan": root / entry["plan"], "report": root / entry["report"]}
        result = run_one(bc, wav, out / (jpath.stem + "_pictures"), flam=not args.no_flam, say_progress=False, **kw)
        result["label"], result["group"] = label, entry["group"]
        bc.write_json(result, jpath)
        print_report(bc, result, None)
    if only:
        return EXIT_OK
    groups = {}
    for entry in manifest["entries"]:
        jpath = out / (entry["label"].replace(" ", "_").replace("'", "") + ".json")
        if not jpath.exists():
            say(f"{entry['label']}: no json, left out of the ranges")
            continue
        data = json.loads(jpath.read_text(encoding="ascii"))
        for g in (entry["group"], "all"):
            slot = groups.setdefault(g, {"bounces": [], "joins": []})
            slot["bounces"].append(entry["label"])
            slot["joins"].extend({"join": j.get("scalars", {}), "label": entry["label"]} for j in data["joins"])
    if "accepted" not in groups:
        raise ValueError("the manifest has no bounce in the group 'accepted' (or none of them was measured): there would be no range "
                         "to judge a join against, so no ranges file was written")
    ranges = {"basis": manifest.get("basis", ""), "groups": {}}
    for g, slot in groups.items():
        used = [item["join"] for item in slot["joins"] if item["join"]]
        measurements = bc.ranges_from_scalars(used)
        ranges["groups"][g] = {"bounces": slot["bounces"], "n_joins": len(slot["joins"]), "n_joins_used": len(used),
                               "thin_measurements": sorted(n for n, d in measurements.items() if d["thin"]), "measurements": measurements}
    (out / "ranges.json").write_text(json.dumps(ranges, indent=1), encoding="ascii")
    for g, slot in ranges["groups"].items():
        say(f"group {g}: {slot['n_joins']} joins from {len(slot['bounces'])} bounces, {slot['n_joins_used']} usable "
            "(a join with a silent window feeds no range)")
        for name, d in slot["measurements"].items():
            say(f"  {name:34s} n={d['n']:3d}  min {d['min']:8.2f}  median {d['median']:8.2f}  max {d['max']:8.2f}" +
                (f"   THIN: {d['n']} joins" if d["thin"] else ""))
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bounce", nargs="?", help="the bounced mix (WAV)")
    ap.add_argument("--report", help="ARRANGEMENT_REPORT.json (default: beside the bounce)")
    ap.add_argument("--plan", help="MIX_PLAN.json (default: beside the bounce)")
    ap.add_argument("--als", help="read the joins from this ALS instead of the plan and report")
    ap.add_argument("--out", help="folder for the pictures and the json (default: 'Bounce Check' beside the bounce)")
    ap.add_argument("--ranges", help="ranges json (default: Config/bounce_check_ranges.json if it exists)")
    ap.add_argument("--range-group", default="accepted")
    ap.add_argument("--no-flam", action="store_true", help="skip the kick flam step (the slow one)")
    ap.add_argument("--calibrate", help="manifest of bounces to run and build ranges from")
    ap.add_argument("--corpus-root", help="folder the manifest paths are relative to")
    ap.add_argument("--only", help="with --calibrate: only these labels (comma separated), no ranges written")
    args = ap.parse_args(argv)
    attention_folder = None
    try:
        sys.path.insert(0, str(REPO / "Source"))
        import bounce_check as bc
        if args.calibrate:
            if not (args.corpus_root and args.out):
                ap.error("--calibrate needs --corpus-root and --out")
            return calibrate(bc, args)
        if not args.bounce:
            ap.error("give a bounce, or --calibrate")
        out = (Path(args.out) if args.out else Path(args.bounce).resolve().parent / "Bounce Check").resolve()
        attention_path = out / "ATTENTION.md"
        attention_folder = out
        attention_path.unlink(missing_ok=True)          # an old report must not stand for this run: no file means the check did not run
        ranges = load_ranges(args.ranges or DEFAULT_RANGES, args.range_group)
        result = run_one(bc, args.bounce, out, ranges=ranges, flam=not args.no_flam, report=args.report, plan=args.plan, als=args.als)
        att = result["attention"] = bc.attention(result, ranges, out)
        bc.write_json(result, out / "bounce_check.json")
        bc.write_attention(att, attention_path)
        print_report(bc, result, ranges)
        say(f"LOOK AT THESE PICTURES: {out}")
        for line in att["text"].splitlines():
            say(line)
        say(bc.attention_summary(att, attention_path))
        return EXIT_OK if result["mapping"]["ok"] else EXIT_BAD_MAP
    except SystemExit:
        raise
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        say(f"bounce check could not run: {reason}", sys.stderr)
        if attention_folder is not None:
            say(did_not_run(attention_folder, reason))
        return EXIT_COULD_NOT_RUN


if __name__ == "__main__":
    sys.exit(main())
