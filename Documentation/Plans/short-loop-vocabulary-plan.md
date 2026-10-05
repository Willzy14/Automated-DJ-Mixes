# Short-loop vocabulary: `intro_hold` + `tail_hold` (plan, 2026-10-05)

**Brain:** Claude (read-only design analyst). Nothing in `Source/` or `Tests/` was changed.
Inputs: both 05.10.26 ALS files (parsed with `analyze_correction_diff.load_snapshot`),
`Receipts/2026-10-05/sept_v4_vs_sam.json`, `Output/ARRANGEMENT_REPORT.json`, the mix's
`_Stem Analysis`, `v5-sam-tweaks-analysis.md`, and replays of `align_pair` + `plan_fill_or_cut`
with `_assess_loop_candidate` instrumented (scratch scripts only). The replay reproduces V4's report
exactly (same 3 abandonments, same 2 not-needed reasons, same BUTCH 4bx3 tail), so it is a valid harness.
Bars are 0-indexed source bars of the named track (source beat / 4). "IN" = incoming's bars.

**Evidence is thin.** The short-loop pattern is n=4 loops on one mix (Sam's own order, his
geometry). The 22.09.26 mix and Fresh Mix V2 back up "loop the incoming intro". Every rule
below is fitted in-sample until it has been run on a fresh corrected mix.

## 1. Top findings

1. **The gate did not reject Sam's windows. The planner never offered them.** Run through the
   unchanged strict gate, all four of Sam's source windows PASS: Youngr beats 6-10 (ss 0.91, tiera
   0.88, insert drop -11.3 dB incoming / -1.5 dB as tail), Sorley bars 176-180 (ss 0.96), BUTCH bars
   0-8 (ss 0.98). Integer-bar Youngr 1-2 and 1-3 also pass. What fails is **candidate generation**.
   The tail search (`pick_cue_bounded_drum_loop`) only looks at `loop_windows` (drums on, bass off)
   plus the first outro section. The intro search is switched off in production.
2. **The real reasons, verified by running the code:**
   - **T2 Youngr: the gate never ran (0 candidates measured).** `loop_windows` is empty. The outro
     is 1 bar (128-129) and sits under the vocal region 105-129, so the synthetic outro window is
     blocked. The D2 fallbacks need `o.loop_windows` or an outro of 2 bars or more. The report's
     reason "no loop-source passed the quality checks" is factually wrong for this pair.
   - **T4 Sorley: the only candidate was the 1-bar outro 183-184** (bass+vocals, no drums), checked
     twice. It failed `self_similarity`: base 0.42, tiera 0.53, threshold 0.65. `loop_windows` is
     empty because Sorley's drums never play without bass. Sam's window 176-180 (the last 4-bar
     phrase of drop_8) passes every check but was never a candidate.
   - **T5 RSquared: a genuine gate rejection, and the right outcome.** Outro windows 186-200 fail the
     tiera self-similarity term (0.53-0.63; the base term passes at 0.69-0.75). Several also sit on the
     `silence_fraction` limit (0.05). Intro windows fail `insert_level_match` (+6.7 to +7.4 dB) and
     silence. Sam added no loop here (he cut 8 bars), so the gate agreed with him.
3. **Two rules really do block Sam's moves, and the gate thresholds are not one of them.**
   (a) `max_loop_repeats = 8` (policy, plus `apply_loops.MAX_LOOP_REPEATS`) blocks 1-bar x15/x16.
   (b) `silence_fraction > 0.05` rejects sparse drums-only intros. On 22.09.26 Sam looped Jewel Kid
   bars 0-8 (silence 0.054), which the gate refuses. Youngr bar 2-3 fails at 0.05x the same way.
   This is the only gate rule that is too strict for short windows, on n=1 used window. `period`
   is not a problem: 1, 2, 4 and 8 bars are all allowed periods.

Side finding: Sam moved the **whole Youngr track by 2 beats** (every clip sits at source offset ≡ 2
mod 4; V4 sits at ≡ 0). Youngr's detected fills also straddle half bars (3.5-4.5, 18.5-20.5,
119.5-120.5). Most likely Youngr's downbeat is off by half a bar in our analysis. So the "+0.5" is a
grid-phase fix, not a loop parameter (question 1).

## 2. What Sam's short loops are (extracted from the ALS)

| T / side | Source window | Len x copies | Inserted | Swap relation | Level / EQ (Sam's automation) |
|---|---|---|---|---|---|
| T1 Scuba->Youngr, incoming `intro_hold` | Youngr intro beats 6-10 (bars 1.5-2.5; intro_1 is only 0-4) | 1 bar x15 extra + 0.5-bar join (17.5 bars of intro before src 8) | After the window: plays src 2-10, then 15 copies, then src 6-8, then continues at src 8 (split inside the intro) | Entry arr 336 = Scuba bar 84. Swap arr 384 falls inside the hold (Sam also moved the swap 14 bars earlier). Drop_1 lands at 414 | Vol flat 0.245 until 384, then 1.0. Bass 0.18 (killed) until 384, then 1.0 |
| T2 Youngr->HARTY, outgoing `tail_hold` | Youngr intro beats 6-10 again (own end bars 120-129 all under vocals) | 1 bar x15 + 0.5-bar join | After Youngr's last bar (src 516, outro bar kept), track end | Swap arr 912 = HARTY drop_1 (IN 16). Youngr's natural end is IN 16.5. Hold runs to IN 32 = HARTY break_1 | Vol 0.95@912 -> 0.35@914 -> 0@976 (fade across the hold). Bass killed from 914 |
| T4 Sorley->RSquared, outgoing `tail_hold` | Sorley bars 176-180 (drop_8, on the 4-bar grid from 164; bass in source) | 4 bars x1 | Straight after the window at bar 180. Rest of drop_8 plus the 1-bar outro follow | Sam moved swap +16 to Sorley 176 = RSq 16. Natural end IN 28, extended to IN 32 = swap+16 (RSq fill ends at 32) | Linear fade 1.0@swap -> 0@end. Bass killed from the swap (the loop is entirely post-swap) |
| T6 N.W.N->BUTCH, incoming `intro_hold` | BUTCH whole intro bars 0-8 (drums+other) | 8 bars x1 extra (intro plays twice) | Prepended (the same thing as playing the intro twice) | Entry N.W.N bar 176 (after its 175-176 fill). Swap unchanged at N.W.N 192 = BUTCH drop_1 | Vol 0.2 ramping to 1.0 at the swap. Bass 0.18 until the swap: exactly the existing incoming treatment |

What all four share: every hold is entirely pre-swap (incoming) or post-swap (outgoing), apart from
T1, where Sam also moved the swap. So the treatment Sam used is the one `apply_automation` already
writes. It keys on the ALS clip extents (`plan_transitions`: `ov_start = in_t.arr_start`,
`ov_end = out_t.arr_end`, bass kill `EQ_BASS_KILL = 0.18`). **No new automation is needed** as long
as holds respect the swap side. Shape: both incoming holds end with 16 to 20 bars of groove before
the drop (BUTCH 8+8, Youngr 4+15.5). Both tails end 16 bars after the swap on a phrase line (Youngr
IN 32, Sorley IN 32). This matches the "16 in, 16 out" finding in v5 §1.

## 3. How the pipeline loops today (decision points, `Source/align_engine.py`)

- `plan_fill_or_cut` `:2262` never moves `arr_offset_bars` / `swap_beats`. **Branch 1** (`:2328-2373`)
  is the last-drop incoming intro loop. It is gated by `CUE_CONFIG.incoming_intro_loop` (`:199`,
  default False). With the flag on it would give Youngr 2bx8 [safety-capped] (16b; Sam used 15.5)
  and BUTCH 4bx5 (20b; Sam used 8b). It still targets the outgoing's last drop, not a lead length,
  and v5 §4 R5 measured 3 false fires on 22.09.26. **Branch 1a** `_plan_incoming_entry_extension`
  (`:2163`) is SAM_V1 only. `entry_phrase_bars=(8,4)` cannot take a 1-2 bar phrase, so Youngr fails.
  Here it gets BUTCH exactly (8bx1, entry N.W.N 176) but also fires on T2/T4/T5, where Sam added nothing.
- **Branch 3** (`:2416-2648`) outgoing tail: R1 skip (`:2423-2437`), R3 reach cap (`:2470`), D15
  section-then-landmark tiers (`:2479`), `pick_cue_bounded_drum_loop` (`:2030`; lengths
  `(8,4,7,6,5,3,2,1)` `:2056`, sources = `loop_windows` + first outro `:2074-2087`). Then the D2
  fallback (`:2569-2594`: latest loop_window, or an outro of 2 bars or more). Abandonment is
  recorded at `:2657-2661` with one fixed reason string, whether or not anything was measured.
- Gate: `evaluate_loop_quality` `:502`, thresholds `:47-55`, AND-semantics tiera `:92`. The apply
  time re-gate is `apply_loops._revalidate_loop_quality` `:690`, with its own repeat cap
  `apply_loops.py:43,271`.
- Consumers: `propose_arrangement._plan_marker_loops` `:903`. `outgoing_tail` is inserted before
  the outro (`:940`), or after the last clip when there is no outro (decisions path only, `:943-948`).
  `incoming_intro` is prepended before the intro (`:1051-1080`). Report fields `:2270`, `:2299-2311`.

## 4. Proposed vocabulary (smallest set that reproduces the four moves)

Two new FillCutSpec kinds, planned in a new **branch 5** at the end of `plan_fill_or_cut` (after
`:2661`, before break-skip). Each fires only when its trigger holds **and** the existing paths
produced nothing for that side. The strict path is never touched.

### 4.1 `intro_hold` (incoming)
- **Trigger (all):** `policy.intro_hold`. The incoming intro is shorter than
  `intro_hold_max_intro_bars + 1` (default: intro < 16 bars). No `incoming_intro` spec exists yet
  (so it is mutually exclusive with branches 1/1a by construction). A window in the intro passes
  the gate.
- **Window search:** inside `[intro_start, intro_end)`, try the whole intro if it is 4 or 8 bars,
  then 2 bars, then 1 bar. Earliest start first. Never block on vocals. Fills block as today (see risk R4).
- **Length:** extend the intro (groove before the drop) to `intro_hold_lead_bars` (16):
  `extra = 16 - intro_length`, rounded up to whole window copies, within `short_hold_max_bars`
  (16) and the existing `loop_budget`. This is intro length, not pre-swap length: Youngr swaps at
  its bar 20, so a pre-swap rule would never fire on T1.
  BUTCH: 8 -> 16 = one 8-bar copy (exact). Youngr: 12 bars as 2bx6 or 1bx12 (Sam: 15.5 bars).
- **Insert:** if the window starts at `intro_start`, prepend (the existing consumer). Otherwise split
  the intro at `window_end`, move the head earlier by `extra`, and put the copies between head and
  rest (Sam's Youngr shape). Prepending a mid-intro window would replay the gate-failing bar-0
  pickup after the loop.
- **Must end at or before the swap.** Guaranteed, because the swap clips do not move.

### 4.2 `tail_hold` (outgoing)
- **Trigger (all):** `policy.tail_hold`. The outgoing has **no usable outro**: no outro section, or
  outro shorter than 4 bars (`tail_hold_max_outro_bars=3`), or the outro is fully vocal/fill blocked.
  Post-swap outgoing content `o.n_bars - al.handoff_bar_out < tail_hold_post_swap_bars` (16).
  Branch 3 produced no `outgoing_tail`. R1 did **not** report "natural end on section line".
  This last clause stops Scuba T1, where outro=2 and post=2 but E sits on Youngr drop_2.
- **Target (IN bars):** the first incoming section start T with `T >= max(E, swap_in + 16)`. If it
  lies more than 4 bars past that, use the 4-bar phrase line from the containing section's start
  instead. Reach `T - E <= short_hold_max_bars` (16). Youngr in pipeline geometry: break_1 32
  (+3 bars). In Sam's geometry: 32 (+15.5, exact). Sorley in Sam's geometry: RSq 32 (+4, exact).
- **Source, in order:** (1) the outgoing's last whole 4-bar phrase inside its final pre-outro section,
  on that section's 4-bar grid, not vocal/fill blocked. **Bass allowed**, because the hold is post-swap
  and gets the kill. Then 2 bars, then 1 bar of the same phrase. (2) The outgoing's own intro drums
  (same search as 4.1). Sorley -> 176-180. Youngr -> intro (own end vocal-blocked).
- **Insert:** source (1) goes straight after the window (split the clip at `window_end`, shift
  later clips by `ext`; Sorley). Source (2) goes after the last clip (Youngr). Insert-level is
  measured at the real join (the source bar before the splice), never at a decayed final bar: at
  src 516 Youngr's join reads -44 dB and would pass anything.
- **Must start at or after the swap**, or the planner declines with reason `would_precede_swap`.

### 4.3 Gate for short holds (strict by default, one scoped relaxation)
- Use the same `evaluate_loop_quality`, unchanged. All four of Sam's windows pass it as it is.
- Caps: `short_hold_max_repeats=16`, `short_hold_max_bars=16`. These apply only to the two new kinds.
  `max_loop_repeats=8` stays for everything else. Both caps travel on the LoopSpec so
  `apply_loops.validate_loop_spec` (`:271`) checks the right cap.
- Optional relaxation `short_hold_max_silence_fraction` (default None = strict; candidate 0.08).
  It applies only to `intro_hold` windows whose intro `stems_on` contains drums and no vocals, and
  only if `worst_beat_dip` and both self-similarity terms pass. It goes into
  `LoopQualityResult.waived_checks` as `silence_fraction`. The report labels it `gate: relaxed` so
  it can never be mistaken for the strict path. 0.08 sits between Jewel Kid 0.054 (Sam used it)
  and All Is Fine 0.103 / Sorley 0.21+ (Sam declined both). That is one point on each side.
  The LoopSpec carries the waiver so the apply-time re-gate (`:690`) applies the same rule.
- Not relaxed: `period`, `insert_level_match`, `self_similarity` (tiera AND kept; it correctly
  rejected T5). `worst_beat_dip` is also kept.

### 4.4 Report fields (new; existing D2b/R1 semantics untouched)
- `Alignment.short_hold`: `{kind, trigger, source: intro|own_last_phrase|own_intro, window_bars,
  reps, partial_bars, target, gate: strict|relaxed:silence_fraction}`.
- `Alignment.short_hold_declined`: `{kind, reason}`, where reason is one of `no_window`,
  `reach_exceeds_cap`, `would_precede_swap`, `budget`. Triggered-but-not-built must be visible.
- Separate small fix, same PR series: keep `outgoing_loop_abandoned` but add
  `candidates_assessed: N`, and use reason `no loop-source candidate existed` when N=0 (T2).
  No test asserts the old string (grep). It is only copied at `propose_arrangement.py:2270`.
- The ARRANGEMENT_REPORT `loops[]` entries get `"type": "intro_hold" | "tail_hold"`.

### 4.5 Policy fields (`transition_policy.py:44-105`)
`intro_hold: bool = False`, `intro_hold_max_intro_bars: int = 15`, `intro_hold_lead_bars: int = 16`,
`tail_hold: bool = False`, `tail_hold_max_outro_bars: int = 3`, `tail_hold_post_swap_bars: int = 16`,
`short_hold_lengths_bars: tuple = (8, 4, 2, 1)`, `short_hold_max_repeats: int = 16`,
`short_hold_max_bars: int = 16`, `short_hold_max_silence_fraction: float | None = None`.
**Recommendation:** OFF in INTERIM_V1. Add `SHORT_HOLD_V1 = replace(INTERIM_V1, name=...,
intro_hold=True, tail_hold=True)` as the A/B lane, relaxation still None. Flip INTERIM_V1 only after
the corpus read (§6) and one fresh corrected mix judged by Sam. Turn on the 0.08 relaxation as a
separate step with its own replay diff.

### 4.6 Consumer changes
- `propose_arrangement._plan_marker_loops` (`:903`): two new branches. `intro_hold` reuses the
  `incoming_intro` prepend (`:1051-1080`, including the `arr_start` update and the named_landmark_64
  lane). The mid-intro window case adds a split-head-and-shift. `tail_hold` reuses the
  `outgoing_tail` LoopSpec but with `insert_at` = the arrangement position of `window_end`
  (own phrase) or the last clip end (own intro). It never uses the outro start. Later sections shift
  by `ext` as now (`:949-970`). Assert the swap-side rule (4.1/4.2) here as well. The automation
  then gives low level + bass kill (incoming) and post-swap bass kill + fade to the new end
  (outgoing) with no change.
- `apply_loops`: a `split_named_clip_at(source_beat)` helper (there is a sibling at `:995`), then
  `shift_named_clip` (`:448`) / `shift_clips_from_beat` (`:963`). The LoopSpec gains `max_repeats`
  and `waived_checks`. `validate_loop_spec` and `_revalidate_loop_quality` read them.
- Check `mix_plan` / reconciliation for loop-count or extension caps before building. Not read here.

## 5. Predicted vs Sam on this mix (pipeline geometry, SHORT_HOLD_V1 rules; hand-computed, not yet run)

| T | Sam | Predicted | Match |
|---|---|---|---|
| 1 in | Youngr intro 1b (beats 6-10) x15.5, entry Scuba 84 | intro_hold Youngr bars 1-3 (2b) x6 = 12b, entry Scuba 78, overlap 22->34 | loop + source yes; length close; entry no (Sam also moved the swap) |
| 2 out | Youngr intro 1b x15.5 to HARTY 32 | tail_hold Youngr intro 1b x3 to HARTY break_1 32, after track end | source + target yes; length differs only because Sam moved the swap |
| 3 | none | none (Sorley intro silence 0.21-0.32; HARTY outro 18) | yes |
| 4 out | Sorley 176-180 x1 (after swap +16) | none (post-swap already 24 bars) | no in pipeline geometry; exact on Sam's swap (verify via decisions replay) |
| 5 | none | none (RSq outro 14 bars) | yes |
| 6 in | BUTCH 0-8 x1, entry N.W.N 176 | intro_hold BUTCH 0-8 x1, entry 176, overlap 32 | exact |
| 7 | removed pipeline's BUTCH tail | unchanged: branch 3 still loops 4bx3 (BUTCH has a 12-bar outro) | out of scope (Sam's "remove wrong loop" pattern) |

Cross-mix check of the intro trigger (computed): 22.09.26 fires on T2 Detlef (intro 8, 0-8 passes).
Sam entered 8 bars earlier there but played drop_1 instead of looping, so this is a false fire for the
loop mechanism. It misses T4 Jewel Kid (silence 0.054, and fills at 0.25-1.75 block bars 0-2) and
T6 Freejak (16-bar intro; the v5 R5b dropout rule covers it). 15.09.26 August: never fires (no intro
under 16 with a passing window). Sam added no incoming loops there, so that agrees.

## 6. Test plan
1. **Unit** (`Tests/test_short_hold_vocabulary.py`, synthetic tracks plus the 05.10.26 stems as a
   fixture): triggers fire and decline per 4.1/4.2. INTERIM_V1 output is byte-identical with the
   fields off. Whole-intro preference (BUTCH -> 8bx1). R1-not-needed suppresses tail_hold (Scuba
   T1). An existing `outgoing_tail` suppresses tail_hold (BUTCH T7). A 1bx16 hold is allowed and
   1bx17 is rejected. Strict kinds stay capped at 8. The waiver only applies to drums-only
   intro_hold and shows in `waived_checks`. Swap-side asserts. The 05.10.26 abandonment reasons
   read: T2 `candidates_assessed=0`, T4 self_similarity, T5 self_similarity(tiera)+silence.
   apply_loops tests cover split+shift, the per-spec repeat cap, and the re-gate honouring the waiver.
   `Tests/test_alignment_baseline.py` must not change (alignment untouched).
2. **Corpus** (`Tools/short_hold_replay.py`, cloned from `d15_outro_loop_replay.py`): all 380 ordered
   pairs of 14.08.26, plus adjacent mix-order pairs of every `_Stem Analysis` project with a report.
   Record `short_hold`, `short_hold_declined` and the existing tail fields. Pass bar: 0 new raises,
   the 26 `align_raise` / 1 `raise` rows unchanged, existing `outgoing_tail` rows unchanged. Read
   every row where a hold appears (D9/D15 discipline). Report fire rate per kind and per source.
   One flag per commit: tail_hold, then intro_hold, then the waiver.
3. **Re-score** against a frozen truth table: Sam's 4 loops, plus Sept T3/T5/T7 and 22.09 T2/T4/T6/T8
   incomings as negatives or misses. Score loop Y/N per side, source class, window (±1 bar), copies,
   and entry/target bar (±1). Also replay T4/T1/T2 on Sam's own swap via
   `alignment_from_decision` (`:2725`) so the vocabulary is judged separately from the swap moves.
   Then build one fresh mix on SHORT_HOLD_V1 for Sam's ears.

## 7. Risks
- **R1 Over-firing on intro < 16** (Detlef-type). Mitigation: lane-only. Watch the corpus fire rate.
  If it is high, add v5's R5a (pre-swap < 16) as a second trigger condition.
- **R2 D15/D1 regression:** none by construction. tail_hold runs only after branch 3 produced
  nothing, and never competes with section/landmark tiers. **D2/D2b:** keep
  `outgoing_loop_abandoned` exactly as the "wanted but failed" record, even when a tail_hold then
  fills the gap. Report both, because merging them is the ambiguity D2b removed.
- **R3 Gate softening creep:** the waiver is one check, one kind, one ceiling, default None, and
  stays labelled. Any proposal to widen it to tails or to `self_similarity` repeats the 2026-08-25
  blended-score un-catch class (404 bad loops passed).
- **R4 Fill blocking** is why Jewel Kid would still be missed (tiny 0.25-beat intro "fills"). Do not
  relax it in this pass. Measure how often fills under 1 beat block intro windows first.
- **R5 Grid phase:** Youngr's half-bar offset means a predicted integer-bar window may sit 2 beats
  off what Sam heard. Fix the downbeat (analysis) rather than teaching the planner half-bar windows.
- **R6 Bass in tail sources:** safe only because the hold is post-swap. The swap-side assert is load-bearing.

## 8. Questions only Sam can answer
1. Youngr: you shifted the whole track by 2 beats. Is our Youngr downbeat half a bar out (fix the
   analysis), or did you offset it on purpose?
2. Incoming holds: is the rule "a short intro (<16 bars) gets looped up to about 16 bars of groove
   before the drop"? Detlef (8-bar intro) you entered early but did not loop. What made that different?
3. Tail holds: when a track has almost no outro, is "hold it to 16 bars after the swap, on a phrase
   line" right? And is looping its intro drums at the end fine when its own ending has vocals?
4. Loop size: 1-bar x15 on Youngr vs 8-bar on BUTCH. Do you pick the longest clean phrase, or is
   1 bar deliberate for pop edits?
