"""D9 burn-list check: does enabling `rescue,deep,phrase` together ever move a
pair the DEFAULT config already aligns, and how many of the 113 default-raise
pairs does the combination rescue?

Sam's long-intro rule (`deep`/`phrase`, PHRASE_BACKSTEP_BARS anchors) and his
short-outro rule (`rescue`, tail_anchor_rescue) are built, tested and merged,
but the standard `/mix` Phase 2a command passes no --cue-signals, so neither
ever runs. Both are documented as RESCUE-ONLY: `_incoming_swap_anchors` only
folds deep/phrase anchors into `rescue_anchors`, which `_align_pair_landmark_aware`
tries only after the normal drop-anchor search returns None; `tail_anchor_rescue`
is tried only after THAT also fails. So flipping all three on should be able to
turn a raise into an align, but should never touch a pair the default config
already aligns. This script proves that empirically over the full 380-pair
14.08.26 corpus rather than trusting the code comment.

Usage (from a repo/worktree root):
    PYTHONPATH=Source python Tools/d9_cue_signal_replay.py [--out FILE.json]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

STEM_DIR = ROOT / "Test Project" / "14.08.26" / "_Stem Analysis"
FROZEN_BASELINE = (ROOT / "Documentation" / "Plans" / "arranger-signal-rewiring"
                   / "baseline_alignments.json")

# Same pinned surface test_alignment_baseline.py uses for an OK row.
PINNED = ("handoff_bar_out", "arr_offset_bars", "overlap_bars", "swap_progress",
          "handoff_kind", "alignment_policy", "n_paired_cues", "paired_cue_bars",
          "anchor_bar_in", "score", "paired_cues", "notes",
          "overlap_policy", "plan")
RAISE_PINNED = ("error", "msg")

# Mirrors propose_arrangement._apply_cue_signals' `known` map — the D9 check
# is specifically the three signals Sam asked about (his long-intro rule +
# his short-outro rule), not the full --cue-signals vocabulary.
SIGNAL_MAP = {"phrase": "incoming_phrase_anchors", "deep": "deep_intro_anchor",
              "rescue": "tail_anchor_rescue"}


def _load_tracks(AE):
    tracks = {}
    for f in sorted(STEM_DIR.glob("SECTIONS_STEM_*.json")):
        key = f.name.replace("SECTIONS_STEM_", "").replace(".json", "")
        tracks[key] = AE.load_track(f)
    return tracks


def _round_floats(obj):
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v) for v in obj]
    return obj


def _spec_dict(spec):
    return _round_floats(dataclasses.asdict(spec))


def _derive_overlap_policy(AE, specs):
    for fc in specs:
        if (fc.kind == "outgoing_tail" and (fc.reps >= 1 or fc.partial_bars > 0)
                and fc.target_marker_name):
            return "named_landmark_64"
        if fc.kind == "incoming_intro" and fc.reps >= 1:
            return "named_landmark_64"
    return "standard_48"


def _plan_record(AE, o, i, al):
    try:
        specs = AE.plan_fill_or_cut(o, i, al)
    except Exception as exc:
        return ({"status": "raise", "error": type(exc).__name__,
                 "msg": str(exc)[:160]}, None)
    return ({"status": "ok", "specs": [_spec_dict(s) for s in specs]},
            _derive_overlap_policy(AE, specs))


def _populate_ok(AE, rec, al, t_o, t_i):
    rec.update({
        "status": "ok",
        "handoff_bar_out": al.handoff_bar_out,
        "arr_offset_bars": al.arr_offset_bars,
        "overlap_bars": al.overlap_bars,
        "swap_progress": (round(al.swap_progress, 6)
                          if al.swap_progress is not None else None),
        "handoff_kind": al.handoff_kind,
        "alignment_policy": al.alignment_policy,
        "n_paired_cues": len(al.paired_cues or []),
        "paired_cue_bars": [p["arrangement_bar"] for p in (al.paired_cues or [])],
        "anchor_bar_in": round(float(al.anchor_bar_in), 6),
        "score": al.score,
        "paired_cues": list(al.paired_cues or []),
        "notes": list(al.notes),
    })
    plan, overlap_policy = _plan_record(AE, t_o, t_i, al)
    rec["plan"] = plan
    rec["overlap_policy"] = overlap_policy


def compute_rows(AE, tracks):
    """Every ordered pair's decision under AE's CURRENT CUE_CONFIG."""
    rows = []
    for out_name in sorted(tracks):
        for in_name in sorted(tracks):
            if out_name == in_name:
                continue
            rec = {"out": out_name, "in": in_name}
            try:
                al = AE.align_pair(tracks[out_name], tracks[in_name])
            except Exception as exc:
                rec.update({"status": "raise", "error": type(exc).__name__,
                            "msg": str(exc)[:160]})
            else:
                _populate_ok(AE, rec, al, tracks[out_name], tracks[in_name])
            rows.append(rec)
    return rows


def _key(row):
    return (row["out"], row["in"])


def _diff_rows(expected_by_key, actual_by_key, pinned):
    moved, newly_ok, newly_raise = [], [], []
    for key, exp in expected_by_key.items():
        act = actual_by_key.get(key)
        if act is None:
            continue
        if exp["status"] != act["status"]:
            (newly_ok if act["status"] == "ok" else newly_raise).append(key)
            continue
        if act["status"] == "ok":
            diffs = [f"{f}: {exp.get(f)!r} -> {act.get(f)!r}"
                     for f in pinned if exp.get(f) != act.get(f)]
        else:
            diffs = [f"{f}: {exp.get(f)!r} -> {act.get(f)!r}"
                     for f in RAISE_PINNED if exp.get(f) != act.get(f)]
        if diffs:
            moved.append((key, diffs))
    return moved, newly_ok, newly_raise


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None,
                        help="Write the full row-level report as JSON here.")
    parser.add_argument("--signals", default="rescue,deep,phrase",
                        help="Comma-separated signals to combine (default: the D9 set).")
    args = parser.parse_args()

    if not STEM_DIR.exists():
        print(f"FATAL: corpus dir missing: {STEM_DIR}")
        return 1

    import align_engine as AE

    tracks = _load_tracks(AE)
    n = len(tracks)
    print(f"corpus: {n} tracks, {n * (n - 1)} ordered pairs, {STEM_DIR}")

    # Pass 1: default CUE_CONFIG (fresh, not the frozen file) — every ordered pair.
    AE.CUE_CONFIG = AE.CueConfig()
    default_rows = compute_rows(AE, tracks)
    default_by_key = {_key(r): r for r in default_rows}
    default_ok = sum(1 for r in default_rows if r["status"] == "ok")
    print(f"pass 1 (default, fresh): {default_ok} ok, {len(default_rows) - default_ok} raise")

    # Cross-check against the frozen baseline this repo already trusts — if this
    # drifts, the corpus or code changed since 2026-08-20 and the diff below is
    # not trustworthy until that's understood.
    if FROZEN_BASELINE.exists():
        frozen = json.loads(FROZEN_BASELINE.read_text(encoding="utf-8"))
        frozen_by_key = {tuple(r[k] for k in ("out", "in")): r for r in frozen["rows"]}
        moved, newly_ok, newly_raise = _diff_rows(frozen_by_key,
                                                   {k: json.loads(json.dumps(v))
                                                    for k, v in default_by_key.items()},
                                                   PINNED)
        if moved or newly_ok or newly_raise:
            print(f"WARNING: fresh default sweep disagrees with the frozen baseline "
                  f"({len(moved)} changed, {len(newly_ok)} newly ok, "
                  f"{len(newly_raise)} newly raise) — the diff below compares fresh "
                  f"vs fresh so it is still valid, but the frozen baseline itself may "
                  f"be stale. Run the full suite / test_alignment_baseline.py first.")
        else:
            print(f"cross-check OK: fresh default sweep matches the frozen baseline "
                  f"byte-for-byte ({len(default_rows)} pairs)")

    # Pass 2: the D9 combination.
    wanted = {s.strip() for s in args.signals.split(",") if s.strip()}
    unknown = wanted - set(SIGNAL_MAP)
    if unknown:
        print(f"FATAL: unknown signal(s) {sorted(unknown)}; known: {sorted(SIGNAL_MAP)}")
        return 1
    AE.CUE_CONFIG = AE.CueConfig(**{SIGNAL_MAP[s]: True for s in wanted})
    combined_rows = compute_rows(AE, tracks)
    combined_by_key = {_key(r): r for r in combined_rows}
    combined_ok = sum(1 for r in combined_rows if r["status"] == "ok")
    print(f"pass 2 ({','.join(sorted(wanted))}): {combined_ok} ok, "
          f"{len(combined_rows) - combined_ok} raise")

    moved, newly_ok, newly_raise = _diff_rows(default_by_key, combined_by_key, PINNED)

    print()
    print(f"RESULT: {len(moved)} changed (an already-OK pair whose decision moved — "
          f"expected 0), {len(newly_ok)} newly align (a raise the combination "
          f"rescued), {len(newly_raise)} newly raise (expected 0 — an OK pair the "
          f"combination broke), of {len(default_rows)} pairs")
    print()

    if moved:
        print(f"!! {len(moved)} CHANGED pairs (default already aligned these — "
              f"this is the unsafe case D9 is checking for):")
        for key, diffs in moved:
            print(f"  CHANGED  {key[0][:40]} -> {key[1][:40]}")
            for d in diffs:
                print(f"             {d}")

    if newly_raise:
        print(f"!! {len(newly_raise)} NEWLY RAISE pairs (default aligned, combination "
              f"broke it — also unsafe):")
        for key in newly_raise:
            print(f"  NOW RAISES  {key[0][:40]} -> {key[1][:40]}")

    if newly_ok:
        print(f"{len(newly_ok)} rescued (default raised, combination aligns):")
        for key in newly_ok:
            row = combined_by_key[key]
            print(f"  RESCUED  {key[0][:40]} -> {key[1][:40]}  "
                  f"(handoff_bar_out={row['handoff_bar_out']}, "
                  f"overlap_bars={row['overlap_bars']}, "
                  f"anchor_bar_in={row['anchor_bar_in']}, "
                  f"n_paired_cues={row['n_paired_cues']})")

    still_raising = sorted(set(default_by_key) - set(newly_ok)
                            & {k for k, r in default_by_key.items() if r["status"] == "raise"})
    n_default_raise = sum(1 for r in default_rows if r["status"] == "raise")
    print()
    print(f"of {n_default_raise} default-raise pairs: {len(newly_ok)} rescued, "
          f"{n_default_raise - len(newly_ok)} still raise")

    if args.out:
        args.out.write_text(json.dumps({
            "corpus": "Test Project/14.08.26", "n_tracks": n, "n_pairs": len(default_rows),
            "signals": sorted(wanted),
            "default_ok": default_ok, "default_raised": len(default_rows) - default_ok,
            "combined_ok": combined_ok, "combined_raised": len(combined_rows) - combined_ok,
            "n_changed": len(moved), "n_newly_ok": len(newly_ok), "n_newly_raise": len(newly_raise),
            "changed": [{"out": k[0], "in": k[1], "diffs": d} for k, d in moved],
            "newly_raise": [{"out": k[0], "in": k[1]} for k in newly_raise],
            "newly_ok": [{"out": k[0], "in": k[1], **combined_by_key[k]} for k in newly_ok],
            "default_rows": default_rows,
            "combined_rows": combined_rows,
        }, sort_keys=True, indent=1), encoding="utf-8")
        print(f"\nfull report written: {args.out}")

    return 0 if not (moved or newly_raise) else 2


if __name__ == "__main__":
    sys.exit(main())
