# D13 review - Claude subagent standing in for Codex (capped until 2026-09-19)

# D13 Review — Claude-arranged mode (Automated DJ Mixes)

## Q1: Code correctness (align_engine.py, propose_arrangement.py, apply_loops.py)

The core arithmetic in `alignment_from_decision` (align_engine.py:2474-2529) and `fills_from_decision` (:2532-2569) is internally consistent — `arr_offset = entry_out - trim`, `handoff_out = arr_offset + swap_in`, `overlap = o.n_bars - arr_offset` — and matches every value pinned in Tests/test_arrangement_decisions.py. I ran the suite read-only: **13/13 pass**, including the boundary case (swap on the outgoing's last bar) and the three "impossible decision" rejections.

Two real validation gaps, though, matching what the brief asked me to hunt for — "a malformed decision would silently produce a wrong arrangement instead of raising":

1. **`swap_in_bar` is never checked against the incoming track's own length.** `alignment_from_decision` validates `handoff_out <= o.n_bars` (outgoing bound) but never touches `i.n_bars` — the parameter `i` is used only for name-matching. A decision with `swap_in_bar` beyond the incoming's actual bar count would pass validation and place the bass swap on non-existent incoming content. No test covers this.
2. **`tail_loop`'s `source_start_bar`/`source_end_bar` are never bounds-checked** against `o.n_bars` in `fills_from_decision` (align_engine.py:2552-2563) — only `s1 > s0` and `reps`/`partial` sanity are checked. A loop source outside the outgoing's real content would silently reference wrong/garbage audio.

A softer, related concern: `outgoing_cut`/`outro_skip` (propose_arrangement.py:969-1021) are applied independently of the swap geometry that `alignment_from_decision` already locked in. Nothing enforces that the cut/skip target lies at or after the computed swap bar — in the real decisions file it always does (verified for T9/T10), but that's authoring discipline, not an enforced invariant.

## Q2: Write-up vs. raw dry-run data

Two concrete errors, both checkable against `phase3c_..._dryrun.txt`:

- **T5/T6/T9 sneak values are stated backwards.** The raw file's `X->Y` format is (Claude-decided → Sam-corrected) — confirmed unambiguously by T7's own `entry_moved_out:+1bars`/`swap_moved_in:-1bars` matching `106->107`/`9->8`, and by T7's sneak row being correctly stated. But T5 (`0.1->0.1194`), T6 (`0.2->0.15`), T9 (`0.2->0.15`) are each reported in the .md with decided/Sam swapped (e.g. T5 says "0.12 decided vs 0.10 Sam"; raw says decided=0.10, Sam=0.12).
- **T1's "(same total bars)" claim is false.** Raw: `tail_loop_changed:2bx7+0b->1bx16+0b` = 14 bars decided vs 16 bars Sam (confirmed by the geometry line "tail after swap 32->34", a 2-bar delta) — not equal.
- **"5 of 11 exact-to-the-beat" is unsupported.** The raw corrections list gives exactly 3 transitions with zero corrections (T2/T3/T10); the table's "exact" label (any wording) covers 9. No reading of the raw data produces 5.

Everything else checked (10/11 within 1 bar, T4 swap-removed, T7 entry+1/swap-1, T8's tail-loop numbers, correction-type tally) matches the raw file exactly.

## Q3: Doc vs. code (mix_md_new_section.md)

Required/optional field list matches what the code reads. One real inaccuracy: the doc calls `tail_loop` a mechanism "for a track with no outro," but T1 and T3 in the actual decisions file use `tail_loop` on tracks that do have an outro (`out_cue: "outro_start bar 196/168"`), and `_plan_marker_loops`'s `outgoing_tail` branch explicitly handles both cases. Minor: the doc says `swap_cue`/`out_cue` both feed `handoff_kind`, but only `swap_cue` does (`out_cue` feeds the `paired_cues` label instead).

VERDICT: CORRECTION — "15.09.26 August Releases Mix Claude Arranged vs Sam Tweaks.md" reverses the decided/Sam sneak values for T5, T6, T9 (e.g. T5 states "0.12 decided vs 0.10 Sam" where the raw dry-run shows `sneak_changed:0.1->0.1194`, i.e. 0.10 decided vs 0.12 Sam) and its T1 "(same total bars)" claim is wrong (14 vs 16 bars); mix_md_new_section.md's claim that `tail_loop` is "for a track with no outro" is contradicted by T1/T3 in the decisions file itself.

---
**Disposition (2026-09-16, Claude):** all four findings confirmed against ground truth and actioned.
Write-up corrected (sneak direction, T1 bars, "5 exact" claim removed - 3 confirmed). Code fixed:
align_engine.py now bounds swap_in_bar against i.n_bars and tail_loop source bars against o.n_bars,
two new tests proved to fail pre-fix, full suite 845/0/6, real decisions file re-verified clean
through Phase 2 + Phase 3 + both validation gates. mix.md corrected (tail_loop no-outro claim,
swap_cue/out_cue split). The outgoing_cut/outro_skip-vs-swap-bar softer concern left as an
authoring-discipline note, not enforced - no real trigger found in the shipped decisions file.
