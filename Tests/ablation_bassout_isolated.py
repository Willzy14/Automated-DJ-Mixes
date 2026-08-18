"""Isolated ablation: emit_bass_out cue, measured alone against the 14.08.26 corpus.

The 2026-08-17 commit (bacf1e7, "feat(align): wire the analysis layer into the swap
decision") added `emit_bass_out` (a CueConfig flag) and the `--cue-signals bassout`
CLI token. Combined runs with 6 flags on at once were measured; per-flag attribution
to bassout was NOT. This script isolates bassout on its own and reports the diff
against the same baseline the regression test pins.

Run with:  PYTHONPATH=Source python Tests/ablation_bassout_isolated.py

Standalone on purpose: never collected by pytest, never touches the module global
permanently. AE.CUE_CONFIG is restored to the no-flag default in a finally block
even on uncaught exceptions inside the script.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Source"
sys.path.insert(0, str(SRC))

STEM_DIR = ROOT / "Test Project" / "14.08.26" / "_Stem Analysis"
BASELINE = (ROOT / "Documentation" / "Plans" / "arranger-signal-rewiring"
            / "baseline_alignments.json")
REPORT_PATH = (ROOT / "Documentation" / "Plans" / "arranger-signal-rewiring"
               / "bassout_ablation_report.md")

#: Pinned fields the diff operates on (per brief).
PINNED = ("handoff_bar_out", "arr_offset_bars", "overlap_bars", "swap_progress",
          "handoff_kind", "alignment_policy", "n_paired_cues", "status")
#: Proximity threshold for the headline metric.
PROXIMITY_BARS = 2.0


def _load_tracks():
    import align_engine as AE
    tracks = {}
    for f in sorted(STEM_DIR.glob("SECTIONS_STEM_*.json")):
        key = f.name.replace("SECTIONS_STEM_", "").replace(".json", "")
        tracks[key] = AE.load_track(f)
    return tracks


def compute_rows(tracks):
    """380 ordered pairs (out != in). Status 'raise' rows carry error/msg."""
    import align_engine as AE
    rows = []
    for out_name in sorted(tracks):
        for in_name in sorted(tracks):
            if out_name == in_name:
                continue
            rec = {"out": out_name, "in": in_name}
            try:
                al = AE.align_pair(tracks[out_name], tracks[in_name])
            except Exception as exc:
                rec.update({"status": "raise",
                            "error": type(exc).__name__,
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
                })
            rows.append(rec)
    return rows


def row_diffs(before, after):
    """Return list of (field, before_val, after_val) for fields whose values differ
    on the pinned set. 'status' is always compared; other pinned fields are skipped
    for raise rows in either side (no handoff_bar_out etc. on raise)."""
    diffs = []
    for f in PINNED:
        b = before.get(f)
        a = after.get(f)
        if b != a:
            diffs.append((f, b, a))
    return diffs


def main():
    import align_engine as AE

    baseline_payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline_rows = {tuple((r["out"], r["in"])): r for r in baseline_payload["rows"]}

    tracks = _load_tracks()
    n_tracks = len(tracks)
    print(f"corpus: {n_tracks} tracks, {n_tracks * (n_tracks - 1)} ordered pairs")

    # --- BEFORE: default config (emit_bass_out=False) ------------------------------
    assert AE.CUE_CONFIG == AE.CueConfig(), \
        f"precondition: AE.CUE_CONFIG must be the default, got {AE.CUE_CONFIG!r}"
    t0 = time.perf_counter()
    before_rows_list = compute_rows(tracks)
    before_dt = time.perf_counter() - t0
    before = {(r["out"], r["in"]): r for r in before_rows_list}
    assert len(before) == n_tracks * (n_tracks - 1), \
        f"BEFORE row count {len(before)} != {n_tracks*(n_tracks-1)}"

    # Sanity: BEFORE matches the frozen baseline JSON exactly on the pinned set.
    assert set(before) == set(baseline_rows), "corpus drift vs the frozen baseline"
    sanity_mismatches = []
    for key, brow in baseline_rows.items():
        arow = before[key]
        for f in PINNED:
            if brow.get(f) != arow.get(f):
                sanity_mismatches.append((key, f, brow.get(f), arow.get(f)))
    sanity_ok = not sanity_mismatches
    if sanity_ok:
        print(f"sanity: BEFORE matches baseline_alignments.json on pinned fields "
              f"({len(before)} pairs)")
    else:
        print(f"sanity: BEFORE DIFFERS from baseline on {len(sanity_mismatches)} "
              f"field(s) over {(len({k for k,_,_,_ in sanity_mismatches}))} pairs")

    # --- AFTER: emit_bass_out=True (all other flags stay False) --------------------
    try:
        AE.CUE_CONFIG = AE.CueConfig(emit_bass_out=True)
        assert AE.CUE_CONFIG == AE.CueConfig(emit_bass_out=True)
        t0 = time.perf_counter()
        after_rows_list = compute_rows(tracks)
        after_dt = time.perf_counter() - t0
        after = {(r["out"], r["in"]): r for r in after_rows_list}
        assert len(after) == n_tracks * (n_tracks - 1)
    finally:
        # Restore: never leak state, never silently leave a non-default CUE_CONFIG.
        AE.CUE_CONFIG = AE.CueConfig()
        assert AE.CUE_CONFIG == AE.CueConfig(), \
            f"failed to restore CUE_CONFIG; now {AE.CUE_CONFIG!r}"

    # --- Diff per pair on the pinned set ------------------------------------------
    n_pairs = n_tracks * (n_tracks - 1)
    changed_pairs: list[tuple[tuple[str, str], list]] = []
    newly_ok: list[tuple[str, str]] = []
    newly_raise: list[tuple[str, str]] = []
    repositioned: list[tuple[str, str], list] = []  # both ok, other pinned diff
    bass_out_won: list[tuple[str, str]] = []  # AFTER kind contains 'bass_out', BEFORE did not

    for key, brow in before.items():
        arow = after[key]
        b_status = brow["status"]; a_status = arow["status"]
        diffs = row_diffs(brow, arow)
        if not diffs:
            continue
        changed_pairs.append((key, diffs))
        if b_status == "raise" and a_status == "ok":
            newly_ok.append(key)
        elif b_status == "ok" and a_status == "raise":
            newly_raise.append(key)
        else:
            # both same status (ok or both raise) but a pinned field differs
            repositioned.append((key, diffs))
        b_kind = brow.get("handoff_kind") or ""
        a_kind = arow.get("handoff_kind") or ""
        if "bass_out" not in b_kind and "bass_out" in a_kind:
            bass_out_won.append(key)

    # --- Headline: proximity to the outgoing's REAL bass_out_bar -------------------
    prox_denominator = []   # changed & ok-after & outgoing has bass_out & not is_end
    prox_excluded_no_bass = []   # outgoing has no bass_out_bar
    prox_excluded_raises = []    # AFTER status is raise
    prox_excluded_is_end = []    # bass_out_is_end
    prox_within = 0
    prox_dists = []
    for (out_name, in_name), diffs in changed_pairs:
        out_track = tracks[out_name]
        arow = after[(out_name, in_name)]
        if arow["status"] != "ok":
            prox_excluded_raises.append(((out_name, in_name), diffs))
            continue
        if out_track.bass_out_bar is None:
            prox_excluded_no_bass.append(((out_name, in_name), diffs))
            continue
        if out_track.bass_out_is_end:
            prox_excluded_is_end.append(((out_name, in_name), diffs,
                                         out_track.bass_out_bar,
                                         out_track.n_bars))
            continue
        delta = abs(float(arow["handoff_bar_out"]) - float(out_track.bass_out_bar))
        prox_denominator.append((out_name, in_name, delta))
        if delta <= PROXIMITY_BARS:
            prox_within += 1
        prox_dists.append(delta)

    denom = len(prox_denominator)
    pct = (100.0 * prox_within / denom) if denom else 0.0

    # --- Report --------------------------------------------------------------------
    lines = []
    lines.append(f"# Bass-out isolated ablation ({time.strftime('%Y-%m-%d')})\n")
    lines.append("Invocation: `PYTHONPATH=Source python Tests/ablation_bassout_isolated.py`\n")
    lines.append(f"Corpus: `Test Project/14.08.26/_Stem Analysis` ({n_tracks} tracks, "
                 f"{n_pairs} ordered pairs).\n")
    lines.append(f"Flag measured: `CueConfig.emit_bass_out = True`. All other flags stay "
                 f"False (isolates bassout, unlike the earlier combined run).\n")
    lines.append(f"Sanity: BEFORE matches `baseline_alignments.json` on pinned fields? "
                 f"**{'YES' if sanity_ok else 'NO'}** "
                 f"({len(sanity_mismatches)} mismatches).\n")
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- Pairs changed (any pinned field): **{len(changed_pairs)} / {n_pairs}**")
    lines.append(f"  - flipped raise -> ok: **{len(newly_ok)}**")
    lines.append(f"  - flipped ok -> raise: **{len(newly_raise)}**")
    if newly_raise:
        lines.append("    **WARNING: behaviour cost.** Flag-on regressed aligned pairs.")
    lines.append(f"  - both same status, other pinned field(s) differ (repositioned): "
                 f"**{len(repositioned)}**")
    lines.append(f"- Pairs whose AFTER `handoff_kind` contains `'bass_out'` and BEFORE "
                 f"didn't: **{len(bass_out_won)}**")
    lines.append(f"- Key acceptance (changed AND ok-after AND outgoing bass_out_bar != "
                 f"None AND not bass_out_is_end): **{prox_within} of {denom} = "
                 f"{pct:.2f}%** within {PROXIMITY_BARS} bars of the outgoing's real "
                 f"bass_out_bar.")
    lines.append(f"  - Excluded from the proximity denominator:")
    lines.append(f"    - AFTER status = raise: **{len(prox_excluded_raises)}**")
    lines.append(f"    - outgoing bass_out_bar is None: **{len(prox_excluded_no_bass)}**")
    lines.append(f"    - outgoing bass_out_is_end (could not fire as Tier-1 cue): "
                 f"**{len(prox_excluded_is_end)}**")
    lines.append("")
    lines.append("## One-line interpretation")
    if pct >= 50.0:
        lines.append(f"{prox_within}/{denom} = {pct:.2f}% clears the 50% bar -- the "
                     f"bass_out cue wins the right anchor on a majority of the "
                     f"changed+ok pairs that could plausibly have moved.")
    else:
        lines.append(f"{prox_within}/{denom} = {pct:.2f}% does NOT clear 50% -- the "
                     f"bass_out cue does not move most eligible pairs to the real "
                     f"bass_out bar. Wiring it is not a clean win.")
    lines.append("")
    lines.append("## Proximity distances (denominator set)")
    if prox_dists:
        lines.append("")
        lines.append("| out | in | abs(handoff - bass_out) (bars) | within 2.0 |")
        lines.append("| --- | --- | --- | --- |")
        for (out_name, in_name, delta) in prox_denominator:
            mark = "Y" if delta <= PROXIMITY_BARS else "N"
            lines.append(f"| {out_name} | {in_name} | {delta:.3f} | {mark} |")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    # Console summary too.
    print(f"BEFORE run: {before_dt:.2f}s")
    print(f"AFTER run:  {after_dt:.2f}s")
    print(f"changed: {len(changed_pairs)} / {n_pairs}")
    print(f"  raise->ok: {len(newly_ok)}   ok->raise: {len(newly_raise)}   "
          f"repositioned: {len(repositioned)}")
    print(f"handoff_kind gained 'bass_out': {len(bass_out_won)}")
    print(f"headline: {prox_within} of {denom} = {pct:.2f}% within {PROXIMITY_BARS} bars")
    print(f"  excluded (raise in AFTER): {len(prox_excluded_raises)}")
    print(f"  excluded (outgoing bass_out None): {len(prox_excluded_no_bass)}")
    print(f"  excluded (outgoing bass_out_is_end): {len(prox_excluded_is_end)}")
    if not sanity_ok:
        print("SANITY MISMATCHES:")
        for key, f, b, a in sanity_mismatches[:20]:
            print(f"  {key} {f}: {b!r} -> {a!r}")
    print(f"report: {REPORT_PATH}")


if __name__ == "__main__":
    main()