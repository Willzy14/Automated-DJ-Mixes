> **STATUS 2026-11-20: PARKED. Do not build as written.** Codex and MiniMax both returned CORRECTION (reviews in `D:\Claude Workspace\Automated DJ Mixeseview-plan-tailloop\`): the 13-of-13 evidence cannot be proven from the records, Rule S misses the legacy and decisions paths, and it can break a working transition. Sam's phrase rules from the same day (answer key in `Documentation/Golden Sections/`) replace its premise: loops exist only to lengthen a blend and are placed from section and fill phrase points.

# Plan: outgoing tail loops may not start before the bass swap (Rule S)

**Brain:** Claude (Opus architect, 2026-11-20; transcribed and owned by Claude Fable). Status: PLAN, under review, nothing built.
Covers burn list D16 (the Core Sample hard failure), and narrows D17 and D18.

## Summary

1. The Core Sample build fails because the planner put a loop in Jewel Kid BEFORE the bass swap, so the swap lands in the middle of a repeat.
2. All 13 tail loops Sam made or kept across the three corrected mixes start AT the swap or AFTER it. None starts before it.
3. So the rule is: a loop on the outgoing track may only start at the swap or later.
4. When the rule refuses a loop, the outgoing track plays its own ending. The overlap gets shorter (Jewel Kid into Zaro: 52 to 42 bars; Sam chose 37). No transition is dropped.
5. This one rule fixes the crash and matches Sam on that transition. It changes nothing else in the three corrected mixes.
6. "Sam deletes 1 and 2 bar stutter loops" does NOT hold: he kept several of those and deleted 4-bar ones. No rule predicts his other deletions.
7. Sam adds tail loops more often than he deletes them, but most additions come with a moved swap or rest on one or two examples. Not built yet.
8. "Pull every overlap toward 32 bars" would fight about half his transitions. Not built.
9. Build behind a switch, prove it on the three corrected mixes, the two uncorrected mixes and the corpus, then turn it on.

## 0. Verified versus assumed

Verified by reading files or read-only replays:
- Today's failure: `d26/22.09.26 Tech House Core Sample/Output/MIX_PLAN.json` puts Jewel Kid's loop at arrangement beat 2756 (source 644-660, x2, plus 8 beats); the swap is at 2784; repeat boundaries are 2756, 2772, 2788, so the swap sits 12 of 16 beats into the second repeat. The CTRL run has the same fault (insert 2756, swap 2776). Only Transition 5 raises.
- Cause: replay of `align_pair` and `plan_fill_or_cut` gives arr 131, handoff_bar_out 167, outro 160-173, so the locked swap gap is 7 bars. Section targets are beyond the 12-bar reach cap, so the landmark `kick_gap_206_208:end` is chosen (gap 10). The primary search `pick_cue_bounded_drum_loop` needs a loop length that divides 7 within 8 repeats (`align_engine.py:2061-2063`, `2469`); none does, so it returns None. The D2 fallback (`align_engine.py:2541-2564`, `2574-2583`) then takes `loop_windows` (160,168) and chunk (161,165). The fallback only checks "does not end before the swap" (`:2635-2643`); it never checks "swap on a repeat boundary". That gap is the cause.
- Deeper effect: any loop placed before the swap pushes the outgoing track's paired swap landmark later (Jewel Kid's `kick_dropout:start`, source bar 167, moves from arrangement beat 2784 to 2824). The validator cannot see this when the swap happens to fall on a boundary. DFTD T1 is such a case (1-bar loop inserted 1 bar before the swap; reconcile passed).
- Sam's 13 tail-loop groups (via `learn_from_correction._geometry_side` on the three Sam sets): at the swap: Core T1 (HARTY 2bx6), Core T10 (Shilla 4bx2), Defected T1 (1bx7), T2 (8bx2), T7 (2bx3), T11 (4bx4). After the swap: Defected T1 second loop (+8.5 bars), T5 (+12), T9 (+15), T10 (+15), Sept T2 (+0.5), Sept T4 (+4), Core T4 (+16). Before the swap, or swap inside a repeat: 0.
- Pipeline loops: the 22.09 build had loops before the swap on T5 (-5 bars) and T9 (-1 bar); Sam removed both. DFTD T1 is -1 bar. Every other pipeline loop in the Core, Sept, Defected CTRL, DFTD, Tech House Selection and August plans starts at or after the swap.
- Policy fields `transition_policy.py:51-52`, `131-141`; validator rule `validate_mix_plan_als.py:315-355` (pinned by `Tests/test_mix_plan_reconcile_fixture.py`); clips split at swaps only `apply_automation.py:1613-1641`; MixPlan repeat cap `mix_plan.py:551`; the baseline pins `plan_fill_or_cut` specs and the derived `overlap_policy` (`Tests/test_alignment_baseline.py:143-195`) under the default policy.

Assumed, not verified: that `al.swap_beats` is set before MixPlan freeze in `propose_arrangement` (serialised at `:2257`); how many corpus pairs the rule affects (piece D measures it); how Defected would look on the new analysis (Phase 2 raises at T7 today).

## 1. The decision

**Rule S (build now): an outgoing tail loop must start at or after the bass swap.**
- In landmark mode, if `outro.start_bar < al.handoff_bar_out - 1e-6`, plan no outgoing tail loop and record why.
- Failure A becomes impossible by construction: with no loop before the swap the swap can never sit inside a repeat; a swap exactly at the loop start is a repeat boundary, which the validator accepts. It also closes the D2 fallback gap (under Rule S the locked swap gap is always 0).
- Evidence: 13 of 13 Sam tail-loop groups over 3 mixes, plus 2 of 2 pipeline pre-swap loops removed by Sam (Core T5, T9, 22.09 build).
- Effect on the three corrected mixes: Core T5 only ("4bx2+2", overlap 52, becomes no loop, overlap 42; Sam: no loop, 37). Holdouts: DFTD T1 loses its 1bx7 loop; Tech House Selection unchanged.

When the rule refuses a loop: the transition keeps its alignment and swap; the outgoing plays its natural end; the overlap drops by the loop's length. It cannot create a new raise: the refusal returns before candidate search (so the ValueErrors at `align_engine.py:2526`, `:2619`, `:2640` cannot be reached for that pair); the raw overlap already passed the 16-48 bar window; `handoff_bar_out <= n_bars` (`:2472-2474`) means the outgoing still plays past the swap. Never abandon a transition for a refused loop. Do not re-target the loop to start at the swap yet (needs a mid-track insert point).

R1 and R3 (already built) stay unchanged. R1: Core T2, Core T9, August T6. R3: Core T7; contradicted by Defected T2 (16 bars), T5 (20), T11 (16), an existing production risk, not created here (open question 2).

Not supported, do not build: a rule to remove the other reachable loops Sam deleted (Core T3, Sept T7, Defected T4, T8). Kept and deleted loops overlap on chunk length (kept Defected T1 1bx7, Core T1 2bx5, August T8 1bx6; deleted the 4-bar loops on Core T3, T5, Sept T7), target type and source.

Sam's added loop shapes: offer none now.
- Own-ending tail on a track with no outro (R4): Core T4, August T4 (2 transitions, 2 mixes). Later.
- Short tail hold after the swap (SLV `tail_hold`, with MiniMax C2): Sept T2 (1); Sept T4 needs his moved swap. Later.
- Loop starting at the swap that lengthens a short post-swap tail: Defected T2, T7, T11 (T2 and T7 also moved the swap); counter-examples Core T5, August T5, T7. Later; mixed evidence.
- Incoming intro loops (R5, `intro_hold` with C1): Core T4, T6, Sept T1, T6, Defected T8 (5); 3 measured false fires on Core. Later.
- Re-anchor (R2): now only Core T3 (Core T8 is fixed by the corrected analysis). n=1. Later.
- 1-bar x15 holds: Youngr only. Later.

## 2. Where it lives and how it reaches production

- Policy field `TransitionPolicy.outgoing_tail_after_swap_only: bool = False` (beside `transition_policy.py:51-52`).
- New lane `INTERIM_V1_TAILSAFE = replace(INTERIM_V1, name="interim_v1_tailsafe", outgoing_tail_after_swap_only=True)`, registered in `POLICIES` (`:163-166`).
- Planner, `align_engine.plan_fill_or_cut`: reset `al.outgoing_loop_refused = None` next to `:2285`; inside `if landmark_mode:`, after the R1 block (`:2428-2442`) and before the target lists (`:2443-2445`): if the flag is on, `not skip_outgoing_loop`, an outro exists and it starts before the handoff, set `skip_outgoing_loop = True` and `al.outgoing_loop_refused = {"reason": "outro starts before swap", "outro_start_bar": ..., "handoff_bar_out": ...}`. Add `outgoing_loop_refused: dict | None = None` to `Alignment` next to `:795`, `:805`. Mutually exclusive with `outgoing_loop_abandoned` and `outgoing_loop_not_needed`.
- CLI: `propose_arrangement.py:2400-2401` hardcodes `choices=("interim_v1","sam_v1")`; change to `sorted(POLICIES)`.
- Production: set the field True in `INTERIM_V1` in its own commit with the baseline refresh, only after every check in section 3 passes. If piece D shows the rule affects more than 10 percent of looping corpus pairs, get Sam's ear on DFTD T1 plus two corpus rows first.

## 3. Acceptance (no listening)

(a) Core builds through Phase 3 on fresh copies of the d26 Core folders (`...__TS`, `...__CTRL_TS`), with `--transition-policy interim_v1_tailsafe`: propose_arrangement (with `--hints`, `--tempo-arc`, `--mix-plan`, `--report`), apply_automation, validate_als, validate_mix_plan_als all exit 0. Report diff against today's: only T5 changes (`out_tail_loop` gone, `overlap_bars` 52 to 42, `outgoing_loop_refused` set); T1, T3, T10 loops byte-identical; every other track position unchanged. Then run the learner (`--dry-run`) on the automated set against Sam's.

(b) Agreement with Sam's loop decisions: new tool `Tools/loop_truth_score.py` reads MIX_PLAN.json and ARRANGEMENT_REPORT.json and compares, per transition and side, with `Tests/fixtures/sam_loop_truth.json`: loop present or absent; chunk class (1-2 / 4 / 8+ bars); total class (<=4 / 5-12 / >12 bars). Prints before and after counts and names every transition that gets worse. Truth file built by script from `pair_history.jsonl` for Sept and Defected; Core from the measurement above plus the d26 REPORT rows. Predicted outgoing-tail presence agreement: Core 7/10 to 8/10; Sept 4/7 to 4/7; Defected (CTRL only) 4/11 to 4/11; total 15/28 to 16/28; none worse; incoming side unchanged at 23/28. All three mixes are development data.

(c) Corpus replay: `Tools/d15_outro_loop_replay.py` gains `--policy NAME`, the fields `outro_start_bar`, `handoff_bar_out`, `loop_starts_before_swap`, `outgoing_loop_refused`, and a `--project-dir` mode. Run before (default), after (`--policy interim_v1_tailsafe`), and `--diff`. Pass: 0 new raises; raise and align_raise rows identical to today's "before" (re-measure today, do not trust October's counts); every changed row has `loop_starts_before_swap=true` in "before" and goes from a loop to `refused`; read every changed row; report loop count and overlap change per refused pair.

(d) Alignment baseline: with the flag off `pytest Tests/test_alignment_baseline.py` stays green and byte-identical after pieces A and B merge. At the production flip the diff may touch only `plan` and `overlap_policy`, only on exactly the pair set piece D refused; `arr_offset_bars`, `paired_cues`, `overlap_bars`, `swap_progress` must not move. Refresh with a stated reason; use `--accept-shift` only for a named guard if more than 10 pairs flip. Also run `Tests/test_align_engine_golden.py`.

Holdouts: 06.10.26 DFTD and 05.10.26 Tech House Selection have no Sam corrections. They can show both build through Phase 3 on the lane and only the predicted transitions change. They cannot show whether Sam agrees. A true agreement holdout is the next mix Sam corrects, built on the lane.

## 4. Not building now

- "Overlap toward 32 bars": Sam's overlaps are within 32 plus or minus 2 on 15 of 28 transitions (Core 6/10, Sept 4/7, Defected 5/11), range 17.4 to 72.4. Overlap is a result of entry, swap and tail choices. A target would fight about 13 of 28.
- Any removal rule keyed on chunk length (contradicted).
- SLV `intro_hold` / `tail_hold`, the 0.08 silence waiver, R2, R4, R5, R6, 1-bar holds (1 or 2 transitions each, or need swap moves; `mix_plan.py:551` would also reject 1bx15).
- Re-targeting a refused loop to start at the swap. Changing R3's 12-bar cap. Loosening the validator.

## 5. Risks and the cheapest check

1. The rule removes a loop Sam would keep (DFTD T1): piece D's refused count; if large, listen to DFTD T1 and two corpus rows.
2. Shorter overlaps feel abrupt: the replay lists overlap change per refused pair; flag any ending below 24 bars.
3. The outgoing ends off a section line (D15's complaint returns): the replay records distance from the natural end to the nearest incoming section start for refused pairs.
4. The new reason field is confused with abandoned / not-needed: unit test that the three are mutually exclusive.
5. A baseline refresh hides an unrelated change: script compares the refreshed pair set with the replay's refused set and requires equality.
6. Errors in the truth table: generate Sept and Defected rows by script; hand-enter only Core, second reader checks.
7. Cannot tell which policy built a set: write the policy name into the report or plan (small follow-up).

## 6. Build split (parallel, separate functions)

- A. Planner rule: `align_engine.plan_fill_or_cut` (reset plus guard), the `Alignment` field, `transition_policy.py` (field plus lane). Test `Tests/test_tail_loop_swap_side.py` in the synthetic style of `Tests/test_outro_loop_simplify.py:44-143`: outro before handoff gives no tail and sets `refused`; outro at handoff unchanged; flag off identical to today; field resets between calls; a Jewel-Kid-like geometry (outro 160, handoff 167, gap 10) is refused.
- B. Freeze-time guard plus report: in `propose_arrangement.py` a new `_assert_swaps_clear_of_outgoing_loops` called just before `build_mix_plan` (`:1825`), same arithmetic as `validate_mix_plan_als.py:315-343`, raises in Phase 2 with a hint naming the lane, applies whatever the policy; serialise `outgoing_loop_refused` next to `:2270-2272`; CLI choices at `:2400`. Do not edit the validator. Parity test `Tests/test_freeze_swap_guard.py` runs both on the same fixtures. Contract names `outgoing_loop_refused` and `interim_v1_tailsafe`, read with `getattr` so B does not wait for A.
- C. Truth scorer: `Tools/loop_truth_score.py`, `Tests/fixtures/sam_loop_truth.json`, `Tests/test_loop_truth_score.py`.
- D. Replay: `Tools/d15_outro_loop_replay.py` (options in 3c); test `Tests/test_d15_replay_policy.py`.
- After A merges: E, the SLV section 4.4 `candidates_assessed` honesty fix (same function as A); F, burn list updates for D16, D17, D18 and an SLV plan revision applying C1, C2, C4, C8.

## Open questions for Sam

1. When a track's outro starts before the bass-swap point, should the pipeline play the outro straight through with no loop, or loop starting at the swap?
2. Should the 12-bar limit on the pipeline's outgoing tail loops apply to every genre, or only to tech house? (On Defected you added 16-20 bar tail loops.)

## Found unasked by the architect

1. The brief's "4 of 16" and "4 of 8" offsets are from the 22.09 build; on today's main T5 is 12 of 16 beats into the second repeat and T9 has no loop; only T5 raises.
2. Root cause is a planner hole: the D2 fallback never applies the swap-on-boundary check the primary search uses (`align_engine.py:2541-2653` against `:2061-2063`).
3. Pre-swap loops misplace the swap landmark even when the validator passes (DFTD T1).
4. Sam kept 1-2 bar loops (Defected T1, Core T1, August T8) and deleted 4-bar ones (Core T3, T5, Sept T7).
5. Sam added tail loops on 8 transitions (Core T4; Sept T2, T4; Defected T2, T5, T7, T10, T11) and removed 5 that today's code still makes (Core T3, T5, Sept T7, Defected T4 and T8 on CTRL).
6. R3 in production is contradicted three times on Defected.
7. The Core learner rows in the d26 REPORT are mislabelled: their "intro loops" (T4 4bx2, T9 4bx2) are the next transition's tail loops, because with no automation `swap_arr` is None and every repeat group counts (`learn_from_correction.py:853-854`).
8. Burn list D16 says the swap fault would be "resolved by R1/R3"; R1/R3 merged and T5 still fails.
9. SAM_V1 does not have R1 or R3 (`transition_policy.py:148-161`); any A/B on SAM_V1 silently turns them off.
10. Validator edge: a swap exactly at the end of a loop that has a partial repeat is reported as "inside" (`validate_mix_plan_als.py:322-343`).
11. The replay tool always uses the default policy today.
12. Defected cannot be rebuilt on the corrected analysis without a T7 decision.
