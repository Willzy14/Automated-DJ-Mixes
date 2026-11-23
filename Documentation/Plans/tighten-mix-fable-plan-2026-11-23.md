# Fable plan: take the blend planner past "almost good enough" (2026-11-23)

Planner: Fable (Plan agent), read-only, from `tighten-mix-fable-brief-2026-11-23.md`. Reviewed by Claude Sonnet 5.5: two code claims re-checked (the 15 ms gate `STEM_KF_FAIL_MS` is at `Source/validate_beatgrid.py:279`; `check_kick_flam` exists in `Source/render_check.py` and is disabled). The rest is Fable's reading and is NOT yet verified by a second brain. Nothing here is built.

## Most important line

The flam is most likely one track, not two: No Panties. Its kick has a slow attack, so its grid line sits about 9 ms ahead of where the kick actually lands, and No Panties is in both transitions Sam flagged.

Limit: the planner's own output for this set (decisions, costs, alternatives) is not on STUDIO-2, so every "why did the planner pick that" claim is not verified.

## 1. The flam

What was checked: Fable recomputed the brief's table from the kick-offset JSONs and it reproduces (T1 -7.61 ms, T2 -0.42, T3 -0.19). The second estimator (steepest rise of the click) gives T1 -15.7 ms and T2 +5.7 ms, and every other transition with enough shared kicks is under 2.3 ms, so that estimator singles out the two transitions Sam named. No Panties reads +1.1 ms by leading edge and +10.4 ms by steepest rise: a small early click, then the main hit builds to about +10 ms. T1 is worse because Get Right's end sits about 5 ms early, the opposite way.

Why the pipeline missed it: the grid defines a kick as the foot of the click (10 percent of peak, above 1500 Hz, drums stem; `refine_to_click` in `Audio Analysis Toolkit/src/audio_analysis/stem_grid.py`). The 15 ms gate judges each track alone, has no sign, and compares the grid to the same kicks it was fitted to. Nothing compares two tracks in an overlap. Tightening the 15 ms gate would not help: No Panties passes at about 1 ms by that definition. `check_kick_flam` ships disabled (false positives proven); `grid_fold` (30 ms) covers solo regions only.

Correction to our brief: the sub-body idea for T2 is weak. In the planned ALS the incoming low shelf sits at 0.18 until the swap, then the outgoing drops to 0.18, so two sub bodies never play at full level together.

Test first: cross-correlate the two tracks' kick envelopes (above 150 Hz) inside each overlap, lags plus or minus 30 ms, no onset picking. Expected: T1 near 8 to 16 ms, T2 near 6 to 9 ms, T3 near 0. Then make one copy of the set with No Panties slid by the measured lag and let Sam hear T1, then T2.

What would change the plan: a lag under 3 ms in T1 and T2, or Sam hearing no difference in the copy. Next suspects: Re-Pitch warp mode (six of nine tracks use mode 3; `warping.py` says it was never round-tripped through Live) and whole-millisecond markers (beats alternate 468 and 469 ms).

Fix layer: add a PAIR check on the relative lag in each overlap (gate). Fix the track by a per-track phase slide through `Hints/grid_overrides.json` (`shift_ms`, `apply_grid_override`); NOT verified that `shift_ms` is applied on the stem-grid path (check `orchestrator.py` lines 320 to 510 first). Leave the toolkit's kick definition alone (shared, and this is one track).

## 2. Does the planner plan the best mix?

H3 was wrong as written: `candidates()` in `Source/phrase_blend.py` already builds every pairing, scores each with named cost terms, ranks them and returns up to 6 alternatives. The real question is whether the hand-set weights rank like Sam. The file itself says "Nothing here is validated".

Plan:
1. Replay Sam's tweak: for each of the 8 transitions put his placement through `candidates()` and report: same as top, in the alternatives, reachable but ranked N (with the term that cost it), or outside the vocabulary. `Tools/score_blends.py` already does this for the seven answer-key blends.
2. Score candidates on measured things only: kick lag in ms; bars with no kick or bass from either side; bars where both sides carry music; loudness step at entry, swap and exit; how many of the three events land on marked cue points.
3. Always print a trivial baseline beside the planner: "16 bars in, 16 out, swap on the outgoing outro start" (`Documentation/Plans/v5-sam-tweaks-analysis.md` found that shape in Sam's earlier tweak).
4. Teaching Mixes: frequency checks only, read from the cards (how often an intro is trimmed, how often the outgoing ends on the incoming's section start). No fitting: they are 2015 mixes with other techniques and `INDEX.md` says they are not for the pipeline.
5. Where top and alternative differ on a close score, Sam hears both, one transition at a time, not blind.

Good enough looks like: Sam's placement is top or a listed alternative in 6 of 8 and the rest are named vocabulary gaps; on the next fresh mix Sam changes 2 or fewer blends; the planner beats the trivial baseline (if not, say so). Today's honest count: 3 of 8 untouched, and Sam had only heard those three properly.

## 3. Five changes, ranked

1. Pair kick-lag tool (report only). New `Tools/measure_pair_kick_lag.py`, promoted from `measure_kicks2.py`. Owner: Claude subagent. Review: MiniMax plus lead. Test: two synthetic kick tracks 8 ms apart read 8 plus or minus 1; a soft-attack fixture shows both estimators and flags that they disagree. Outside check: reproduces the T1 and T3 figures.
2. Slide No Panties, re-lay, bounce T1 and T2 (`Hints/grid_overrides.json`). Owner: lead. Review: Claude subagent. Test: tool 1 fails T1 before and passes all judged overlaps after. Outside check: Sam's ear.
3. Pair gate in the pipeline (`Tools/run_mix_pipeline.sh` plus tool 1). Warn only for two mixes, then fail. Start at 4 ms; Sam sets the real figure. Owner: MiniMax. Review: Claude, then Codex after 2026-11-27. Test: fewer than about 16 shared kick beats must say "cannot judge", never pass (POP to Lift Off has 2, Lift Off to Interlude has 3).
4. Tweak replay tool: new `Tools/score_tweak.py` modelled on `Tools/score_blends.py`. Owner: Claude subagent. Review: MiniMax. Test: a fixture where the owner's answer is rank 3 prints rank 3 and the cost gap; an unreachable answer prints "outside vocabulary". Needs the Home PC's `_Stem Analysis` folder.
5. Planner rule changes, one at a time, only after 4 (`Source/phrase_blend.py`, `Tests/test_phrase_blend_round8.py`). Owner: Codex after 2026-11-27. Review: MiniMax plus Claude. Test: all seven answer-key blends still hit and the named tweak transition now matches. Outside check: Sam hears each one.

## 4. Sam's tweak as data

Look like rules:
- No intro trims: Sam restored the 1-bar trim on 007 (T4) and the 4-bar trim on Lift Off (T6). Memory note `claude-arranged-mode-is-wanted.md` records "no intro trims" from an earlier mix too. In the planner an intro cut is free; it should cost, and a 1-bar cut should not be allowed.
- Swap onto the incoming's first drop, not its break (T8): planner used Killa's break start (source beat 64) against Interlude's last drop start, 32 bars; Sam used Killa's drop start (beat 32) on the same point with Interlude ending on Killa's break, 16 bars (8 in, 8 out). `in_point_kind` prices any section start at 1.5, drop or break; the length clock likely outvoted the drop (not verified).
- Loop the tail to the incoming's next section (T5): Sam looped 007's last 8 bars once so it ends on POP's break start. That is already rule R4 (round 8); the planner did not do it, so treat it as a bug to find, not a new rule.

Need Sam's ear (one mix is not enough): T7 same swap but entry 8 bars earlier on Lift Off's drop_3 start by looping Interlude's 8-bar intro (24 in, 16 out); T4 TCFS outro beats 520 to 552 reused instead of its last bars.

One-off taste, do not learn: T6 POP stops dead on Lift Off's drop with no tail (`MIN_SIDE_BARS` forbids it); Killa break_2 cut by 16 bars (other long breaks, TCFS 32 bars, were left alone).

Correction to our brief: POP's "drop lengthened, outro cut" is only clip naming. The source runs straight through; only POP's last 4 bars were dropped.

## 5. Risks and what not to build

- T2 rests on one estimator. Sam may number transitions differently. Question for Sam: is it the blends into and out of No Panties?
- The measurement is on full mixes; hats can bias a click detector. The cross-correlation is the guard.
- Do not build a learned scorer from Teaching Mixes or the 63 lines of `pair_history.jsonl` (C7 lost to the baseline).
- Do not switch `check_kick_flam` back on (false positives proven).
- Do not change the toolkit's kick definition yet.
- Do not re-warp everything to two markers because of Lift Off (the click moved under 1 ms); ask Sam what he heard there.
- Do not build another blind test rig.
- Leave alone: the max-abs column, Get Right's drift (the pair measure covers it), and Complex Pro on TCFS, 007 and POP until Sam has heard those blends.
