# Production Polish Backlog — "Mix → Wired Masters Production" Refinements

The transitions are strong (V17, 2026-06-09 — Sam, after listening overnight: "sounding really
good"). These are the SMALLER, finer details that take it from a good automated mix to a
**Wired-Masters-production-level** mix. All are **future fine-tuning** (captured 2026-06-10, NOT
for immediate build), and all live in the **automation** phase (`Source/apply_automation.py`).

---

## 1. Transition loudness compensation — no loudness bump at the overlap

**The problem.** Tracks are now LUFS-levelled down to the quietest (2026-06-09), but it's never
perfect between tracks. At a transition two tracks SUM — as you volume the incoming in, the
overlap section gets a touch LOUDER ("more stuff going on"). A finished mix should never bump UP
in loudness at a transition; the level should feel constant end to end.

**Sam's manual technique.** As the incoming comes in, slightly turn **down** one of the incoming
or outgoing tracks — on the order of **0.25–0.5 dB** — so the summed level isn't full-throttle.
Do the same gentle dip on the **low end**. Net effect: the mix stays really smooth, no audible
jumps at the seams.

**To build.** Extend the existing energy-compensation idea with concrete, subtle values: across
each overlap window, apply a small (~0.25–0.5 dB) level dip + a gentle low-shelf dip on one side,
restoring after the overlap. Validate with a LUFS-over-time check — there should be NO positive
loudness spikes at transition points.

---

## 2. Bass-switch energy preservation — boost the incoming bass at the swap, then fade it out

**The problem.** At the bass-swap, the OUTGOING track's bass may be LOUDER than the INCOMING's.
When you make the switch you can LOSE a lot of energy, because the incoming bass is quieter than
the outgoing one was.

**Sam's manual technique.** At the switch, BOOST the incoming track's low end so the energy stays
the **same** across the swap (no drop). Then SLOWLY FADE that bass boost back down as you move
into the track, so that by the time the full body is playing the bass is at its **natural,
un-boosted** level.

**To build.** This needs ANALYSIS, not a fixed value: measure the bass energy/level of BOTH
tracks around the swap (outgoing bass vs incoming bass), compute the boost the incoming needs to
MATCH the outgoing at the switch point, then apply it as a low-shelf EQ automation that starts at
that boost on the swap beat and ramps to **0 dB** over the following N bars (into the incoming's
first full section). Goal: constant low-end energy through the switch, settling to the track's
natural bass.

---

Both pair with the planned **audio-validation** feature (LUFS-jump + clipping + bass-energy
checks) — that's exactly how we'd verify the loudness stays smooth and the bass energy is held
through each switch.

_Source: Sam, 2026-06-10 (morning, after listening to In-Key Mix V17 overnight)._
