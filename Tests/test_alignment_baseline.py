"""Full-corpus alignment baseline — the safety net for the signal-rewiring work.

The existing golden test (`test_align_engine_golden.py`) pins ONE mix's swap beats,
break-skip and loop counts. It does NOT pin `paired_cues`, `arr_offset_bars`,
`overlap_bars`, `swap_progress`, `handoff_kind`, `alignment_policy` or
`overlap_policy` — so a change to which CUES the engine can see would sail straight
through it. That is precisely the class of change the rewiring makes, which is why
this file exists.

It regenerates every ordered pair over the 14.08.26 corpus (380 pairs, 20 tracks) and
diffs against `Documentation/Plans/arranger-signal-rewiring/baseline_alignments.json`,
captured 2026-08-17 from unmodified code BEFORE any signal was wired.

This test is EXPECTED to fail once a rewiring step lands. That is its job: the failure
report names every pair whose decision moved and how, so the change can be inspected
and attributed to one signal. When a diff is reviewed and accepted, refresh the
baseline deliberately with:

    PYTHONPATH=Source python Tests/test_alignment_baseline.py --refresh

Never refresh to make a red test green without reading the diff.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

STEM_DIR = ROOT / "Test Project" / "14.08.26" / "_Stem Analysis"
BASELINE = (ROOT / "Documentation" / "Plans" / "arranger-signal-rewiring"
            / "baseline_alignments.json")

#: Fields whose change constitutes a behavioural difference worth failing on.
PINNED = ("handoff_bar_out", "arr_offset_bars", "overlap_bars", "swap_progress",
          "handoff_kind", "alignment_policy", "n_paired_cues", "paired_cue_bars")

pytestmark = pytest.mark.skipif(
    not list(STEM_DIR.glob("SECTIONS_STEM_*.json")) or not BASELINE.exists(),
    reason="14.08.26 stem JSONs or the captured baseline are unavailable",
)


def _load_tracks():
    import align_engine as AE
    tracks = {}
    for f in sorted(STEM_DIR.glob("SECTIONS_STEM_*.json")):
        key = f.name.replace("SECTIONS_STEM_", "").replace(".json", "")
        tracks[key] = AE.load_track(f)
    return tracks


def compute_rows():
    """Every ordered pair's decision, in the baseline's exact shape."""
    import align_engine as AE
    tracks = _load_tracks()
    rows = []
    for out_name in sorted(tracks):
        for in_name in sorted(tracks):
            if out_name == in_name:
                continue
            rec = {"out": out_name, "in": in_name}
            try:
                al = AE.align_pair(tracks[out_name], tracks[in_name])
            except Exception as exc:          # a raise IS a pinned outcome
                rec.update({"status": "raise", "error": type(exc).__name__,
                            "msg": str(exc)[:160]})
            else:
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
                    "paired_cue_bars": [p["arrangement_bar"]
                                        for p in (al.paired_cues or [])],
                })
            rows.append(rec)
    return rows


def _key(row):
    return (row["out"], row["in"])


def test_alignment_decisions_match_baseline():
    expected = {_key(r): r for r in json.loads(BASELINE.read_text(encoding="utf-8"))["rows"]}
    actual = {_key(r): r for r in compute_rows()}

    assert set(actual) == set(expected), (
        "the corpus itself changed — pairs added/removed since the baseline was captured"
    )

    moved, newly_ok, newly_raise = [], [], []
    for key, exp in expected.items():
        act = actual[key]
        if exp["status"] != act["status"]:
            (newly_ok if act["status"] == "ok" else newly_raise).append(key)
            continue
        if act["status"] != "ok":
            continue
        diffs = [f"{f}: {exp[f]!r} -> {act[f]!r}"
                 for f in PINNED if exp.get(f) != act.get(f)]
        if diffs:
            moved.append((key, diffs))

    if not (moved or newly_ok or newly_raise):
        return

    report = [
        f"alignment decisions moved against the frozen baseline "
        f"({len(moved)} changed, {len(newly_ok)} newly align, "
        f"{len(newly_raise)} newly raise, of {len(expected)} pairs)",
        "",
        "This is not automatically a bug — a rewiring step is SUPPOSED to move decisions.",
        "Read the diff, attribute it to the signal you just wired, then refresh the",
        "baseline deliberately:  PYTHONPATH=Source python Tests/test_alignment_baseline.py --refresh",
        "",
    ]
    for key in newly_ok[:15]:
        report.append(f"  NOW ALIGNS  {key[0][:34]} -> {key[1][:34]}")
    for key in newly_raise[:15]:
        report.append(f"  NOW RAISES  {key[0][:34]} -> {key[1][:34]}")
    for key, diffs in moved[:25]:
        report.append(f"  CHANGED     {key[0][:34]} -> {key[1][:34]}")
        report.extend(f"                {d}" for d in diffs)
    if len(moved) > 25:
        report.append(f"  ... and {len(moved) - 25} more changed pairs")

    pytest.fail("\n".join(report))


def test_baseline_is_self_consistent():
    """The captured baseline must describe the corpus it claims to."""
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    rows = payload["rows"]
    n = payload["n_tracks"]
    assert len(rows) == n * (n - 1), "row count does not match an all-ordered-pairs sweep"
    assert payload["ok"] + payload["raised"] == len(rows)
    assert payload["ok"] > 0 and payload["raised"] > 0, (
        "a baseline with no successes or no failures cannot discriminate a regression"
    )


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        rows = compute_rows()
        ok = sum(1 for r in rows if r["status"] == "ok")
        payload = {"corpus": "Test Project/14.08.26",
                   "n_tracks": len(_load_tracks()), "n_pairs": len(rows),
                   "ok": ok, "raised": len(rows) - ok, "rows": rows}
        BASELINE.write_text(json.dumps(payload, sort_keys=True, indent=1),
                            encoding="utf-8")
        print(f"baseline refreshed: {len(rows)} pairs, {ok} aligned, {len(rows)-ok} raised")
    else:
        print(__doc__)
