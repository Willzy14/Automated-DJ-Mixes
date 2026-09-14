# Held-out replay, result 02 — three-way A/B/C, neither promotes

Date: 2026-09-14. Project: `Test Project/10.09.26 Tech House Heldout`.
Protocol and kill criteria: [Heldout Replay Plan V2](Heldout%20Replay%20Plan%20V2.md).
Sealed test: `Output/AB/Blind Test/` (whole-mix pair) and
`Output/AB/Blind Test/Transitions/T01`-`T08` (per-transition, this result's real
evidence).

## What was tested

Three sides built from the same 9 held-out tech house tracks, same running order, same
sections/tracks — the only variable is the transition policy:

- **A** = `interim_v1` (production baseline)
- **B** = `sam_v1`
- **C** = `sam_v1` + the `incoming_intro_loop` cue signal ("introloop")

Running order: Yellody feat Lovelee -> Freejak -> HARTY -> Jay de Lys -> Sam Leagas ->
Sapian, Aviv Sab -> Marco Lys, Michael Ekow -> Enzo Is Burning -> Jewel Kid (8
transitions, T1-T8). Unlike Result 01's single clean geometric diff, all three sides'
own overlap geometry genuinely differs per transition here (confirmed directly against
each side's own `Arranged {side}_ARRANGEMENT_REPORT.json` while building the sealed
excerpts - e.g. T1's C-side overlap zone measured 147.69s against 103.38s for A/B), so
this is a real, not null, three-way comparison on every transition.

## Protocol

Whole-mix pair sealed with `seal_listening_test.py` (seed 20260914), per-transition
excerpts sealed with `extract_transition_excerpts.py` (base seed 20260914, transition N
sealed with seed `20260914 + N`) - 4 clips per transition (A, B, C, and an A-twin noise
control), duration-equalised so no clip's length discloses its side, all three sides
bound to their real ALS/report/WAV via `record_bounce_manifest.py`'s sha256 manifests
(`--require-bounce-manifests` set - every side strongly bound, not the weaker
duration-only fallback). Sam listened without the mapping, one transition folder at a
time.

**The control passed on every single transition** - confirmed directly: "all the twins
matched." T6 is the sharpest proof of this: Sam correctly identified the twin *while
listening*, and still preferred it over the other two clips - a deliberate, informed
call, not a listener losing the thread. The test was discriminating throughout.

## Per-transition verdict

C vs A and B vs A tracked separately - they're different policies, not one:

| T | Pair | Sam said | C vs A | B vs A |
|---|---|---|---|---|
| T1 | Yellody -> Freejak | clip1(B) messy; clip3(C)/clip4(A) great, C wins | **WIN** | **LOSS** |
| T2 | Freejak -> HARTY | clip2(B)=clip3(C), tied, best; twin (1,4=A) separately confirmed | **WIN** | **WIN** |
| T3 | HARTY -> Jay de Lys | all identical; shared pre-drop glitch on every clip | tie | tie |
| T4 | Jay de Lys -> Sam Leagas | clip4(C) best | **WIN** | not stated |
| T5 | Sam Leagas -> Sapian | clip1(C) best | **WIN** | not stated |
| T6 | Sapian -> Marco Lys | clip1(A-twin) best - *knew it was the twin, still won* | **LOSS** | **LOSS** |
| T7 | Marco Lys -> Enzo Is Burning | all identical; blend overruns the break | tie | tie |
| T8 | Enzo Is Burning -> Jewel Kid | clip1(B)/2(A-twin)/3(A) same; clip4(C) stands apart, not praised as better | no clear win | tie |

**C: 4 wins, 1 loss, 3 ties.** **B: 1 win, 2 losses, 3 ties (2 transitions not
independently assessable from the notes).**

## Verdict

Against the pre-registered kill criteria (win >= 5 of 7, lose <= 1 - this set has 8
transitions, not 7; treated as >= 6 of 8 rather than an exact match since Plan V2's
numbers were calibrated on a different held-out set):

- **`sam_v1` (B) hits an explicit kill condition** - 2 losses exceeds the "loses more
  than 1" limit outright, independent of the win count. **Park or revise.**
- **`sam_v1`+introloop (C) does not trigger the hard kill** (1 loss, exactly at the
  boundary) **but falls well short of the win bar** (4/8, not ~6/8). Same outcome class
  as Result 01: **validated, not promoted, not shown superior.**

`interim_v1` remains the production default. Even a clean pass this round would not
have been enough on its own - Plan V2's REGIME clause requires surviving a *second*
held-out mix with different structural patterns before any promotion.

## The finding that matters

> i knew it was the twin, it was still the best mix

T6 is the whole round's sharpest result precisely because the control ruled out the
obvious objection. This was not "Sam couldn't tell it was a repeat" - he could, and
`interim_v1` still won on its own musical merits against both `sam_v1` variants. That
is real, informative signal that `sam_v1`'s transition selection has at least one
failure mode on this held-out set, not just a null result.

Two things worth chasing that are **not** policy differences - present identically on
every clip, every side:

- **T3**: "a slight mistake... just before the drop... nothing that can be seen from a
  single bounce like this." Sam's own caveat is correct - this needs isolated stems or
  closer analysis outside the blind protocol, not another A/B round.
- **T7**: "the mix works, although the mix lasts over the break and it doesn't need
  to." An overlap-length tuning opportunity independent of which policy builds it.

## Not a verdict on swap PLACEMENT itself

The `sam_v1` this round tested is missing two fixes burn list section C already has
queued: it never consults `pair_history.jsonl` (16+ real Sam corrections sitting
unread - C1), and its automation style is still chosen purely by overlap length, not
by what's actually playing underneath (C2 - the exact Freejak->HARTY-shaped bug Sam
corrected by hand recently). T1's "messy" B clip and T6's outright loss are both
plausibly explained by one or both of those gaps, not by the swap-placement idea
itself being wrong. "Park or revise" for `sam_v1` should read as "revise, specifically
by wiring in C1/C2 first," not as the underlying approach being disproven.

## Next

1. **Don't re-run this held-out set on `sam_v1` alone hoping for a different read** -
   the kill fired on losses, not a marginal win count; the failure mode needs
   understanding before another blind round is worth the seed. T6 specifically: what
   does `sam_v1` do differently there that `interim_v1`'s simpler choice avoided?
2. Wire in C1 (`pair_history.jsonl` actually informing a choice) and C2 (content-aware
   automation style) before re-testing `sam_v1` - the most likely fix for what this
   round actually found, not a fresh redesign.
3. `sam_v1`+introloop's 4 real wins are worth keeping as a live thread, but the bar
   wasn't cleared - if revisited, it still needs the REGIME's second held-out mix
   before any promotion conversation, per the pre-registered protocol.
4. T3's shared pre-drop glitch: pull isolated stems for HARTY / Jay de Lys and look
   at what's actually happening there - independent of this comparison entirely.
5. T7's overlap: worth a look at whether the overlap-length policy can be tightened to
   not run past the break, on `interim_v1` as it stands today (this is a live-default
   observation, not a `sam_v1` one).
