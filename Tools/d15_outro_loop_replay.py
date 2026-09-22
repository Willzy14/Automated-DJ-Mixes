"""D15 burn-list check: does the outgoing-outro loop targeting fix (candidate
ordering, dead last-resort fallback) change any pair's decision across the
full 14.08.26 corpus, and does it change the RIGHT ones?

Sam found (by eye in Ableton) that `plan_fill_or_cut`'s outgoing-tail loop
sometimes targets a raw kick-gap landmark instead of a reachable, cleaner
`section:*` boundary (T1), and sometimes abandons a correctly-identified
target with zero trace when no candidate's audio passes the quality gate
(T3). This script replays every ordered pair in the 380-pair corpus and
records each transition's outgoing-outro-loop decision (target name, reps,
whether a loop fired at all, alignment_policy) so a before/after run can be
diffed and every changed verdict read by a human, not just counted -
matching the D9 replay's own discipline (`Tools/d9_cue_signal_replay.py`).

Usage (from a repo/worktree root):
    PYTHONPATH=Source python Tools/d15_outro_loop_replay.py --out FILE.json
    PYTHONPATH=Source python Tools/d15_outro_loop_replay.py --diff BEFORE.json AFTER.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

STEM_DIR = ROOT / "Test Project" / "14.08.26" / "_Stem Analysis"


def _load_tracks(AE):
    tracks = {}
    for f in sorted(STEM_DIR.glob("SECTIONS_STEM_*.json")):
        key = f.name.replace("SECTIONS_STEM_", "").replace(".json", "")
        tracks[key] = AE.load_track(f)
    return tracks


def _outro_loop_record(AE, o, i, al):
    """The single outgoing_tail spec (if any) plus enough context to judge
    whether the target changed for a good or bad reason."""
    rec = {"alignment_policy": al.alignment_policy}
    try:
        specs = AE.plan_fill_or_cut(o, i, al)
    except Exception as exc:
        rec.update({"status": "raise", "error": type(exc).__name__,
                     "msg": str(exc)[:200]})
        return rec
    tail = next((s for s in specs if s.kind == "outgoing_tail"), None)
    if tail is None:
        rec.update({"status": "ok", "loop_source": "none"})
        return rec
    rec.update({
        "status": "ok",
        "loop_source": "outro",
        "target_marker_name": tail.target_marker_name,
        "target_marker_bar": tail.target_marker_bar,
        "reps": tail.reps,
        "partial_bars": round(tail.partial_bars, 3),
        "source_start_bar": tail.source_start_bar,
        "source_end_bar": tail.source_end_bar,
        "note": tail.note,
    })
    return rec


def compute_rows(AE, tracks):
    """Every ordered pair's outgoing-outro-loop decision."""
    rows = []
    for out_name in sorted(tracks):
        for in_name in sorted(tracks):
            if out_name == in_name:
                continue
            rec = {"out": out_name, "in": in_name}
            try:
                al = AE.align_pair(tracks[out_name], tracks[in_name])
            except Exception as exc:
                rec.update({"status": "align_raise", "error": type(exc).__name__,
                             "msg": str(exc)[:200]})
            else:
                rec.update(_outro_loop_record(AE, tracks[out_name], tracks[in_name], al))
            rows.append(rec)
    return rows


def _key(row):
    return (row["out"], row["in"])


def diff(before_path: Path, after_path: Path) -> int:
    before = {_key(r): r for r in json.loads(before_path.read_text(encoding="utf-8"))}
    after = {_key(r): r for r in json.loads(after_path.read_text(encoding="utf-8"))}
    if set(before) != set(after):
        print(f"PAIR SET MISMATCH: before={len(before)} after={len(after)}")
        return 2

    changed = []
    for key, b in before.items():
        a = after[key]
        fields = ("status", "loop_source", "target_marker_name", "reps",
                   "partial_bars", "error")
        if any(b.get(f) != a.get(f) for f in fields):
            changed.append((key, b, a))

    by_policy = {}
    for key, b, a in changed:
        by_policy.setdefault(a.get("alignment_policy", "?"), []).append((key, b, a))

    print(f"TOTAL pairs: {len(before)}  CHANGED: {len(changed)}  "
          f"UNCHANGED: {len(before) - len(changed)}")
    print()
    for policy, rows in sorted(by_policy.items()):
        print(f"=== alignment_policy={policy}  ({len(rows)} changed) ===")
        for (out_name, in_name), b, a in rows:
            print(f"  {out_name} -> {in_name}")
            print(f"    before: loop_source={b.get('loop_source')} "
                  f"target={b.get('target_marker_name')} reps={b.get('reps')} "
                  f"status={b.get('status')}")
            print(f"    after:  loop_source={a.get('loop_source')} "
                  f"target={a.get('target_marker_name')} reps={a.get('reps')} "
                  f"status={a.get('status')}")
        print()
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--diff", nargs=2, type=Path, metavar=("BEFORE", "AFTER"), default=None)
    args = p.parse_args()

    if args.diff:
        raise SystemExit(diff(*args.diff))

    import align_engine as AE
    tracks = _load_tracks(AE)
    print(f"Loaded {len(tracks)} tracks from {STEM_DIR}")
    rows = compute_rows(AE, tracks)
    n_looped = sum(1 for r in rows if r.get("loop_source") == "outro")
    n_none = sum(1 for r in rows if r.get("loop_source") == "none")
    n_raise = sum(1 for r in rows if "raise" in r.get("status", ""))
    print(f"{len(rows)} ordered pairs: {n_looped} looped, {n_none} no-loop, "
          f"{n_raise} raised")

    if args.out:
        args.out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
