"""Compare cached legacy reader outputs byte for byte with an exported main Source.

Run from this repo with the pipeline dependencies installed. Example:
    python Tools/legacy_identity.py PROJECT --main-source MAIN/Source --track "Exact track stem"

Omit --track to compare the whole layout and each adjacent alignment. Optionally
pass --automation-als and --arrangement-report to compare automation plans too.
The project is read-only. The hints gate writes its report to a temporary copy.
"""

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def _name(text):
    return html.unescape(text).removesuffix(".wav")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _prepare_gate(project, gate, version, track):
    review = Path("Sections Review") / f"Sections_V{version}.json"
    hints = Path("Hints") / "track_hints.json"
    for rel in (review, hints):
        data = _read_json(project / rel)
        if track is not None:
            data = {k: v for k, v in data.items() if _name(k) == track}
        target = gate / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
    for folder, pattern in (("_Stem Analysis", "SECTIONS_STEM_*.json"),
                            ("Output", "ARRANGEMENT_REPORT*.json")):
        (gate / folder).mkdir()
        for path in (project / folder).glob(pattern):
            if (folder == "_Stem Analysis" and track is not None
                    and path.stem.removeprefix("SECTIONS_STEM_") != track):
                continue
            shutil.copy2(path, gate / folder / path.name)


def _snapshot(args, gate):
    from align_engine import _mix_cues, align_pair, load_loop_quality_context, load_track
    from alignment_feasibility import load_tracks
    from apply_automation import _load_arrangement_report, plan_transitions
    from automated_dj_mixes.phrase_viz import segments_from_stem_sections
    from extract_sections_als import parse_sections_als
    from propose_arrangement import fill_missing_bpm_from_stem_grid, ordered_tracks
    from stem_detector import _check_cached_sections, hints_from_stem_result
    from validate_hints_vs_sections import validate

    directory = args.project / "_Stem Analysis"
    layout = args.project / "Output" / f"Sections V{args.version}.als"
    sections = parse_sections_als(layout)
    if args.track is not None:
        sections = {k: v for k, v in sections.items() if _name(k) == args.track}
        if not sections:
            raise ValueError(f"No such track in {layout}: {args.track!r}")
    order = [_name(name) for name in sections]
    payload = {"tracks": {}, "alignments": [], "hints_gate": validate(gate, args.version)}
    tracks = {}
    for stem in sorted((gate / "_Stem Analysis").glob("SECTIONS_STEM_*.json")):
        source = directory / stem.name
        data = _read_json(source)
        model = load_track(source)
        tracks[model.name] = model
        row = asdict(model)
        row.pop("section_mode", None)
        quality = load_loop_quality_context(model.envelope_cache_path)
        payload["tracks"][model.name] = {
            "track": row,
            "cues": _mix_cues(model),
            "hints": hints_from_stem_result(data),
            "cache_check": _check_cached_sections(source, model.name)[1],
            "quality_clock": [quality.bpm, quality.downbeat_sec, quality.hop_sec],
            "layout": [asdict(s) for s in segments_from_stem_sections(data)],
        }
    if not tracks or any(name not in tracks for name in order):
        raise ValueError(f"Missing stem analysis for tracks in {layout}")
    for out, incoming in zip(order, order[1:]):
        try:
            pair = asdict(align_pair(tracks[out], tracks[incoming]))
        except ValueError as exc:
            pair = str(exc)
        payload["alignments"].append(pair)
    payload["bpm_backfill"] = fill_missing_bpm_from_stem_grid(ordered_tracks(sections), directory)
    payload["feasibility_inputs"] = [(t.name, bpm) for t, bpm in load_tracks(gate)]
    if args.automation_als:
        swaps = _load_arrangement_report(args.automation_als, args.arrangement_report)
        payload["automation"] = [asdict(p) for p in plan_transitions(
            ordered_tracks(parse_sections_als(args.automation_als)), swaps,
        )]
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", type=Path)
    parser.add_argument("--main-source", required=True, type=Path)
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--track", help="Exact legacy track stem from the layout")
    parser.add_argument("--automation-als", type=Path)
    parser.add_argument("--arrangement-report", type=Path)
    parser.add_argument("--snapshots", type=Path, help="Save the two compared JSON snapshots")
    parser.add_argument("--worker", nargs=2, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if bool(args.automation_als) != bool(args.arrangement_report):
        parser.error("--automation-als and --arrangement-report must be used together")
    if args.worker:
        source, gate = args.worker
        sys.path.insert(0, str(source.resolve()))
        with redirect_stdout(sys.stderr):
            payload = _snapshot(args, gate)
        print(json.dumps(payload, sort_keys=True, default=str))
        return 0

    scratch = ROOT / "_scratch"
    scratch.mkdir(exist_ok=True)
    snapshots = {}
    with tempfile.TemporaryDirectory(dir=scratch, prefix="legacy-identity-") as directory:
        gate = Path(directory)
        _prepare_gate(args.project, gate, args.version, args.track)
        for label, source in (("main", args.main_source), ("worktree", ROOT / "Source")):
            if not (source / "align_engine.py").is_file():
                parser.error(f"No DJ Mixes reader modules in {source}")
            command = [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:],
                       "--worker", str(source), str(gate)]
            result = subprocess.run(command, stdout=subprocess.PIPE, check=True)
            snapshots[label] = result.stdout
            print(f"{label}: SHA-256 {hashlib.sha256(result.stdout).hexdigest()}", flush=True)
            if args.snapshots is not None:
                args.snapshots.mkdir(parents=True, exist_ok=True)
                (args.snapshots / f"{label}.json").write_bytes(result.stdout)
    if snapshots["main"] != snapshots["worktree"]:
        print("FAIL: legacy reader outputs differ", file=sys.stderr)
        return 1
    tracks = json.loads(snapshots["main"])["tracks"]
    print(f"PASS: byte-identical legacy reader output on {len(tracks)} track(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
