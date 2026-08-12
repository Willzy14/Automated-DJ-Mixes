# Held-out replay, result 01 — earlier/quieter incoming entry

Date: 2026-08-12. Project: `Test Project/12.08.26 Heldout Replay`.
Protocol and kill criteria: [Heldout Replay Plan V2](Heldout%20Replay%20Plan%20V2.md).

## What was tested

One variable. `sam_v1` brings the incoming in earlier as a quiet, bass-killed loop
of its own intro phrase; the bass handover does not move.

Held-out material: 8 deep/soulful-house tracks from Stephanes Playlist, verified by
normalised title against all 77 tracks used in the four prior mixes. Running order
forced to a BPM-ascending chain (118 -> 125) whose every adjacent pair was confirmed
alignable before building.

Only **1 of 7** transitions differed — T7, `It Is What It Is -> Got 2 Say`:

| | side A (`interim_v1`) | side B (`sam_v1`) |
|---|---|---|
| incoming enters | bar 976 | **bar 960** (16 bars earlier) |
| bass handover | bar 984 | bar 984 (identical) |
| outgoing ends | bar 1003 | bar 1003 (identical) |
| overlap | 27 bars | 43 bars |

Audio confirmed the design exactly: bit-identical until bar 960, differing through to
bar 984, bit-identical thereafter. Between bars 976-984 both play the intro but B sits
louder, its fade having had longer to travel.

## Protocol

Three clips, randomised (seed 778341), identical duration and byte size, with a second
copy of A as a noise twin. Sam listened without the mapping.

**The control passed** — he described two variants, not three, and did not claim a
difference between the two identical clips. He also correctly identified which clip was
the new version.

## Verdict

**Technique validated. Not promoted, and not shown superior.**

Sam: both versions work. So this is *not* a preference win, and is not recorded as one.
The claim that was actually supported is the narrower one: a long, slow, quietly-looped
faded-in entry is musically sound.

The pre-registered kill criteria (B wins >= 5 of 7, loses <= 1) **do not apply** — only
one transition differed, so this is a rig validation plus a directional read, exactly as
scoped. `interim_v1` remains the production default.

## The finding that matters

Sam's qualification is worth more than the verdict:

> it works in this one because the break that it goes over is quite a chilled out break.
> If that was a busy break, it probably wouldn't have worked.

So the entry extension is **conditional on the host section**. The quiet incoming layer
needs room in the outgoing; stacked against a dense section it becomes mush.
`_plan_incoming_entry_extension` currently selects any outgoing cue inside the loop
budget with no regard for what is playing there — that gate is missing, and the Demucs
stem envelopes already carry the per-stem density needed to measure it rather than guess.

His broader framing — "it's gonna be different to all tracks" — argues for measured
per-pair gates over blanket rules, which is also what the failed swap-selection
experiment showed earlier the same day.

## Next

1. Gate the entry extension on outgoing density across the overlaid region.
2. Re-run the replay once more transitions differ, so the 5-of-7 criteria become live.
3. Unblock the transitions that could not extend: partial-chunk placement (gap not
   divisible by 4/8 bars) and the evidence-gated extended lane (nearest cue beyond the
   loop budget).
