# Fresh Mix Final V2 -> Sam's Tweaks

Date: 2026-07-16

Inputs:
- Generated baseline: `Test Project/16.07.26 Fresh Mix/Output/16.07.26 Fresh Mix Final V2.als`
- Expert correction: `Test Project/16.07.26 Fresh Mix/Output/16.07.26 Fresh Mix Final V2 Sam's Tweaks Project/16.07.26 Fresh Mix Final V2 Sam's Tweaks.als`
- Machine-readable diff: `Test Project/16.07.26 Fresh Mix/Output/Correction Analysis/SAM_TWEAKS_DIFF_V1.json`

## Integrity

- Eight tracks and the sequence are unchanged.
- All eight warp modes, marker counts and numeric `(SecTime, BeatTime)` grids are unchanged.
- Project tempo remains 121 BPM.
- Static LUFS fader values are unchanged apart from Ableton save-rounding at insignificant decimal places.
- The corrected ALS passes `validate_als.py`.
- Only the existing Utility Gain and Channel EQ bass envelopes are automated; Sam did not add a new effect-automation layer.

## Transition Diff

| T | Pair | V2 overlap | Sam overlap | Swap change | Main correction |
|---|---|---:|---:|---:|---|
| 1 | Falling -> Roadblock | 35 bars | 33 bars | 0 beats | Keep the successful swap; remove eight beats from Falling's exit and finish on its final dropout. |
| 2 | Roadblock -> Get The Message | 18 bars | 26 bars | 0 beats | Start the incoming track eight bars earlier with a four-bar intro phrase repeated twice at low level; keep bass handover unchanged. |
| 3 | Get The Message -> Same Thing | 26 bars | 73.82 bars | -64 beats | Add six four-bar incoming intro repeats, mute the incoming track across the outgoing eight-beat dropout, and hand bass over at Same Thing's source beat 96 drop. Extend the outgoing tail. |
| 4 | Same Thing -> Making Shapes | 58 bars | 37 bars | -128 beats | Remove the generated outgoing tail loop. Hand bass over at outgoing outro source beat 768 and incoming source beat 64 (`intro_2`), not at the incoming first drop. |
| 5 | Making Shapes -> Natural Child | 56 bars | 58.70 bars | -64 beats | Replace the generated eight-beat source 672-680 loop (7x) with a later 16-beat outro phrase at 704-720 (5x). Bring Natural Child in eight bars earlier and swap at source beat 32. |
| 6 | Natural Child -> Seein' You | 32 bars | 55.25 bars | -32 beats | Repeat Seein' You's first 32 source beats four times at low level. Preserve the outgoing source-748 cue and make the full handover at incoming source beat 32. |
| 7 | Seein' You -> Feel Your Touch | 34 bars | 42 bars | -64 beats | Expose a missed 16-beat dropout at Seein' You source 560-576, start the incoming intro at its endpoint, and swap at Feel Your Touch source beat 128/drop start. |

Baseline overlap mean/median: 37/34 bars. Sam overlap mean/median: 46.54/42 bars. Three corrected transitions exceed 48 bars, but T4 was shortened by 21 bars. This is cue-dependent, not a blanket preference for longer transitions.

## High-Confidence Lessons

1. **Entry, bass ownership and exit are separate musical decisions.** Sam frequently moved the incoming entry much earlier without moving the bass swap by the same amount, then chose a different outgoing exit. A transition cannot be represented by one alignment point plus a fixed overlap.

2. **Earlier low-level incoming loops are a primary technique.** T2, T3 and T6 add repeated intro phrases before the existing handover. Bass remains killed and the incoming level stays around 0.11-0.20 until the ownership point. The current arranger starts useful incoming material too late.

3. **A 48-bar ceiling cannot be a universal hard musical limit.** Corrected transitions include 55.25, 58.70 and 73.82 bars. Keep a short/default lane, but allow an evidence-backed extended lane when clean intro loops and paired exit cues exist.

4. **Protect important dropouts inside a transition.** In T3 Sam hard-mutes the incoming track from arrangement beats 1564-1572, exactly matching Get The Message's outgoing eight-beat dropout. Dropout landmarks are not only possible start/end markers; they can create protected silence windows inside a long blend.

5. **Do not turn every raw kick gap into a structural clip.** Sam consolidated Roadblock from 30 display fragments to five musical clips, but manually added the missed 16-beat Seein' You dropout inside an active drop. Raw landmarks, visual overlays and structural section splits need separate promotion rules. A gap inside an already kickless intro/break is usually evidence, not a new section; a phrase-level dropout interrupting an active drop is structural.

6. **Loop selection must search the musical phrase, not take the first available outro slice.** T4 deletes the generated loop entirely. T5 replaces an early eight-beat loop with a later 16-beat phrase. Candidate scoring needs groove continuity, phrase length, energy and distance to the desired exit cue.

7. **Source coordinates are the learning truth.** After manual splits/copies, corrected clip names become stale: Making Shapes clips named `drop_5` actually play source 704-720, which the baseline map identifies as `outro_1`. Learning from corrected names alone would teach the wrong section rule.

8. **The hard bass swap model is validated.** Sam retained binary Channel EQ values (0.18/1.0) and single-beat ownership changes. The main error was where the swap happened, not the bass automation shape.

## Candidate Architecture Changes

- Represent each transition with independent `entry`, `protected_windows`, `bass_swap`, and `exit` anchors, each mapped on both tracks' source clocks.
- Add an evidence-backed extended-transition mode up to at least 80 bars; do not make it the default.
- Generate incoming intro-loop candidates at 16- and 32-beat phrase lengths, with low-level/bass-killed playback until handover.
- Score outgoing loop candidates across the full late-track region rather than only the detected outro start.
- Preserve dropout landmarks as evidence; promote them to structural clips only when they interrupt an active section and have phrase-level prominence.
- Make correction ingestion source-aware and arrangement-aware before adding anything to `pair_history.jsonl`.

## Do Not Generalise Yet

- T3 contains a 12-beat outgoing loop repeated five times. It may be a deliberate three-bar phrase or an accidental edit; it is not evidence for a general 12-beat-loop rule.
- T4 contains a two-beat gap in Same Thing at arrangement 2370-2372 and a detailed outgoing volume ride. Confirm by ear before learning either.
- T3 and T5 finish at non-integer source/arrangement beats. These may be deliberate audio-tail edits rather than phrase anchors.
- Exact sneak levels vary. The supported rule is a low-level incoming layer with bass removed, not one fixed gain value.

## Deployment Status

These are candidate rules from one corrected eight-track mix. The source-clock facts are strong; creative preferences remain N=1 until replayed on a fresh mix or confirmed in the historical ALS corpus. Do not overwrite `interim_v1` or change production defaults solely from this file.
