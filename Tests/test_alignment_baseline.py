"""Full-corpus alignment baseline — the safety net for the signal-rewiring work.

The existing golden test (`test_align_engine_golden.py`) pins ONE mix's swap beats,
break-skip and loop counts. It does NOT pin `paired_cues`, `arr_offset_bars`,
`overlap_bars`, `swap_progress`, `handoff_kind`, `alignment_policy` or
`overlap_policy` — so a change to which CUES the engine can see would sail straight
through it. That is precisely the class of change the rewiring makes, which is why
this file exists.

It regenerates every ordered pair over the 14.08.26 corpus (380 pairs, 20 tracks) and
diffs against `Documentation/Plans/arranger-signal-rewiring/baseline_alignments.json`,
captured 2026-08-17 from unmodified code BEFORE any signal was wired (schema extended
and re-captured 2026-08-20 with a field-projection proof that every pre-extension
field value survived the refresh unchanged).

This test is EXPECTED to fail once a rewiring step lands. That is its job: the failure
report names every pair whose decision moved and how, so the change can be inspected
and attributed to one signal. When a diff is reviewed and accepted, refresh the
baseline deliberately with:

    PYTHONPATH=Source python Tests/test_alignment_baseline.py --refresh

Never refresh to make a red test green without reading the diff.

Pinned surface (extended for Codex BLOCKER 2, 2026-08-20):
  * full pair provenance (al.paired_cues VERBATIM — arrangement_bar /
    outgoing_labels / incoming_source_bar / incoming_labels — plus al.notes
    carrying the weighted cue score). The legacy 8 only carried count+bars, so
    any change to which cues were seen (label list, arrangement-bar position,
    weight summary) passed silently.
  * align_pair raise-pair exception TYPE together with message prefix (a
    ValueError that becomes a KeyError, or a changed message, now fails).
  * plan_fill_or_cut specs (loops / cuts / break-skips) for every successful
    align — the layer that decides WHAT loops/cuts land around the swap, which
    had no coverage at all before this extension.
  * derived overlap_policy ("named_landmark_64" | "standard_48") per spec,
    pinned as a value (not as code-path branching).

RETIRED 2026-09-22 (burn list D9, Sam's call): the separate "rescue-flag plan
layer" this docstring used to describe (CUE_CONFIG.tail_anchor_rescue=True
re-run over every pair the default-flag align raised, guarding the
2026-08-20 tail-anchor-rescue flag-leak) is gone. `tail_anchor_rescue` (and
`deep_intro_anchor`/`incoming_phrase_anchors`) are now themselves the
default — see the CueConfig class docstring in align_engine.py — so "default"
and "rescue-flag-on" are the same CUE_CONFIG state and the layer had nothing
left to distinguish. The flag-leak class it protected (a rescued pair
misclassified as legacy policy in plan_fill_or_cut) is still independently
covered by Tests/test_codex_blocker_fixes.py's "Fix 3" section, which
constructs the Alignment directly and does not depend on the corpus sweep or
the ambient CUE_CONFIG default. A `baseline_alignments.json` produced before
this retirement still carries a `rescue_rows` key; `test_baseline_is_self_
consistent` tolerates its presence but no longer requires it.
"""
import dataclasses
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

STEM_DIR = ROOT / "Test Project" / "14.08.26" / "_Stem Analysis"
BASELINE = (ROOT / "Documentation" / "Plans" / "arranger-signal-rewiring"
            / "baseline_alignments.json")

#: Fields whose change constitutes a behavioural difference worth failing on for
#: an OK alignment. Includes the legacy 8 plus cue provenance (paired_cues /
#: anchor_bar_in / score / notes) plus the plan_fill_or_cut layer (plan /
#: overlap_policy).
PINNED = ("handoff_bar_out", "arr_offset_bars", "overlap_bars", "swap_progress",
          "handoff_kind", "alignment_policy", "n_paired_cues", "paired_cue_bars",
          "anchor_bar_in", "score", "paired_cues", "notes",
          "overlap_policy", "plan")
#: Fields whose change constitutes a behavioural difference worth failing on for
#: a RAISE alignment — exception class plus message prefix. A ValueError that
#: becomes a KeyError (or a changed message) now fails, instead of slipping
#: through because both rows have status "raise".
RAISE_PINNED = ("error", "msg")

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


def _key(row):
    return (row["out"], row["in"])


def _normalize(rows):
    """Round-trip through JSON to mirror what --refresh writes. Floats land in
    the same repr either side of the comparison."""
    return json.loads(json.dumps(rows))


def _round_floats(obj):
    """Round every float in (possibly nested) dict/list to 6 decimals. Ints and
    strings untouched. Defensive recursion so a future spec field with nested
    structure cannot silently regress the comparison."""
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v) for v in obj]
    return obj


def _spec_dict(spec):
    """dataclasses.asdict(spec) with every float rounded to 6 decimals. Spec
    fields are currently flat (no nested dicts/lists), but _round_floats recurses
    so a future field can't break determinism."""
    return _round_floats(dataclasses.asdict(spec))


def _derive_overlap_policy(specs):
    """Mirrors Source/propose_arrangement.py:838 (outgoing_tail with a NAMED
    target_marker_name and either reps>=1 or partial_bars>0) and :896
    (incoming_intro with reps>=1). Either condition promotes
    analysis.overlap_policy to "named_landmark_64" so the 64-bar override beats
    the 48-bar standard cap. Every other outcome — including an empty spec
    list — keeps "standard_48"."""
    for fc in specs:
        if (fc.kind == "outgoing_tail" and (fc.reps >= 1 or fc.partial_bars > 0)
                and fc.target_marker_name):
            return "named_landmark_64"
        if fc.kind == "incoming_intro" and fc.reps >= 1:
            return "named_landmark_64"
    return "standard_48"


def _plan_record(o, i, al):
    """Run plan_fill_or_cut on a successful align and pin the outcome alongside
    its derived overlap_policy. (plan, overlap_policy)."""
    import align_engine as AE
    try:
        specs = AE.plan_fill_or_cut(o, i, al)
    except Exception as exc:
        return ({"status": "raise", "error": type(exc).__name__,
                 "msg": str(exc)[:160]},
                None)
    return ({"status": "ok", "specs": [_spec_dict(s) for s in specs]},
            _derive_overlap_policy(specs))


def _populate_ok(rec, al, t_o, t_i):
    """Stamp every field carried by an OK-row onto rec in one place. Both
    compute_rows() and compute_rescue_rows() go through here so the row shape
    cannot drift between them."""
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
    plan, overlap_policy = _plan_record(t_o, t_i, al)
    rec["plan"] = plan
    rec["overlap_policy"] = overlap_policy


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
                _populate_ok(rec, al, tracks[out_name], tracks[in_name])
            rows.append(rec)
    return rows


def _diff_rows(expected_by_key, actual_by_key, pinned):
    """Walk expected keys, diff each against the matching actual key.

    Status flip -> newly_ok / newly_raise (these contain NO field diffs — the
    decision ITSELF moved). Status "ok" -> diff over `pinned` fields. Status
    "raise" -> diff over RAISE_PINNED fields, so a ValueError that becomes a
    KeyError or a changed message prefix now FAILS instead of passing.

    Returns (moved, newly_ok, newly_raise); `moved` entries are (key, [diffs]).
    """
    moved, newly_ok, newly_raise = [], [], []
    for key, exp in expected_by_key.items():
        act = actual_by_key.get(key)
        if act is None:
            continue                                       # corpus moved; outer set-equality catches it
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


def _failure_report(heading, expected_by_key, actual_by_key, pinned, refresh_cmd):
    """Build the standard moved-against-baseline report used by both tests."""
    moved, newly_ok, newly_raise = _diff_rows(expected_by_key, actual_by_key, pinned)

    if not (moved or newly_ok or newly_raise):
        return None

    report = [
        f"{heading} "
        f"({len(moved)} changed, {len(newly_ok)} newly align, "
        f"{len(newly_raise)} newly raise, of {len(expected_by_key)} pairs)",
        "",
        "This is not automatically a bug — a rewiring step is SUPPOSED to move decisions.",
        "Read the diff, attribute it to the signal you just wired, then refresh the",
        f"baseline deliberately:  PYTHONPATH=Source python {refresh_cmd}",
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

    return "\n".join(report)


def test_alignment_decisions_match_baseline():
    expected = {_key(r): r for r in json.loads(BASELINE.read_text(encoding="utf-8"))["rows"]}
    actual = {_key(r): r for r in _normalize(compute_rows())}

    assert set(actual) == set(expected), (
        "the corpus itself changed — pairs added/removed since the baseline was captured"
    )

    report = _failure_report(
        "alignment decisions moved against the frozen baseline",
        expected, actual, PINNED,
        "Tests/test_alignment_baseline.py --refresh",
    )
    if report is not None:
        pytest.fail(report)


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

    # The rescue-flag plan layer was retired 2026-09-22 (burn list D9) — see the
    # module docstring. A baseline captured before the retirement still carries
    # rescue_rows/rescue_ok/rescue_raised; tolerate but do not require them, and
    # do not validate their content (no code path re-derives it any more).

    # OK rows carry both plan and overlap_policy as first-class fields.
    for r in rows:
        if r["status"] == "ok":
            assert "plan" in r, f"OK row {r.get('out')!r} -> {r.get('in')!r} missing 'plan'"
            assert "overlap_policy" in r, (
                f"OK row {r.get('out')!r} -> {r.get('in')!r} missing 'overlap_policy'"
            )


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        rows = compute_rows()
        ok = sum(1 for r in rows if r["status"] == "ok")
        payload = {"corpus": "Test Project/14.08.26",
                   "n_tracks": len(_load_tracks()), "n_pairs": len(rows),
                   "ok": ok, "raised": len(rows) - ok,
                   "rows": rows}
        BASELINE.write_text(json.dumps(payload, sort_keys=True, indent=1),
                            encoding="utf-8")
        print(f"baseline refreshed: {len(rows)} pairs ({ok} align, "
              f"{len(rows) - ok} raise)")
    else:
        print(__doc__)
