# C4 plan: wire `intro_skip_bars` / `loop_source_sec` into align_engine's production path

**Status: DRAFT, not built.** Per C4's own 2026-09-15 investigation note, this is "genuine
align_engine design work at the same risk tier as C6... needing the same baseline-verification +
Codex-review rigor" — `plan_fill_or_cut` runs on EVERY transition of EVERY mix, so a mistake
here has a much larger blast radius than a narrowly-scoped bug fix. That investigation also
confirmed **zero real usage**: no `track_hints.json` file in this project's history has ever set
either hint to a non-zero value, so there is no live urgency forcing a rushed implementation.
Given that, and given the amount of already-shipped, tested, reviewed work landing in this same
session (D4, D9, E8), this is scoped as a reviewed plan rather than code this round — the
responsible way to "go ahead" on a C6-tier item is to design it properly first, the same
judgment call this item's own prior investigation already made once.

## What exists today (confirmed by direct code reading, not assumed)

- `intro_skip_bars` / `loop_source_sec` are read from `track_hints.json` into `TrackInfo` at
  `propose_arrangement.py:1464-1465`.
- They are ONLY consumed by the **legacy** path: `t.intro_skip_bars` pre-trims `t.sections`
  directly (`propose_arrangement.py:1477-1489`, gated `not USE_ALIGN_ENGINE`);
  `loop_source_sec` feeds `_plan_loop_extensions` (`propose_arrangement.py:785-787`), which only
  runs on the legacy natural-fill path.
- **align_engine's production path never sees either.** `plan_fill_or_cut`
  (`align_engine.py:2170`) has its own entirely separate, automatic loop/cut generation:
  `incoming_intro` (loop the incoming's intro drums back to the outgoing's last drop),
  `intro_cut` (drop the incoming's intro outright, only when the swap lands in a break/fill),
  `outgoing_tail` (loop the outgoing's outro forward). None of these currently accept a
  hand-authored override.
- A **precedent for bridging a legacy hint into align_engine already exists**: the
  `first_drop_sec`/`first_break_sec`/`outro_start_sec`/`last_bass_drop_sec` hints are converted
  bar-wise via `_sec_to_bar` inside `compute_aligned_positions`'s bridging block
  (`align_engine.py`, right after `_resolve_stem_key`) and gated into `_mix_cues` by
  `CueConfig.emit_hint_fields`. C4's wiring should follow this SAME shape, not invent a new one.

## Design

### `intro_skip_bars` -> a HINT-DRIVEN intro_cut

Currently `intro_cut` only fires automatically ("only if NO intro loop AND the intro starts in
a low-energy break" — `plan_fill_or_cut`'s own docstring, item 2). A hint-driven skip is a
DIFFERENT thing: an explicit, hand-authored instruction to always cut N bars off the incoming's
front, independent of whether the automatic break-detection would have found it. Proposed:

1. Bridge `t.intro_skip_bars` onto `align_engine.Track` as `hint_intro_skip_bars: int = 0`
   (same bridging site as the existing four `_sec` hints), gated behind
   `CueConfig.emit_hint_fields` — reuse the existing flag rather than adding a new one, since
   it's already the established "hand-authored hint" gate and these are the same trust tier.
2. In `plan_fill_or_cut`, BEFORE the automatic `incoming_intro`/`intro_cut` block: if
   `i.hint_intro_skip_bars > 0` and `emit_hint_fields` is on, emit an `intro_cut` spec directly
   from the hint (`cut_to_bar=hint_intro_skip_bars`) and set the SAME `intro_loop`-style
   mutual-exclusion flag the automatic paths already use, so the automatic `incoming_intro`/
   `intro_cut` logic skips itself for this pair exactly the way (1)/(1a)/(2) already skip each
   other today (see the existing ordering comment at `align_engine.py:2209-2234` — a hint
   override must slot into the SAME mutual-exclusion chain, not bypass it and risk double-
   cutting).
3. A hint-driven cut still needs the SAME downstream validity checks the automatic path gets
   (does the cut leave the incoming with anything to play, does it respect `loop_budget`/
   `overlap_ceiling`) — reuse the existing cut-construction helper rather than hand-rolling a
   second code path with its own bugs.

### `loop_source_sec` -> an override for automatic loop-source selection

Currently `pick_clean_drum_loop(i, 0, intro_end, insert_bar=0)` (for `incoming_intro`) and the
equivalent outgoing-tail source search both pick their own loop source window automatically. A
hint here means "trust my ear over the automatic picker for THIS specific loop." Proposed:

1. Bridge `t.loop_source_sec` onto `Track` as `hint_loop_source_bar: float | None` (via
   `_sec_to_bar`, same pattern), gated the same way.
2. Where `pick_clean_drum_loop` is called for a track that has `hint_loop_source_bar` set, use
   the hint's bar as the loop window start directly instead of calling the automatic picker —
   but STILL run the picker's own quality checks (silence_fraction, insert_level_match,
   worst_beat_dip, self_similarity) against the hint's window rather than skipping them, so a
   hand-authored hint gets the same "never fabricate/never silently accept a bad loop" guarantee
   automatic picks already have. A hint that fails those checks should be REPORTED (loudly, in
   the arrangement report) rather than silently used anyway — Sam's ear picked the SOURCE
   position, not a waiver on whether it actually loops cleanly.

### What this does NOT change

- The legacy path's existing consumption of these two hints — untouched, still gated
  `not USE_ALIGN_ENGINE`.
- Every other CueConfig flag, every other `plan_fill_or_cut` branch's ordering/mutual-exclusion
  logic.
- Behaviour for the 0 real projects that don't set either hint — by construction (both new
  code paths are gated on the hint actually being present), this is a no-op for every historical
  corpus, which is also the easiest regression proof: the 380-pair baseline sweep should show
  **zero changed pairs**, since none of that corpus's tracks carry either hint.

## Validation plan

1. **Baseline sweep, same discipline as D9**: run the 380-pair 14.08.26 corpus (or whichever is
   current) through `plan_fill_or_cut` before and after, confirm 0 changed pairs — proves the
   gating is genuinely inert until a hint is set, not just believed to be.
2. **A real synthetic hint fixture**: since no real project has ever used these hints, build a
   deliberate test fixture (a track_hints.json with a non-zero `intro_skip_bars`/
   `loop_source_sec` against a real corpus track) and confirm the NEW code path actually fires
   and produces the requested cut/loop, not just that it doesn't crash.
3. **Mutual-exclusion proof**: a hint-driven intro_cut must be provably exclusive with the
   automatic `incoming_intro`/`intro_cut` paths for the same pair (test that only ONE spec of
   that family is ever emitted, matching the existing `intro_loop` flag's own proven behaviour).
4. **Full suite green**, plus the existing `test_alignment_baseline.py`/
   `test_arrangement_decisions.py` suites specifically (closest neighbours to this change).

## REVIEW OUTCOME, 2026-09-22: park it — do not build this round

MiniMax's design review independently reached the same conclusion this plan's own "open
question" section leaned toward, and gave a clear verdict rather than leaving it open: **DROP —
park it.** Its reasoning: zero real usage, zero live defect, optional polish on a path that runs
on every transition of every mix; the existing runtime WARNING already surfaces the one
silent-failure class worth caring about ("a hint is set and does nothing"); building now adds
new code paths into `plan_fill_or_cut` for zero current value. Two independent judgments (this
plan's own author and an independent reviewer) landing on the same call is a real signal, not
just caution for its own sake — recorded as the decision, not left as a still-open question.

The review also caught real design gaps worth fixing WHEN this is eventually built (not now):
- The plan claimed "reuse the existing cut-construction helper" for the hint-driven `intro_cut`
  — **no such helper exists**; block (2) of `plan_fill_or_cut` (`align_engine.py:2299`) builds
  its `FillCutSpec` inline. Whoever builds this needs to extract a small
  `_build_intro_cut_spec(...)` helper shared by both paths, or duplicate the sanity check.
- The mutual-exclusion mechanism (reusing the `intro_loop` flag) is verified CORRECT by reading
  the real gating (`align_engine.py:2207,2271,2296,2299`) but the flag's name is misleading for
  a cut (not a loop) — rename to something like `intro_filled` in the same commit, or document
  explicitly why the loop-named flag is still the right gate for a cut.
- "An override for automatic loop-source selection" needs to name WHICH `pick_clean_drum_loop`
  call sites it affects — `plan_fill_or_cut` calls it from three places (incoming-intro intro,
  incoming-intro's outro fallback, outgoing-tail outro); decide and state which the hint reaches.
- "Still run the picker's own quality checks" needs to explicitly include `_blocked`
  (`align_engine.py:2014`, the vocal/fill exclusion) — a hand-authored loop source landing in a
  vocal region should be rejected loudly, not silently pass on benign metrics alone.

## Open question for the reviewers (and ultimately Sam) — RESOLVED above, kept for the trail

Given zero real usage today, is this worth building AT ALL before a real project actually needs
it (per the item's own 2026-09-15 conclusion), or does having the hint fields silently ignored
on the production path (with only a printed WARNING, `propose_arrangement.py:1516`) remain an
acceptable "documented gap, not yet a real cost" state? This plan's position: the warning
already prevents the SILENT-failure class the item worried about ("Sam re-fixing the same thing
twice" without knowing why) — the remaining risk is purely "a future user sets the hint and is
surprised it does nothing to the align_engine path," which the warning already surfaces loudly
at run time. Building this now is optional polish, not a live defect; flagging that explicitly
rather than treating "Sam said go ahead" as license to skip re-confirming the priority call.
