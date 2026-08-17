# Plan v1 — Cue hierarchy and arrangement-first selection

**Slug:** `arranger-signal-rewiring` · **v1, 2026-08-17** · Architect: Claude (Opus 5)
**State:** SYNTHESIZED — not yet attacked, not authorized, do not build from this yet.
**Supersedes:** the two Diverge sketches (Fable's two-tier merge, MiniMax's per-class caps). Both
solved a real but *secondary* problem — stopping dense markers dominating the score. Sam's model
changes what the engine is trying to **do**, which comes first. Their good parts survive underneath.

---

## 1. What the engine should be doing (Sam's model, plainly)

> *"Lots of things can happen at a cue point. If you get several things converging on one point,
> that's a bigger signal that that's a cue point. And then you probably need to put arrangement
> above that."*

Three claims, each a change from today:

1. **A cue point is a place, not a signal.** Several different markers landing on the same bar make
   that place *more* certain. Today the engine records the labels and scores the bar identically to
   one with a single marker — **within-track cue confidence does not exist in the model at all.**
2. **Arrangement outranks cue strength.** Work out how the two tracks fit together, *then* use cue
   strength to choose between the fits. Today the order is inverted: marker agreement is scored
   first and arrangement fit is a tie-breaker.
3. **The search region follows the music.** Look between the last long break and the end of the
   track — not a fixed distance from the end.

And the mixing rule underneath it all:

> *"If there is a bass switch, that's a perfect time to do the bass crossover. The bass switch is a
> basic mixing skill."*

---

## 2. THE CUE HIERARCHY TREE

What makes a place a good handover point, strongest first. **Tier is not the same as weight** — tier
says *what kind of claim a marker makes*; convergence across tiers is what makes a place strong.

```
CUE POINT HIERARCHY  (outgoing track, tail region)
│
├─ TIER 1 — BASS OWNERSHIP CHANGES        "the swap can genuinely happen here"
│   ├─ bass_out inside the outro region   ← SAM'S PERFECT SWAP. 13/20 tracks
│   │     (measured: lands exactly on the drop→outro boundary on 13/20)
│   └─ bass_out standalone, off any section edge   ← 1/20 (Switch Disco, bar 148)
│         the only case today where wiring the bass changes a decision on its own
│
├─ TIER 2 — STRUCTURAL BOUNDARIES         "the arrangement changes here"
│   ├─ section drop→outro boundary        (already wired; 64% of all handovers today)
│   ├─ section break:end / drop:end       (already wired)
│   └─ last break end                     opens the search window (§3)
│
├─ TIER 3 — EVENT MARKERS                 "something happens here"
│   ├─ kick-dropout landmarks             (already wired — 46% of candidate bars)
│   ├─ fills — 191 detected, 0 usable today   ← the code calls these good mix points
│   └─ kick cues — 136 detected, 0 usable today
│
└─ TIER 4 — GEOMETRIC                     "a position, not a musical event"
    ├─ track_start                        incoming side only
    ├─ track_end                          ** NEVER a valid outgoing swap. PROVEN. **
    │     algebra: anchor = n_bars ⇒ overlap = incoming_anchor ⇒ progress = 1.0 > 0.95
    │     empirical: 0 of 245 successful alignments have ever used it
    └─ the 30-second count-back           a TARGET to search near, never itself a cue
```

**Convergence is the multiplier.** A bar carrying a Tier-1 bass-out *and* a Tier-2 boundary *and* a
Tier-3 fill is the strongest cue available. This is the concept that does not exist today and is the
single biggest change in this plan.

**Why `max(weight)` must go.** Today `_mix_cues.add()` merges co-located markers with `max(weight)`,
so a bar with three agreeing markers scores identically to one with a single marker. That is exactly
the opposite of Sam's rule. Measured consequence: `bass_out` co-locates with an existing cue on
**19 of 20 tracks**, so under `max(weight)` wiring it in would change **almost nothing** — ablation
would still move ~1 decision. Per-tier contributions (MiniMax's insight) are what make convergence
expressible at all.

**Why density still has to be capped.** Fills (191) and kick cues (136) vastly outnumber Tier 1-2
markers. Without a bound, a fill-dense track wins on count. Tier 3 therefore contributes a **capped
total** — it can raise confidence and break ties, never outvote Tiers 1-2. (Note: MiniMax's sketch
capped per bar, which does **not** bound the total, because the score accumulates across every
coinciding bar. The cap must be on the accumulated Tier-3 contribution per candidate.)

---

## 3. THE ARRANGEMENT DECISION TREE

Selection order. Arrangement fit is decided **before** cue strength; cue strength chooses among fits.

```
FOR A PAIR (outgoing → incoming)
│
├─ STEP 1 — define the outgoing search window
│     start = end of the LAST BREAK
│     BOUNDS REQUIRED (both measured, both real):
│       · if that leaves < 16 bars   → walk back to the previous break
│           (Christoph - Reachin: last break ENDS at bar 196 of 200 → 4-bar window)
│       · if there is no break at all → cap the window at the last ~64 bars
│           (Sam Leagas - Double Dutch has no break sections; window would be the whole track)
│
├─ STEP 2 — is there a Tier-1 bass ownership change in that window?
│   │
│   ├─ YES (13-14/20 tracks) → THAT IS THE SWAP POINT. Sam's basic mixing skill.
│   │      Prefer it over a bare section edge even when they nearly coincide;
│   │      when they DO coincide, that convergence makes it stronger still.
│   │
│   └─ NO — the bass runs to the end (6/20). There is no bass swap available.
│          → fall through to STEP 3. This is the 30-second rule's trigger condition,
│            and it is now *derived* rather than a special case.
│
├─ STEP 3 — the 30-second fallback (only reached from STEP 2's NO branch)
│     target = 30 s back from the MUSICAL end (not the file end — see §4)
│     · snap to a real Tier-2/3 marker within ~8 bars (2 phrases) if one exists
│         MEASURED: only 1 of the 6 has one (Double Dutch, fill at bar 135 vs target 135.7);
│         the other five have nothing nearer than 8-12 bars
│     · otherwise use the computed bar itself
│     Sam's caveat, honoured: a kick dropout or fill near the 30 s point IS a marker and
│     should be preferred over an invented bar.
│
├─ STEP 4 — match against the INCOMING track's arrangement
│     Sam: "if the incoming's break is at 45 s, look for a cue 45 s from the outgoing's end."
│     The engine already computes this as `exit_paired` (does the outgoing's end land on an
│     incoming cue?) but only as a 0/1 tie-breaker. Under this plan it becomes a SEARCH TARGET.
│
└─ STEP 5 — among surviving candidates, rank by cue strength (the §2 tree)
      Tier 1 > Tier 2 > Tier 3-capped, with convergence raising confidence.
      Existing geometry gates (16-48 bar overlap, 0.25-0.95 progress) are unchanged and
      still eliminate candidates first.
```

---

## 4. Supporting change: musical end vs file end

`n_bars` comes from the audio file's duration (`stem_detector.py:405`), so it includes trailing
silence and reverb tails. The 30-second count-back must start from the **musical** end.

Measured across 20 tracks (last frame above peak−30 dB): median tail 0.70 s, 7/20 over one bar,
worst 7.25 s (~4 bars). **Detection must not confuse a kick-out with an ending** — BUTCH & Santos
ends with ~15 s of kick-less drum loop that is real, mixable music (Sam caught an earlier
measurement that made exactly this error). The true ending is an energy cliff: −22 dB → −62 dB
inside one second, so any threshold from −20 to −40 dBFS finds it identically.

Ships as a **sibling field**, consumed only by the count-back. `n_bars` is NOT redefined — changing
it would shift every overlap computation and could tip currently-passing transitions over the
16/48-bar edges under fail-closed output.

---

## 5. What changes in the code

| # | Change | Why | Risk |
|---|---|---|---|
| 1 | Cue records carry per-tier contributions instead of a single `max(weight)` | Makes convergence expressible; without it `bass_out` is inert on 19/20 tracks | Medium — changes the score for every pair |
| 2 | Tier-3 contribution capped **per candidate** (not per bar) | 191 fills must not outvote a bass-out | Low |
| 3 | Rank tuple reordered: arrangement fit above cue strength | Sam's explicit instruction; today it is inverted | **High — this is the behavioural core** |
| 4 | Search window derived from the last break, with both bounds | Follows the music; two real failure cases measured | Medium |
| 5 | Wire `bass_out` as Tier 1 | Restores the core mixing model (today: 0/380 influence) | Medium |
| 6 | Wire `fills` + `kick_cues` as Tier 3 | 327 detected markers currently unreachable | Low once capped |
| 7 | Wire the four hint fields | 12/60 create genuinely new anchors | Low — additive, no-op without a hints file |
| 8 | Musical-end sibling field | §4 | Low |
| 9 | 30-second fallback anchor, snap-to-marker | Sam's rule, now derived from Step 2's NO branch | Medium |
| 10 | `track_end` explicitly documented as never a valid outgoing anchor | It is dead weight in every cue set today | None |

---

## 6. Build order

**Step 0 is not optional and comes first.** Baseline is already captured:
`baseline_alignments.json`, sha256[:16] `72cb9c3f84cf23b0` — 380 pairs, 245 aligned, 135 raised,
4 distinct handoff kinds. Extend the regression fixture to pin `paired_cues`, `arr_offset_bars`,
`overlap_bars`, `swap_progress`, `handoff_kind`, `alignment_policy`, `overlap_policy` **before any
signal is wired**. Also populate or delete `regress_section_detection.py` — a gate that returns PASS
on an empty directory is worse than none.

Then, one signal per step, each diffed against the baseline and individually explainable:

1. Signal registry + inertness test (fails if a JSON key is in none of WIRED / DELIBERATELY-NOT / NOT-YET)
2. Per-tier cue records (change 1) — behaviour-neutral by construction; prove it with a zero-diff run
3. Musical-end field (change 8) — no consumer yet
4. Wire hints (change 7) — additive, smallest blast radius
5. Wire `bass_out` as Tier 1 (change 5) + the Step-2 branch
6. Wire fills + kick cues as Tier 3, capped (changes 2, 6)
7. Reorder the rank tuple (change 3) — **the highest-risk step, deliberately last**
8. Window bounds (change 4) and the 30-second fallback (change 9), together

---

## 7. Success criteria (from the Brief, plus what this plan adds)

1. No signal silently unwired — machine-checkable registry
2. Per-signal attribution — one flag, one fixture diff
3. Regression fixture pins the seven fields, built before wiring
4. A blind A/B Sam prefers, or an explicit "no worse, better-grounded"
5. Ablating `bass_out` changes ≥1 decision (baseline: 0 of 380)
6. Fewer than 135 unalignable pairs, without inflating overlaps
7. Handoff kinds broaden beyond today's four; **any signal wired that never once wins a handoff is
   evidence the wiring is inert**

---

## 8. Open questions for the Attack round

1. **Change 3 is the risk.** Reordering the rank tuple changes every transition in every mix.
   Is there a way to stage or A/B it that does not require accepting the whole reorder at once?
2. Does per-tier contribution reintroduce count-inflation by another name — can a track dense in
   Tier-2 section boundaries dominate?
3. Step 2's binary (bass swap available / not) is derived from `bass_out` vs `track_end`. What
   happens on a track where the bass fades rather than cuts? None in this corpus — is that luck?
4. The window bounds in Step 1 are fitted to two observed failures. Are they principled or
   over-fitted to n=2?
5. Tier 1 "prefer bass-out over a bare section edge even when they nearly coincide" — how near is
   near, and does it interact with `COINCIDE_TOL_BARS = 2`?
6. Pre-mortem prompt: it passed every planned test — what did the tests fail to represent?
