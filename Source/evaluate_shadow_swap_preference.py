"""Held-out evaluation of shadow_swap_preference (burn list C7 Step 1).

`canonicalize_pair_history.shadow_swap_preference` is a REPORT-ONLY
similarity-weighted guess at a swap-beat delta, wired into propose_arrangement.py's
report but never read by anything that chooses an arrangement. It is a
hypothesis, not a trusted signal - this script is the falsification test.

Method: leave-one-PROJECT-out (never leave-one-PAIR-out - transitions within
one mix are correlated, not independent samples; a project's own pairs must
never be allowed to predict themselves - Codex review, swap-first-redesign
plan). For every canonical pair, ask shadow_swap_preference for a prediction
using every OTHER project's pairs, then compare the prediction against what
Sam actually did.

The comparison that matters is not "is the shadow signal ever right" - it is
"does the shadow signal beat a trivial always-predict-zero baseline". A
sizeable share of real corrections are delta_beats=0 (verdict="correct" -
nothing moved), so a baseline that never predicts a move at all can look
deceptively good on raw pass rate alone. Both are reported side by side,
honestly, including on a corpus this small (currently ~30 canonical pairs
across ~5 projects) - this evaluation is UNDERPOWERED and the script says so
in its own output rather than letting a headline percentage overclaim.

Promotion past shadow mode (an actual nudge to a real arrangement decision)
requires this evaluation to show real skill over the baseline, per the
C6/C7/C8 plan's own Codex-reviewed sequencing. A pass here is necessary, not
sufficient - it does not by itself authorise anything past shadow mode.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from canonicalize_pair_history import (
    DELTA_TOLERANCE_BEATS, CanonicalPair, canonicalize, load_records,
    load_resolutions, shadow_swap_preference,
)


@dataclass(frozen=True)
class EvalRow:
    project: str
    pair_index: int
    true_delta: float
    shadow_delta: float | None       # None = no prediction made at all
    shadow_confidence: float | None
    shadow_within_tolerance: bool | None   # None when shadow_delta is None
    baseline_within_tolerance: bool        # "always predict 0" baseline


def evaluate(canonical_pairs: list[CanonicalPair],
            tolerance_beats: float = DELTA_TOLERANCE_BEATS) -> list[EvalRow]:
    """Run the leave-one-project-out loop. Pure function, no I/O - the
    corpus size here is small enough this never needs streaming."""
    rows: list[EvalRow] = []
    for held_out in canonical_pairs:
        prediction = shadow_swap_preference(
            held_out.bpm_out or 128.0,
            list(held_out.out_structure), list(held_out.in_structure),
            canonical_pairs, exclude_project=held_out.project,
        )
        if prediction is None:
            shadow_delta = None
            shadow_conf = None
            shadow_ok = None
        else:
            shadow_delta = prediction["suggested_delta_beats"]
            shadow_conf = prediction["confidence"]
            shadow_ok = abs(shadow_delta - held_out.delta_beats) <= tolerance_beats
        baseline_ok = abs(0.0 - held_out.delta_beats) <= tolerance_beats
        rows.append(EvalRow(
            project=held_out.project, pair_index=held_out.pair_index,
            true_delta=held_out.delta_beats, shadow_delta=shadow_delta,
            shadow_confidence=shadow_conf, shadow_within_tolerance=shadow_ok,
            baseline_within_tolerance=baseline_ok,
        ))
    return rows


def summarise(rows: list[EvalRow]) -> dict:
    total = len(rows)
    covered = [r for r in rows if r.shadow_delta is not None]
    uncovered = total - len(covered)
    shadow_hits = sum(1 for r in covered if r.shadow_within_tolerance)
    baseline_hits_on_covered = sum(1 for r in covered if r.baseline_within_tolerance)
    baseline_hits_overall = sum(1 for r in rows if r.baseline_within_tolerance)

    by_project: dict[str, dict] = defaultdict(lambda: {"total": 0, "covered": 0,
                                                        "shadow_hits": 0})
    for r in rows:
        by_project[r.project]["total"] += 1
        if r.shadow_delta is not None:
            by_project[r.project]["covered"] += 1
            if r.shadow_within_tolerance:
                by_project[r.project]["shadow_hits"] += 1

    return {
        "total_pairs": total,
        "covered_pairs": len(covered),
        "uncovered_pairs": uncovered,
        "shadow_hit_rate_on_covered": (shadow_hits / len(covered)) if covered else None,
        "baseline_hit_rate_on_covered": (baseline_hits_on_covered / len(covered)) if covered else None,
        "baseline_hit_rate_overall": (baseline_hits_overall / total) if total else None,
        "shadow_beats_baseline_on_covered": (
            (shadow_hits - baseline_hits_on_covered) if covered else None
        ),
        "by_project": dict(by_project),
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pair_history", type=Path)
    ap.add_argument("--resolutions", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None,
                    help="Write the full per-pair rows + summary as JSON")
    args = ap.parse_args()

    resolutions_path = args.resolutions
    if resolutions_path is None:
        candidate = args.pair_history.with_name("pair_history_resolutions.jsonl")
        resolutions_path = candidate if candidate.exists() else None

    records, malformed = load_records(args.pair_history)
    resolutions = load_resolutions(resolutions_path)
    result = canonicalize(records, resolutions)
    canonical_pairs = list(result.canonical)

    rows = evaluate(canonical_pairs)
    summary = summarise(rows)

    print(f"{len(canonical_pairs)} canonical pairs, "
          f"{len(result.conflicts)} conflicts excluded, "
          f"{len(malformed)} malformed excluded")
    print(f"Projects: {sorted(summary['by_project'])}")
    print()
    print(f"Coverage: {summary['covered_pairs']}/{summary['total_pairs']} pairs got "
          f"a shadow prediction ({summary['uncovered_pairs']} too dissimilar to "
          f"every other project's pairs)")
    if summary["covered_pairs"]:
        print(f"Shadow hit rate (within {DELTA_TOLERANCE_BEATS:g} beats), covered pairs only: "
              f"{summary['shadow_hit_rate_on_covered']:.0%}")
        print(f"Baseline hit rate ('always predict 0'), SAME covered pairs: "
              f"{summary['baseline_hit_rate_on_covered']:.0%}")
        delta = summary["shadow_beats_baseline_on_covered"]
        verdict = ("BEATS baseline" if delta > 0 else
                   "TIES baseline" if delta == 0 else "LOSES to baseline")
        print(f"Shadow vs baseline: {verdict} ({delta:+d} pair(s))")
    print(f"Baseline hit rate over ALL {summary['total_pairs']} pairs "
          f"(no coverage gap): {summary['baseline_hit_rate_overall']:.0%}")
    print()
    print("UNDERPOWERED WARNING: this corpus has "
          f"{summary['total_pairs']} canonical pairs across "
          f"{len(summary['by_project'])} projects. A handful of pairs flipping "
          "changes the headline percentage by several points - read this as a "
          "direction, not a precise number, until the corpus is meaningfully bigger.")
    print()
    print("By project:")
    for project, stats in sorted(summary["by_project"].items()):
        hit_rate = (f"{stats['shadow_hits']}/{stats['covered']}"
                    if stats["covered"] else "no coverage")
        print(f"  {project}: {stats['total']} pairs, {stats['covered']} covered, "
              f"shadow correct {hit_rate}")

    if args.out:
        payload = {
            "summary": summary,
            "rows": [vars(r) for r in rows],
        }
        args.out.write_text(json.dumps(payload, indent=1, allow_nan=False),
                            encoding="utf-8")
        print(f"\nwrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
