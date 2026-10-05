# Review: `Documentation/Plans/short-loop-vocabulary-plan.md`

**Verdict: CORRECTIONS** (not rubber-stamp; the plan is evidence-honest but ships at least 4 design holes that the test plan will not catch on its own).

The plan reads well, is honest about n=4 / n=1 calibration (line 12, 35-37, 124), and the corpus-gated rollout in §6 is the right shape. But "honest about thin evidence" is not the same as "robust to thin evidence" — and the test plan as written lets three known holes ship.

Below, broken out per question. Line numbers cite the staged plan.

---

## 1. Root-cause plausibility

The framing at **line 13** ("the gate did not reject Sam's windows — the planner never offered them") is correct for the four windows Sam used, but the supporting evidence is thinner than it looks.

- **Line 14**: "Run through the unchanged strict gate, all four of Sam's source windows PASS." Then only `ss` is reported for the three incoming windows (T1 0.91, T4 0.96, T6 0.98). The plan never shows `period`, `insert_level_match`, `worst_beat_dip`, or `silence_fraction` for those three. The T2 outgoing window is "Youngr intro beats 6-10 again" — same source as T1, so its pass is implied, not measured. **Verify before trusting**: re-run the gate on each of the four windows with all six metrics printed, not just `ss`.
- **Line 35-37**: "This is the only gate rule that is too strict for short windows, **on n=1 used window**." The plan is honest. But this is the foundation for the 0.08 waiver (line 109-115). **Verify before trusting**: count how many other Sam-heard windows would have been admitted at 0.05-0.08 across 22.09.26 and 14.08.26. If n=1 is the only one, the waiver is over-fit.
- **Line 24**: "The report's reason 'no loop-source passed the quality checks' is factually wrong for this pair." This is a reporting bug, not a planning bug. The new `candidates_assessed: N` field (line 126-127) is the right fix, but note this is a separate defect the plan rolls into a feature PR — consider splitting the bugfix from the feature.
- **Line 19-22 (T2)**: "The D2 fallbacks need `o.loop_windows` or an outro of 2 bars or more." Worth confirming: the D2 fallback at `:2569-2594` is described as using "the latest loop_window, or an outro of 2 bars or more." If Youngr's outro is 1 bar and `loop_windows` is empty, D2 falls through to abandonment. Correct. But the plan doesn't say what *would* have been picked if D2 had a candidate — i.e., the order of preference for D2 vs the new branch 5 (line 80). The plan says branch 5 fires "only when its trigger holds **and** the existing paths produced nothing for that side" (line 81-82). OK, but the trigger requires D2 fall-through. **Verify**: trace the order: branch 3 → D2 → branch 5. Is there a path where D2 produces a candidate that fails the gate, abandonment is recorded, then branch 5 fires? Yes — that's the intended path. Confirm the report order is D2 then branch 5.
- **Line 28-32 (T5 RSquared)**: The plan correctly identifies T5 as a "genuine gate rejection, and the right outcome" — but T5 is also the *control* that argues the gate is not the blocker for Sam's windows. Worth saying: T5 is the only transition where the gate *did* reject, and the rejection was correct (Sam also declined). That is the only empirical evidence in the plan that the gate is well-calibrated for the cases it currently sees. **Verify**: T5 is one data point; the 14.08.26 corpus has 215 D15 loops (per the v5 analysis line 84) — how many of those would the new short-hold vocab want to *replace*? The plan doesn't say. If 50/215 would be replaced, that's a 23% overlap with existing tail logic and a real test-plan gap.

**Root cause is plausible** — the two real blockers are `max_loop_repeats=8` and `silence_fraction>0.05` for the specific cases the plan names. But "plausible" ≠ "verified."

---

## 2. TRIGGER conditions — over-fire risks

The triggers are well-chosen for the four positives, but each has at least one fire mode that the plan should reject.

### 2a. `intro_hold` — "Never block on vocals" (line 89)

This is the biggest hole in the plan. A 4-bar vocal intro is not a "fill" — it is the song. Many pop edits have the vocal hook in the first 4 bars (Sam's own genre). Looping bars 0-4 with vocals on them produces a stutter that is exactly the "vocal clashes" the question asks about.

- **What blocks this in the existing path**: `pick_cue_bounded_drum_loop` requires drums-on, bass-off (line 92). The plan's intro_hold does not.
- **Counter-argument in the plan**: the four Sam windows are all drums-only intros. But those four are all the plan has. The trigger has no in-sample test of a vocal-intro case.
- **The plan's own detector exists**: line 110-111 says the 0.08 waiver only fires "if intro `stems_on` contains drums and no vocals." So the *waiver* path has a vocal check. The *strict* path (line 84-92) does not. This is inconsistent.
- **Fix**: require the intro_hold window's `stems_on` to include drums AND not include vocals. If a 4-bar window has only vocal, decline with reason `vocal_intro`.

**Verdict on intro_hold trigger: incomplete.** Without a vocal check, it will stutter pop intros.

### 2b. `tail_hold` — source phrase may not contain drums (line 102-104)

The plan allows a "last whole 4-bar phrase inside its final pre-outro section, on that section's 4-bar grid, not vocal/fill blocked. Bass allowed, because the hold is post-swap and gets the kill." But:

- "Bass allowed" because of the swap-side bass kill. But a *drumless* phrase (hats only, or vox + pad) is not rescued by a bass kill. The plan does not require the source to contain drums.
- v5 T1 HARTY (line 91 of the v5 analysis) has an outro that's "kickless 168-186." A 4-bar phrase in the kickless outro is hats-only. The plan would admit it.
- The HARTY intro-drums source in v5 T1 is the case the plan warns about at line 92-93 ("the `pick_cue_bounded_drum_loop` fallback-to-intro case its own comment at `:2057-2062` warns about"). The new tail_hold has the same problem.
- **Fix**: require the tail_hold source to include drums in `stems_on`. Decline with `source_has_no_drums` otherwise.

**Verdict on tail_hold source: incomplete.** Hats-only loops are not saved by bass kill.

### 2c. `tail_hold` — could mask a real dropout (line 102-104)

The plan keeps `worst_beat_dip` strict (line 114) and the swap-side assert (line 105). But:

- A 4-bar phrase with a kick dropout at bars 2-3 (kick off for half a bar, then back) passes `worst_beat_dip` if dip threshold is e.g. 6 dB. v5 T7 Freejak (line 105-106 of v5 analysis) is exactly this case — "cut at bar 156.5 (its last kick, dropout 157-165)". Sam cut AT the dropout. A 4-bar phrase that *contains* a dropout hole is the failure mode.
- The plan's R5 (line 175) covers grid phase but not dropout structure.
- **Fix**: require the source phrase's kick envelope to be steady (no half-bar-or-longer kick dropout within the window). Use the existing kick-detector (line 80 of v5 analysis mentions "Kick-Detector landmarks").

### 2d. R1 over-fire on Detlef T2 (line 124, 171)

The plan acknowledges: "fires on T2 Detlef (intro 8, 0-8 passes). Sam entered 8 bars earlier there but played drop_1 instead of looping, so this is a false fire for the loop mechanism."

- The mitigation is "lane-only" (R1, line 171). That means: it's gated behind the policy flag. In production (INTERIM_V1 off), it doesn't fire. In SHORT_HOLD_V1 (the A/B lane), it does. **OK for INTERIM_V1, but: the corpus test (line 160-164) must report the false-fire rate**, and the test plan doesn't list a false-fire pass bar (only "0 new raises" and unchanged tail rows at line 163).
- **Fix**: add a pass bar to the corpus test. Suggested: false-fire rate ≤ 1 transition per 100 ordered pairs across the 14.08.26 + 22.09.26 corpora. Above that, do not flip INTERIM_V1 (line 120-121).

### 2e. R2 D15/D1/D2 regression (line 173-175)

The plan claims "none by construction" for D15/D1 and "keep `outgoing_loop_abandoned` exactly as the 'wanted but failed' record" for D2/D2b. This is the right call. But:

- The plan says "Report both, because merging them is the ambiguity D2b removed." Worth confirming the *consumer* (e.g., the validation that gates MixPlan freeze) reads `outgoing_loop_abandoned` and a separate `short_hold` record as distinct signals. The plan says at line 144: "Check `mix_plan` / reconciliation for loop-count or extension caps before building. Not read here." That is an honest gap. **Verify**: what does the MixPlan validator do with a transition that has both `outgoing_loop_abandoned` and a successful `tail_hold`? If it treats the abandoned loop as evidence the transition is "broken," the new pass could be blocked.

### 2f. Looping a vocal (covered above in 2a)

Same point. The strict path has no vocal check. The waiver path does. Inconsistent.

---

## 3. The 0.08 silence allowance — is it safe?

The waiver is well-scoped on paper (line 109-115):

- Only `intro_hold`, only when `stems_on` contains drums and no vocals, only when `worst_beat_dip` and both `self_similarity` terms pass.
- Labelled `gate: relaxed` in the report.
- Default None.

But:

- **n=1 calibration** (line 35-37). 0.08 sits between Jewel Kid 0.054 (accepted) and All Is Fine 0.103 / Sorley 0.21+ (rejected). One point on each side. This is over-fit on the smallest possible evidence.
- **Could a 0.08 window sound like a dropout?** At 0.08 silence_fraction over 4 bars (16 beats), that's ~1.3 beats of silence. Could be a single kick dropout in the middle of a hat pattern. A human ear hears this as "the kick dropped out" — exactly the dropout-masking failure mode the question asks about. The plan's `worst_beat_dip` check is over the *whole* window, so a 1-beat local dip is below typical thresholds.
- **The Sorley 0.21+** at line 112 — what was Sorley at? The plan mentions 0.21+ for Sorley but Sam declined. Sorley's intro silence is also a "drum-only" window. Why did Sam decline? The plan doesn't say. If the reason was "sounds like a dropout," then 0.21 is a real upper bound and 0.08 is fine. If the reason was "doesn't match the track's energy" or "vocal ad-lib tail," then 0.08 is over-calibrated.
- **The "no vocals" check is `stems_on` not the audio**: a reverb tail of a vocal from bar -1 bleeding into bar 0 would have `stems_on: drums, no vocals` but the audio has a vocal-ish tail. The plan should require `stems_on: no vocals` AND a measurement of vocal-band energy in the window below a threshold.
- **The waiver is not sunset**: line 175 says "Any proposal to widen it... repeats the 2026-08-25 blended-score un-catch class." Good. But the plan doesn't say what to do if the corpus replay shows the waiver causes 1+ bad loops. Suggested: cap the waiver at 30 days / 1 quarter; re-justify or remove.

**Verdict on 0.08: directionally right, not safe to ship without (a) a third calibration point in the 0.05-0.08 range, (b) a vocal-band energy check, (c) a sunset/re-justify clause.**

---

## 4. Cross-swap, MixPlan freeze, paired_boundary, validator gates

The plan's swap-side asserts (line 88, 105) are the right design, but the plan under-specifies what happens at the edges.

### 4a. T1 swap-move (line 38)

Line 38: "Swap arr 384 falls inside the hold (Sam also moved the swap 14 bars earlier)." Line 87: "Must end at or before the swap. Guaranteed, because the swap clips do not move."

These are in tension. The plan's intro_hold will not reproduce Sam's T1 (it targets 16 bars of hold, end at swap, while Sam's hold extends 14 bars past the swap). The plan's predicted-vs-Sam table at line 130-131 shows: "T1 in | intro_hold Youngr bars 1-3 (2b) x6 = 12b, entry Scuba 78, overlap 22->34 | loop + source yes; length close; entry no (Sam also moved the swap)."

- The plan's intro_hold alone cannot reproduce Sam's T1 because T1 combines intro_hold + a swap move. The plan does not propose the swap move.
- **Question 4's premise**: "the new loop crosses the swap." The plan's design prevents that (line 88: "must end at or before the swap"). But Sam's actual mix has the hold crossing the swap. The plan does not address how to handle a track where the user wants both.
- **Fix or clarification needed**: either (a) the plan says explicitly "we do not handle combined hold+swap-move; that's a separate feature," or (b) the plan allows `intro_hold` to extend up to the swap minus a 0-bar lead-in, which it does, and notes that Sam's T1 swap-move is out of scope.

### 4b. `paired_boundary` and validator gates (line 144)

The plan says "Check `mix_plan` / reconciliation for loop-count or extension caps before building. Not read here." This is an honest gap, but it's the load-bearing one for whether the feature can ship.

- **Verify**: what does the MixPlan validator check? If it checks that outgoing end = incoming start of next transition, a tail_hold that extends the outgoing should be fine (outgoing end moves, incoming start is unchanged). If it checks that loop count ≤ 1 per transition, the new `tail_hold` is in addition to branch 3 (which is suppressed by the trigger at line 99: "Branch 3 produced no `outgoing_tail`"). So count = 1. OK.
- **Verify**: the new branch 5 is at the *end* of `plan_fill_or_cut` (line 80). Is the plan written to a `FillCutSpec` list? If so, the order matters for downstream readers. Worth confirming branch 5 appends after all others.

### 4c. Consumer changes — apply_automation on split clips (line 144-152)

The plan claims "No new automation is needed" (line 47) because holds respect swap side. But the held tail is a *split* clip (line 149 introduces `split_named_clip_at(source_beat)`).

- **Verify**: when `apply_automation` writes the post-swap bass kill + fade, does it key on the new (split) clip or the original? If on the original, the automation is applied to the pre-split clip's automation lane and the new split clip has no automation. The plan says (line 47) "`ov_end = out_t.arr_end`" — the outgoing's end. After the split, the outgoing's end is the new end. If `ov_end` is read at apply time from the post-split clip, the automation is written to the new (held) clip. If from the pre-split, it goes to the original. **This needs a test.**

### 4d. The mid-intro window case (line 91)

"split the intro at `window_end`, move the head earlier by `extra`, and put the copies between head and rest."

- This is the Youngr case. Youngr's intro is 12 bars (the plan says 12 at line 92). If the window is bars 1-3 (2 bars), the head is bars 0-1, the rest is bars 1-12. Split at bar 1. Move head earlier by extra (12 - 8 = ... wait, extra = 16 - 12 = 4). So head moves 4 bars earlier. Head is now bars -4 to 1 (4 bars of... what?). The plan says "the head earlier by extra" — but the head is 1 bar. Moving it 4 bars earlier means it starts 4 bars before the original intro. But the original intro started at the entry point. So entry moves 4 bars earlier.
- This is fine if the outgoing has room. But the plan needs to check: does the outgoing's end + 4 bars extend past the previous transition's incoming end? In Sam's T1, Scuba's outro is 2 bars (the plan says outro=2 at line 101, in the trigger example). If Scuba's outro is 2 bars and the previous transition's end is 2 bars after Scuba's outro start, the entry can move 4 bars earlier without colliding. **Verify**: trace Scuba's geometry. The plan says entry moves from Scuba 84 (Sam) to Scuba 78 (predicted) — a 6-bar move. That's a 6-bar earlier entry, requiring 6 bars of Scuba content. The plan says "overlap 22->34" — overlap grows by 12. So 6 bars of Scuba head is used, then 6 bars of Youngr (intro head + extra), then the loop, then the rest. The math works if Scuba has 6+ bars of pre-outro content. The plan says "Scuba bar 84" as entry — that's bar 84 of Scuba, which is 22 bars before swap. Sam's overlap is 22 bars. Predicted overlap is 34 bars. So 12 more bars. Youngr's intro is 8 bars (line 92 says Youngr intro is 8). Plus 6 extra = 14 bars of Youngr pre-swap. The plan says predicted extra is 12 (line 130: "intro_hold Youngr bars 1-3 (2b) x6 = 12b"). Hmm, 6 extra = 12/2 = 6 copies. 8 + 12 = 20 bars of Youngr pre-swap. The swap is at Youngr bar 20 (line 92). So 20 bars of Youngr pre-swap exactly reaches the swap. The plan says "Must end at or before the swap" (line 88). So end at 20, swap at 20. Exactly at the swap. The plan allows "at or before," so OK.
- But the predicted entry is 6 bars earlier. Does the plan check that the previous transition's outgoing has 6+ bars? **Verify**: confirm Scuba's pre-outro region has 6+ bars of usable content. The plan doesn't say.

---

## 5. Rollout: "off in INTERIM_V1, A/B policy first"

This is the right call given n=1 (line 12, 35-37, 124). The test plan (line 154-168) is mostly right. The gaps:

### 5a. Test plan gap that matters most: no held-out gate on the INTERIM_V1 flip

The test plan has:
1. Unit (line 154-159): synthetic + 05.10.26 fixture.
2. Corpus (line 160-164): 14.08.26 380 pairs + adjacent mix-order pairs of every `_Stem Analysis` project. Pass bar: 0 new raises, unchanged 26 align_raise + 1 raise, unchanged outgoing_tail.
3. Re-score (line 165-168): truth table of n=4 Sam + ~7 negatives.

Then line 168: "Then build one fresh mix on SHORT_HOLD_V1 for Sam's ears."

**The gap**: the re-score on n=4+ is in-sample (it's Sam's 4 + the 22.09.26 / 14.08.26 / Sept corpora, all of which have informed the rule design). A held-out mix is the *last* step, after the INTERIM_V1 flip is already possible.

- The plan should require: a held-out mix scored on the truth table must hit ≥ 75% (3/4 or better) before INTERIM_V1 is flipped. Without this, the flip can happen on in-sample evidence alone.
- The plan should also require a false-fire rate ≤ 1% on the 14.08.26 corpus (per §2d above).

### 5b. The 0.08 waiver is gated behind its own replay diff (line 121)

Good. But: the waiver's first ship is the 0.05→0.08 relaxation. There is no plan for what comes after if 0.08 is too lax or too tight. A re-calibration step is missing.

### 5c. Test plan covers D2b reporting change but not the negative impact

Line 162-163: "existing `outgoing_tail` rows unchanged." This is a regression check. But the new short_hold rows are *additive* — they don't replace existing tail logic. So the truth-table re-score is the only check on whether short_hold fires correctly. The plan should explicitly call out: "if short_hold fires on a transition that has an existing `outgoing_tail`, that is a bug — the trigger must suppress."

### 5d. The unit test mentions "byte-identical" INTERIM_V1 output (line 155-156)

This is the regression check for the off-by-default case. But it requires the new code to be present and inactive. If the new code is added in a separate PR, the byte-identical check is for the PR that adds the inactive code. If the policy is added in the same PR, the byte-identical check covers only the field absence. **Verify**: the byte-identical test should be run *before* the new fields are added (existing behavior) and *after* the new fields are added with `intro_hold=False, tail_hold=False` (regression check). The plan implies this but doesn't say.

### 5e. R3 gate-softening creep is well-bounded (line 173-174)

"One check, one kind, one ceiling, default None, and stays labelled. Any proposal to widen it... repeats the 2026-08-25 blended-score un-catch class (404 bad loops passed)."

- The 404 number is a real anchor. Worth carrying that into a comment in the code (e.g., `transition_policy.py` docstring) so the next person sees the historical cost.

---

## Summary of required corrections (line-cited)

| # | Line | Issue | Severity |
|---|---|---|---|
| C1 | 89 | "Never block on vocals" for intro_hold will stutter pop intros. Require `stems_on: drums, no vocals` for both strict and waiver paths. | **Must fix** |
| C2 | 102-104 | tail_hold source may be hats-only / vox-only. Require `stems_on: drums` in the source phrase. | **Must fix** |
| C3 | 35-37, 109-115 | Waiver calibration is n=1. Add a third calibration point in 0.05-0.08 (e.g., a 22.09.26 window Sam used or rejected but at 0.06-0.07) before the 0.08 ceiling is set. Add a sunset/re-justify clause. | **Should fix** |
| C4 | 154-168 | Test plan has no held-out gate on the INTERIM_V1 flip. Require ≥ 75% truth-table hit rate on a held-out mix *before* the flip. | **Must fix** |
| C5 | 161-163 | No false-fire rate pass bar in the corpus test. Add ≤ 1% false-fire rate. | **Should fix** |
| C6 | 38, 87, 130 | T1 swap-move handling is implicit. State explicitly that combined hold+swap-move is out of scope for this PR. | **Should clarify** |
| C7 | 124 | Jewel Kid remains a known miss even with the waiver (fills block bars 0-2). Call this out in the test plan's expected truth-table hit rate (≤ 3/4 is the realistic ceiling). | **Should document** |
| C8 | 144 | `mix_plan` / paired_boundary / validator interaction is "not read here." This is load-bearing for whether the feature can ship. Read it before merging. | **Must verify** |
| C9 | 47, 144-152 | `apply_automation` on split clips is asserted, not verified. Add a unit test for the post-split clip's automation lane. | **Should verify** |
| C10 | 19, 22-24, 28-32 | "All four windows pass the gate" is supported by `ss` only for three of them. Re-run with all six metrics and re-state. | **Should document** |

The plan is salvageable and the design is right. The corrections above are about (a) plugging two real audio-quality holes (vocal-intro stutter, hats-only tail), (b) gating the rollout on evidence the plan doesn't currently require, and (c) being explicit about edges the plan currently waves at.

**Verdict: CORRECTIONS — do not merge as-is.** C1, C2, C4, C8 are the load-bearing ones; the rest are rigor.


===MINIMAX-ASK-DONE exit=0 session=pi nonce=a99b6e21b9b84c73bebc190bc6442348===
