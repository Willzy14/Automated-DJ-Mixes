VERDICT FORMAT: the FIRST line of your reply must be exactly one of:
VERDICT: SOUND
VERDICT: CORRECTION
VERDICT: DROP

CONSUMED-BY: Claude, deciding whether burn list item D12 (Automated DJ Mixes, Documentation/BURN_LIST.md) can be checked off. SOUND closes it; CORRECTION is applied first; DROP reverts.

This brief may be wrong. If a premise below is false, say so.

## What this is

`Source/learn_from_correction.py` diffs the pipeline's DJ-mix ALS against Sam's hand-corrected ALS and appends one entry per transition to `pair_history.jsonl`, the corpus a future arranger will score against. Until today it classified corrections from the volume and bass-EQ automation only. On the 15.09.26 August Releases Mix Sam changed all 11 transitions, mostly by moving clips (where the incoming enters, how much intro plays, loops added or removed, outros cut), and the learner labelled 5 of the 11, two of them wrongly.

Root cause of the wrong one: `_find_bass_swap_beat` searches in SOURCE-audio beats (a 2026-09-10 fix that makes a resized section clip comparable across files). On a LOOP clip that frame is meaningless: the T6 swap sat on a tail loop cut from Tommy Farrow's intro (source 0..32), so its source position fell outside the finder's window and the finder returned the track's last automation point. Result: "bass_swap_moved:-64beats" for a swap that had not moved.

## The change (uncommitted diff staged as review.diff; full files staged too)

1. A geometry layer added beside the automation diff, deliberately NOT touching the automation diff's own fields or the 2026-09-10 source-anchoring:
   - `_repeat_groups(clips)`: loop detection by shape. A group starts at a clip that is arrangement-contiguous with the one before, goes backwards in source, is no longer than it, and replays material already played; identical following clips are repeats; a shorter clip starting on the chunk's start is a trailing partial; a preceding identical clip is counted in so the notation matches ARRANGEMENT_REPORT ("8bx7+0b"). Hand-made loops (no `_tail_loop` name) are found the same way.
   - `_source_bar_at`: source bar at an arrangement beat, None + flag when on a loop clip.
   - `_find_swap_arr`: the swap's ARRANGEMENT beat (outgoing's first kill in the overlap, +-40 beats like `_scope_points`, else the incoming's first rise).
   - `_geometry_diff`: per transition, both files: entry bar on the outgoing, intro trim (first natural clip's source start), swap bar on the incoming and on the outgoing, tail bars after the swap, tail/intro loop groups, outro cut (natural outgoing material after the later entry that Sam's version no longer plays), overlap. Emits labels: entry_moved_out, intro_trim, swap_moved_in, swap_moved_out, swap_removed/swap_added, tail_loop_added/_removed/_changed (and intro_loop_*), outro_cut, overlap_changed. Thresholds: 1 bar for moves, 2 bars for cuts, 4 bars for overlap.
2. `TransitionDiff` gains `geometry` and `bass_swap_reliable`. The old `bass_swap_moved` label and the report's "swap moved" line are withheld when the swap sits on a loop clip on either side. `bass_swap_delta` itself is unchanged (a 2026-09-10 test pins it as the raw source difference).
3. pair_history entries gain `geometry` and `bass_swap_reliable`. Existing keys unchanged.
4. `Tests/test_learn_geometry_corrections.py`: 9 tests (loop detection: pipeline copies with natural first copy, hand-made last bars with partial, outro after copies is not a partial, forward skip and landmark split are not loops, intro-loop copies; transitions: T6, T4, T7, T9, T2 shapes from the real mix; an unchanged transition). All pass; the two existing learner test files still pass; full suite 800+ green.

Real-pair result: all 11 transitions labelled, matching a hand analysis of the two ALS files transition by transition (e.g. T4: intro_trim:16->0bars, entry_moved_out:+16bars, swap_removed, tail_loop_added:2bx3+1b; T9: entry_moved_out:+4bars, swap_moved_out:+14bars, outro_cut:10bars, tail_loop_removed:2bx8+0b).

## What to check

- `_repeat_groups`: is the "backwards in source + replays played material + no longer than the previous clip" rule sound? Can a natural section be mis-read as a loop (e.g. a short bridge that revisits earlier source), or a real loop be missed (e.g. a loop of material from LATER in the track, or copies that are not arrangement-contiguous)? The "replays played material" check uses [min, max] of preceding clips' source, not their union - is that a problem?
- `_find_swap_arr` with a +-40-beat margin: can it pick up the outgoing's OWN earlier bass-in ramp (from when it was the incoming of the previous transition) on a short outgoing, and is that any worse than the existing source-space finder's exposure?
- `_beats_missing` / `_natural_intervals`: is the outro_cut arithmetic right, including when Sam ADDS material the pipeline did not play (should not count) and when a cut spans the end of the track?
- The gating of `bass_swap_moved` on `bass_swap_reliable`: does withholding the label when either swap is on a loop clip lose anything real? (The geometry labels carry swap moves in each track's own bars.)
- Zero-length clips (Ableton leaves them; two exist in Sam's file) are dropped by `_positioned` - any hole there?
- Anything in the diff that changes an EXISTING pair_history field's meaning. The intent is additive only.

## Standing decisions (do not reopen without new evidence)

- The 2026-09-10 source-anchoring of the automation diff stays; this change is additive.
- pair_history.jsonl is append-only in normal use; the 11 entries this project appended earlier today were replaced (removed and re-appended) because they came from the pre-fix learner in the same session. That is a deliberate one-off, not a precedent.
- Peers review; they do not edit the repository. Return findings as text.

FOUND UNASKED: report anything else wrong you notice in these files, clearly separated from the verdict.

## Staged files

- review.diff - the change to Source/learn_from_correction.py plus the new test file
- learn_from_correction.py - full current file
- test_learn_geometry_corrections.py - the new tests
- test_learn_from_correction_geometry.py - the existing learner tests (unchanged, for the pinned behaviour)
