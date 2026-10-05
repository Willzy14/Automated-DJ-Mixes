# Build brief: R1 + R3 outgoing-loop simplification (2026-10-05)

You are building in the git worktree you were launched in (branch `build/r1-r3-loop-simplify`).
Write access: this worktree only. Commit locally when green. Do NOT push. Do NOT touch the main checkout.
ASCII-only output/prints (Windows cp1252). `set PYTHONPATH=Source`.

## Why
Sam hand-corrected our D15-era mix (evidence + rule derivation: `Documentation/Plans/v5-sam-tweaks-analysis.md`
- read sections 3 and 4 first). He deleted most outgoing-outro loops. Two rules reproduce that with the
alignment untouched:

- **R1 - stop when the outgoing already lands on a line.** If an incoming section start lies within +-2 bars
  of the outgoing's natural end E (`current_incoming_bar = o.n_bars - arr`, in incoming bars), plan NO outgoing
  tail loop. Today `_outro_section_target_candidates` (`Source/align_engine.py` ~line 2218) keeps only
  boundaries `>= E + 2`, so a track that already ends exactly on a section line gets looped on to the NEXT one.
- **R3 - loop only a short reach.** Loop the outgoing only if the chosen target is <= 12 bars past its natural
  end. Replace the `candidate_gap > loop_budget` test (~line 2448) with
  `candidate_gap > min(loop_budget, policy.max_outgoing_reach_bars)` when that field is not None.

## Where
`plan_fill_or_cut` branch (3), `Source/align_engine.py` ~lines 2395-2560. Policy dataclass:
`Source/automated_dj_mixes/transition_policy.py` (`TransitionPolicy`, line 36; `INTERIM_V1` is the production
default, `SAM_V1` experimental).

## Spec
1. Add two fields to `TransitionPolicy`: `skip_outgoing_loop_when_on_section_line: bool = False` and
   `max_outgoing_reach_bars: float | None = None` (defaults = old behaviour). Set them to `True` and `12.0` on
   `INTERIM_V1` only (it is the production default). Leave every other policy unchanged.
2. R1 in `plan_fill_or_cut`: when the flag is on, in landmark mode, and some incoming section start is within
   +-2 bars of `current_incoming_bar` AND the outgoing's natural end already reaches the locked swap
   (`al.handoff_bar_out <= o.n_bars` - i.e. no loop is needed to cover the swap), return with no outgoing-loop
   spec. Record why in a NEW field/note (e.g. `FillCutSpec`-adjacent audit note or the existing report's
   `loop_source` text such as `none: natural end on section:<name>`). Do NOT reuse `outgoing_loop_abandoned` -
   that means "wanted but failed" and D15/D2b transparency must stay unambiguous. Find how D15 surfaced
   abandonment in `propose_arrangement.py`/`ARRANGEMENT_REPORT.json` and mirror it with a distinct reason.
3. R3: apply the reach cap. CRITICAL SAFETY: the cap must never turn a pair that planned fine today into a new
   hard `ValueError("Cannot plan outgoing tail loop ...")`. A loop that is REQUIRED to reach the locked swap
   (`candidate_gap < locked_swap_gap`) must still be allowed past the cap. So the cap only removes loops that
   are optional (the outgoing already reaches the swap on its own). If in doubt, keep the old behaviour.
4. Keep tier order (sections before landmarks), the quality gate, and D15's fallbacks exactly as they are.
5. Replay: `Tools/d15_outro_loop_replay.py` (read it; it supports `--out` / `--diff`). Run it BEFORE your change
   (baseline), then AFTER, over the 380-pair corpus. Expected, from a read-only estimate: R1 removes about 58 of
   215 loops; the R3 cap removes more. Required: the 26 `align_raise` and 1 `raise` rows unchanged and NO new
   raise anywhere. Save both JSONs and the diff under `Receipts/2026-10-05/` in the worktree. Read every changed
   row category, report counts by reason, and list 3 changed pairs verbatim.
6. Re-score the Core Sample mix: data is at the MAIN checkout (gitignored, read-only for you):
   `C:\Users\Carillon\Wired Masters Dropbox\Sam Wills\0.1---GIT HUB---\Automated DJ Mixes\Test Project\22.09.26 Tech House Core Sample\_Stem Analysis`.
   Run `align_pair` + `plan_fill_or_cut` in mix order for the 10 transitions with the new policy and print a
   table: transition | loop Y/N + bars | overlap bars. Sam's truth (loop Y/N, overlap): T1 Y 46, T2 N 32, T3 N 32,
   T4 Y(own tail) 39, T5 N 37, T6 N 64, T7 N 32, T8 N 32, T9 N 33, T10 Y 32. V5 loops were T1 10 bars, T2 24,
   T3 5, T5 8, T7 15, T9 8, T10 8. Expected after R1+R3: T2, T7, T9 loops gone; T1 and T10 kept. Report
   honestly where it differs.
7. Tests: new `Tests/test_outro_loop_simplify.py` (R1 skips on-a-line; R1 does not skip when the swap needs the
   loop; R3 cap removes an optional 15-bar loop but keeps a 10-bar one; cap never raises new ValueError; flags
   off = old behaviour byte-identical). Then run the FULL suite (`python -m pytest Tests -q`); baseline was
   902 passed / 6 skipped / 0 failed. `Tests/test_alignment_baseline.py` must stay unchanged (alignment is
   untouched; if it diffs, STOP and explain). If a D15 test (`Tests/test_outro_loop_targeting.py`) legitimately
   encodes old behaviour, do not weaken it - make it pass `skip_outgoing_loop_when_on_section_line=False`
   explicitly via a policy override and say so.

## Out of scope (do not build)
R2 (re-anchor), R4 (no-outro tail), R5/R6 (incoming side, rounding). Do not edit docs/BURN_LIST.

## Report format (last message)
Files changed, test counts, replay counts by category, the Core Sample table, any deviation from this spec,
and anything you were unsure about. Be honest about what you did NOT verify.
