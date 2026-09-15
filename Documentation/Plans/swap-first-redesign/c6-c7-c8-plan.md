# Swap-first redesign: landing order C6 / C7 / C8

Date: 2026-09-14, revised 2026-09-15 against Codex's round-1 review (8 findings:
3 FATAL + 4 MAJOR + 1 MINOR, all addressed below) plus a direct empirical
investigation of the T2 trigger case that materially changes C6's scope. This
is the remaining scope of burn list item "c" (the deeper swap-first
candidate/geometry reorder), after item (a) (built, merged `62c47f5`) and
C5/C2 (built 2026-09-14, see `Documentation/BURN_LIST.md` section C). Trigger
case throughout: Sam's real T2 hand-correction on the Tech House Heldout
project - moved the Freejak->HARTY bass swap 28 beats and widened the overlap
~8 bars, in one edit, because none of today's automated candidate selection
would have found that point on its own.

## Round-1 findings, addressed one by one

1. **FATAL - verification order.** Fixed in the sequencing section below:
   inspect and attribute every changed decision BEFORE refreshing any baseline,
   matching `test_alignment_baseline.py`'s own documented convention (this is
   also exactly the discipline used for the 2026-09-15 tiera rebuild's own
   15-pair baseline diff, so it now has a second real precedent in this repo).
2. **FATAL - "rank everything globally" had no anti-gaming rule.** Superseded
   below, not patched - the empirical T2 investigation found the rank-tuple
   ordering itself, not anchor iteration order, is the real mechanism at play
   for the motivating case. See "What actually blocks T2" below for the
   evidence and the resulting, narrower, evidence-grounded proposal.
3. **FATAL - `pair_history` is not 34 clean observations.** Addressed under C7
   below: canonicalisation (dedup, conflict resolution, a real schema) is now
   an explicit, first, separately-reviewed step before any scoring code is
   written, not assumed.
4. **MAJOR - C7 has no integration contract.** Addressed below: C7 starts in
   SHADOW MODE ONLY (report a preference + confidence, never alter selection),
   evaluated leave-one-PROJECT-out. No code path lets it touch admissibility.
5. **MAJOR - C7 must not implement part of C8 by stealth.** Restated as a hard
   constraint below: C7 stays a nudge among already-admissible candidates,
   full stop, until C8 is itself built and reviewed.
6. **MAJOR - C8's scope was materially wrong** (loops only extend, "feasible"
   can't be read off the numeric budget alone). C8 remains explicitly
   UNSCOPED below, for the reasons Codex gave, now restated - not attempted
   this round.
7. **MAJOR - C6's proposed verification didn't cover the actual trigger case.**
   This is the finding that reshaped the whole plan. See below - not covered
   by re-verifying against T2, but by DISCOVERING what T2 actually needs,
   which turned out not to be what C6 originally proposed at all.
8. **MINOR - `_search_matched_tail_head` is gated behind `CUE_CONFIG.
   matched_tail_head_swap`, default False.** Corrected throughout this
   document; the primary pass description below no longer implies it always
   runs.

## What actually blocks T2 - a real investigation, not a restated assumption

Codex's finding 7 asked for a T2-specific fixture proving the redesign fixes
the actual trigger case, rather than trusting the 380-pair corpus as a proxy.
Built that check FIRST, before writing any redesign code - and it overturned
the plan's own premise.

**Probe methodology:** loaded the real Freejak/HARTY tracks from
`Test Project/10.09.26 Tech House Heldout/_Stem Analysis/` directly (not a
synthetic fixture) and called `align_engine._search_anchors` directly with
both `rank_all=False` (today's primary-pass behaviour) and `rank_all=True`
(C6's originally-proposed pooled-and-ranked-globally behaviour), on the real
`_incoming_drop_anchors(HARTY)` = `[16, 48]`.

**Finding 1 - C6 as originally scoped is a no-op for T2.** Anchor 48 produces
ZERO admissible candidates (fails the overlap/progress checks outright).
Anchor 16 is therefore the ONLY anchor with anything to offer either way -
`rank_all=False` and `rank_all=True` return the IDENTICAL winner
(`arr_offset=148`, i.e. `outgoing_anchor=164`) for this pair. Stopping early
across INCOMING anchors was never the bottleneck here, because there was
nothing past the first anchor to reach.

**Finding 2 - the real bottleneck is ranking among OUTGOING anchor candidates
within that single incoming anchor**, and it is a real, evidenced ranking
defect. Enumerated every `outgoing_anchor` candidate at `incoming_anchor=16`
directly against `_search_anchors`' own rank-tuple formula
(`drop_payoff, weighted_score, entry_paired+exit_paired, -abs(progress-0.65),
overlap`):

| outgoing_anchor | overlap | progress | rank tuple |
|---|---|---|---|
| 148 (Freejak's OWN `bass_out_bar`, 148.00268 - the exact point this file's own top-of-module arrangement model calls the natural swap point when bass doesn't run to the end) | 33 bars | 0.485 (close to the 0.65 target) | `(0, 20, 0, -0.165, 33)` |
| 164 (today's actual pick - a `kick_dropout` landmark 16 bars after bass has already been gone) | 17 bars | 0.941 (at the edge of the 0.95 admissibility ceiling) | `(0, 21, 1, -0.291, 17)` |

`drop_payoff` ties at 0 for both (the primary anti-gaming term the module's
own docstring relies on - see below, this is NOT bypassed). Bar 164 wins the
tie purely on `weighted_score` (21 vs 20 - a ONE-POINT edge from one extra
coincidental cue-weight match) and `entry_paired+exit_paired` (1 vs 0). Every
other signal - overlap length, distance from the 0.65 progress target, and
Freejak's own bass-out point - favours bar 148 by a wide margin. **A 1-point
cue-coincidence edge is currently enough to override a vastly better
structural candidate**, because the rank tuple has no term at all for "is this
near the track's own documented natural bass-out/breakpoint."

**Finding 3 - even bar 148 is not Sam's actual correction.** Sam's real point
(`sam_bass_swap_beat=628` beats = bar 157, from
`Documentation/Mix Patterns Library/pair_history.jsonl` pair_index 2) sits
between bar 148 and bar 164 and is NOT a registered `_mix_cues(Freejak)` entry
at all (confirmed: dumped every Freejak cue in the full mixable range - none
at bar 157). No amount of re-ranking, nudging, or admitting EXISTING
candidates can reach a bar that was never generated as a candidate. Bar 157
is out of scope for C6/C7/C8 as any of them are currently conceivable - it
would need a new CUE-GENERATION source, not a selection/ranking change, and
that is not scoped anywhere in this document. Stated plainly so it is not
silently expected: **this redesign, even fully built, will not reproduce
Sam's exact T2 correction.** What it CAN do is move the automatic pick from
an objectively worse structural candidate (bar 164) to a substantially better
one already sitting in the candidate pool (bar 148) - closing most, not all,
of the gap.

## Round-2 findings, addressed (2026-09-15)

Codex round 2 (real staged files: this plan, `align_engine.py`, the real
`pair_history.jsonl`, `-Effort high`): "Needs one more revision before
C6/C7 build" - 3 MAJOR + 1 MINOR, all real, all actioned below.

1. **MAJOR - C6(b)'s anti-gaming rule is provably a no-op.** Confirmed by
   direct re-derivation: `_align_pair_landmark_aware` passes ONLY
   `_incoming_drop_anchors()` (sorted, ascending) into the primary
   `_search_anchors` call, and `first_drop_bar` is defined as that list's
   OWN first element. `drop_payoff` requires `incoming_anchor < first_drop_
   bar` - which is false for every element of the very list being searched,
   since none of them can be less than the list's own minimum. `drop_payoff`
   is therefore structurally 0 for every candidate the primary pass ever
   considers (my own T2 probe already showed this empirically - both bar148
   and bar164 candidates had `drop_payoff=0` - but I hadn't generalised it).
   A "later anchor wins only on strictly higher drop_payoff" rule can
   therefore NEVER fire in this pass. **C6(b) (the incoming-anchor
   early-return fix) is DROPPED from this round**, not patched - it needs a
   genuinely different, evidenced rule for a pass where pre-drop anchors are
   actually reachable, which is real, separate, unscoped follow-up work, not
   something to ship as a no-op labelled "fixed."
2. **MAJOR - C6(a) lacked an implementable contract, and an existing
   mechanism needed ruling out first.** Both actioned:
   - **Ran the existing `CUE_CONFIG.emit_bass_out` flag as a real T2 A/B**,
     exactly as asked, before writing any new code. Confirmed empirically:
     flipping it to `True` does NOT change T2's outcome (still bar 164) -
     matching the flag's own code comment ("ablation-tested 2026-08-17 to
     change 0 of 380 decisions"). Root cause, verified by dumping the full
     pair breakdown: `emit_bass_out` raises bar 148's cue WEIGHT (6->7), but
     `weighted_score` sums EVERY coincidental cue-pair across the WHOLE
     overlap window, not just the swap point itself - and bar 148 sits
     inside BOTH candidates' overlap windows (it's `overlap=33` for the
     bar-148 candidate, `overlap=17` for the bar-164 candidate, and bar 148
     is bar 164's own overlap START). Raising bar 148's weight therefore
     boosts BOTH candidates' `weighted_score` by the same +1, and the
     relative gap (bar164 - bar148) stays exactly 1 point either way (21-20
     before, 22-21 after). This is a structural property of how
     `weighted_score` aggregates, not a tuning problem - no cue-weight value
     fixes it, which is exactly why Codex's discrete-tier direction is
     right and a weight-boost approach (what `emit_bass_out` already tried)
     cannot be.
   - **Full implementable contract**, per Codex's spec: rename to
     `bass_out_payoff` (the "natural breakpoint" language in this file's own
     docstring is specifically the `bass_out_is_end=True` fallback case -
     genuinely different from this proposal, which applies when it's
     False). New rank tuple: `(drop_payoff, bass_out_payoff, weighted_score,
     entry_paired+exit_paired, -abs(progress-0.65), overlap)` - inserted as
     the SECOND tier, preserving `drop_payoff` as dominant. Exact match only,
     no tolerance, NULL-GUARDED (round-3 finding 1 - `bass_out_bar` is
     nullable while `bass_out_is_end` defaults False, so the predicate must
     check presence first or it can crash on an unannotated outgoing track):
     `bass_out_payoff = 1` iff `o.bass_out_bar is not None and not
     o.bass_out_is_end and outgoing_anchor == round(o.bass_out_bar)`, else 0.
     Ranks an already-admissible cue only - no new candidate generation, the
     `outgoing_anchor` must already be a real entry in `outgoing` cues (bar
     148 already is, via the existing `section:drop:end`/`section:outro:
     start` cue - `emit_bass_out` is not even required for the candidate to
     exist, only for it to WIN).
   - **Changed-pair audit is now an explicit build-order gate** (not just a
     baseline diff): before any baseline refresh, enumerate every pair in the
     380-pair corpus whose `o.bass_out_bar is not None`, whose
     `o.bass_out_is_end` is False, AND whose `round(o.bass_out_bar)` is
     already a real `outgoing` cue bar (T2's exact shape, same null guard as
     the tier itself) - inspect those specifically, in addition to reading
     every changed decision the refresh would report, per finding 1's fix.
3. **MAJOR - C7 canonicalisation's conflict rule contradicted itself.**
   "Keep latest/most-complete" and "surface conflicts for a human decision"
   cannot both be the rule. Fixed: canonicalisation is now FAIL-CLOSED on a
   genuine conflict (same (project, pair_index), disagreeing `verdict` or
   `bass_swap_delta_beats`) - a conflicting pair is EXCLUDED from the
   canonical scoring dataset entirely (not guessed at, not silently
   resolved) until a human-authored resolution record exists for it. Source
   precedence and a real schema (delta always a field, never only inside a
   free-text `corrections` string) are both part of Step 0's deliverable,
   not assumed.
4. **MINOR - shadow-mode wording overclaimed.** `find_similar_pairs` runs
   AFTER geometry is already locked, so there is no live candidate SET to
   have "preferred" one from. C7 shadow mode now reports a predicted
   swap-delta/direction, a confidence, the matched training record(s), and
   leave-one-project-out provenance - never phrased as a preference among
   alternatives that were never actually compared.

**Needs Codex's read (round 3) specifically on:** (a) whether dropping C6(b)
entirely this round, rather than attempting a narrower fix, is the right
call; (b) the `bass_out_payoff` tier's exact contract as now specified
(rank position, match semantics, the changed-pair audit gate); (c) whether
the fail-closed conflict rule for C7's canonicalisation is itself
well-specified enough to build against, given the real Black Book T3
conflict cited in round 2; (d) any other real corpus pair the
`bass_out_payoff` tier would move for the worse.

## C7 (was burn list C1) - feed pair_history into candidate scoring

**Step 0, now first and separately gated: canonicalise `pair_history.jsonl`,
FAIL-CLOSED on conflict.** Codex's round-1 finding 3: the real count is 25
unique (project, pair_index) observations, not 34 (Black Book pairs 1-9 are
logged twice under different sources); some directly conflict; schema is
inconsistent (a move delta sometimes lives in a field, sometimes only inside
a `corrections` string, often absent, sometimes present but STALE against the
record's own beat fields). Real non-zero-move count is 10 (7 earlier, 3
later), not "7-9."

Build a canonicalisation pass BEFORE any scoring code, in this exact order
(round-2 finding 3 and round-3 finding 3, both folded in - a naive
same-verdict-and-same-delta-field check misses real conflicts, see below):

1. **Normalise first.** For every record, DERIVE a delta from
   `sam_bass_swap_beat - claude_bass_swap_beat` (never trust the pre-existing
   `bass_swap_delta_beats` field on its own - round-3 found a real case,
   Black Book pair 4, where BOTH duplicate records carry
   `bass_swap_delta_beats: null` while their own beat fields imply genuinely
   different deltas, -96 vs -64 - two nulls read as "agreement" by a naive
   field comparison, which is exactly backwards).
2. **Compare on the normalised value.** Group by (project, pair_index); a
   group is a CONFLICT if its records disagree on `verdict` OR on the
   DERIVED delta (not the raw field) beyond a rounding tolerance of 4 beats
   (1 bar - `SNAP_BARS`'s own beat equivalent, the same bar-snap granularity
   every geometry decision in `align_engine.py` already rounds to, not a new
   arbitrary number).
3. **Fail closed.** A conflicting group is EXCLUDED from the canonical
   scoring dataset entirely - never auto-resolved by "keep the latest" or
   "keep the most complete" - until a human-authored resolution record
   exists for it. A non-conflicting group dedupes to one canonical row per
   key (any of its agreeing records; they agree by construction).
4. **Resolution record schema, round-4 finding 1 - VERDICT must be
   resolvable too, not only the delta** (new, small, hand-authored file this
   step also defines): keyed on `(project, pair_index)`; required fields
   `resolved_verdict` (one of the corpus's own real verdict values -
   `correct` / `corrected` / `correct_with_arrangement`; a delta-only
   resolution cannot produce a complete canonical row when the conflict is
   about verdict semantics, not position - real, verified examples:
   Black Book pair 5 has IDENTICAL zero delta in both duplicate records but
   disagrees `corrected` (a two_stage_bass automation change - the swap
   POSITION didn't move, but Sam still changed something real) vs `correct`
   [the coarser `auto_diff` source can't see a correction that doesn't move
   the swap beat at all]; pair 7 disagrees `correct_with_arrangement` vs
   `correct` the same way, over an arrangement extension), `resolved_delta_
   beats` (the position component, still required, may be 0), `resolved_by`
   (who decided), `date`, `reason` (one sentence); a resolution record takes
   precedence over the raw `pair_history.jsonl` rows it resolves,
   permanently, until a human edits it again.

This is its own reviewable unit of work, not a throwaway preprocessing step
folded into the scoring change.

**Step 1: SHADOW MODE ONLY.** `find_similar_pairs`
(`propose_arrangement.py:1008-1052`) runs AFTER `compute_aligned_positions`
has already called `align_pair` and locked geometry - it returns records, not
scoring features, and nothing threads it into the search today, and no
candidate SET is ever compared at that point. C7 adds a REPORTING path only:
for each real alignment decision, compute a PREDICTED swap-delta/direction
(not a "preference" among alternatives - none exist to prefer among) from the
canonicalised corpus (now reading real swap-delta data, not just matching on
BPM/structure), together with a confidence and the matched training
record(s), and log it alongside the decision actually made. Selection itself
is untouched. No code path lets this influence `_search_anchors`,
`plan_fill_or_cut`, or any admissibility check. Evaluated leave-one-PROJECT-
out (transitions within one mix are correlated, not independent samples per
Codex's round-1 finding 4).

**Step 2, explicitly deferred:** promoting C7 from shadow-reporting to an
actual nudge among already-admissible candidates - never an override of
admissibility, that boundary belongs to C8 alone per Codex's finding 5 - only
after shadow-mode evidence exists to design a defensible weighting scheme
from, not guessed up front. The open design questions from the 2026-09-14
draft (flat bonus vs magnitude-scaled, minimum corpus size per bucket) stay
open until shadow-mode data can answer them empirically.

## C8 - let a loop-extension-needing candidate compete on merit

**Deliberately left unscoped**, per Codex's finding 6, restated: existing loop
mechanisms only EXTEND material (cannot rescue a candidate already OVER the
max overlap, only one below the minimum), and "feasibility" cannot be read
off the numeric loop budget alone - `plan_fill_or_cut` can reject for
clean-loop availability, repeat limits, named-cue reachability, or
locked-swap constraints the budget number never sees. C8 needs an atomic
candidate object (alignment + a real dry-run fill/cut plan + effective
geometry + extension cost) before it can be scoped at all - real design work
for after C6/C7 are built, tested, and reviewed, not decided speculatively
here. Also now informed by the T2 investigation above: C8 was never going to
reach bar 157 either (it is not a registered cue), so C8's real value is
letting OTHER geometrically-infeasible-but-musically-strong candidates
compete, not a second route to T2 specifically.

## Proposed sequencing

1. **C6 - one piece now, one dropped**: build the `bass_out_payoff` tier
   only, verified directly against the T2 fixture (bar 148 must now outrank
   bar 164), the changed-pair audit (every non-end-bass-out pair whose
   bass_out_bar is already a registered cue, inspected before any refresh),
   and the full 380-pair baseline (inspect-and-attribute BEFORE refresh, per
   finding 1's fix). The incoming-anchor early-return piece (formerly C6(b))
   is NOT built this round - dropped per round-2 finding 1, real follow-up
   work, unscoped.
2. **C7 second**: canonicalise `pair_history.jsonl` first, fail-closed on
   conflicts (own Codex round), then build shadow-mode reporting with the
   corrected predicted-delta wording (own Codex round). No scoring influence
   until real shadow-mode evidence exists.
3. **C8 explicitly deferred** - unscoped until C6/C7 are proven, informed by
   whatever they reveal about how coupled swap-point selection and geometry
   really are in practice, and by this document's finding that T2 sits
   outside all three pieces as currently conceivable.

## What this document is asking Codex to do (round 4)

Round 3 (real staged files, `-Effort high`): C6's conclusions confirmed
sound ("Dropping C6(b) is correct"; the `bass_out_payoff` position and exact
match "safe once null-guarded"; the `emit_bass_out` A/B explanation
"accurate for T2"; the changed-pair audit "a sufficient superset"). 3 MAJOR
findings, all actioned in this revision: (1) `bass_out_payoff` and the
changed-pair audit are now null-guarded on `o.bass_out_bar is not None`; (2)
the C7 body text itself (not just the round-2 summary above it) now carries
the fail-closed wording and the predicted-delta phrasing - the stale
"keeping the latest/most-complete record" and "would have preferred" text
Codex quoted is gone; (3) C7's canonicalisation now derives the delta from
the two beat fields FIRST and compares on that derived value, never the raw
(sometimes null, sometimes stale) `bass_swap_delta_beats` field - directly
verified against the real Black Book pair 4 conflict Codex cited (line-by-
line against `pair_history.jsonl`: the `v21_v22_initial` record has
`claude_bass_swap_beat=2400`/`sam=2304` = -96 beats, `correction_delta_
beats: -96`; the `auto_diff` record has `claude=2368`/`sam=2304` = -64
beats, `corrections: ["bass_swap_moved:-64beats"]` - genuinely different
deltas, same verdict "corrected," neither raw record even carries a
`bass_swap_delta_beats` field to compare in the first place, confirming
Codex's point exactly), plus a defined resolution-record schema.

Review this revision against those three findings specifically, plus
anything else. Read the real source directly - `Source/align_engine.py`
(`_search_anchors`, `_align_pair_landmark_aware`, `CUE_CONFIG.emit_bass_out`),
the real `Documentation/Mix Patterns Library/pair_history.jsonl`, and the
real Freejak/HARTY stem-analysis JSON cited above - rather than trusting
this document's paraphrase. State "NO MATERIAL OBJECTIONS" if none of that
applies - this is intended to be the round that converges.
