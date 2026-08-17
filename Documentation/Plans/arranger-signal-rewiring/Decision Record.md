# Decision Record — arranger-signal-rewiring

ADR-style. Every Sam decision with the options he saw, unresolved tensions, invalidating
assumptions, and the pre-mortem. Append-only.

---

## D-01 — Gate F: tier and Room degradation (Sam, 2026-08-17)

**Options presented:**
- (a) Sequence it — Frame/Ground/Diverge now, hold Attack until Codex returns Wed 20th
- (b) Named acceptance — run Attack degraded now (MiniMax + a fresh-context Claude adversarial lens)
- (c) Top up Kimi, then decide

**Sam chose (b), with an addition:** *"named acceptance - run degraded now but with the addition of
adding a note to run the full test through codex on thursday when he's back"*

**Decision recorded:**
1. The T3 Attack round runs **degraded** — Lens B (MiniMax M3) plus a fresh-context Claude
   adversarial lens standing in for Lens A. This is a **recorded degradation, not a silent one**:
   two Anthropic lineages are not two independent brains, and the process floor
   (Lens A + Lens B) is knowingly not met.
2. **BINDING FOLLOW-UP — C-01: a full Codex Sol adversarial pass runs on Thursday 2026-08-21.**
   Sam said Thursday; Codex's quota resets Wed 2026-08-20 07:16, so Thursday is safely clear.
   - This is **not** a mandatory pre-build condition — Sam explicitly wants to proceed now, so
     gating the build on Thursday would defeat his choice.
   - It is a **tripwire**: any BLOCKER Codex raises **reopens the ledger and voids authorization for
     anything not yet merged**, and anything already merged is re-examined against that finding
     before the next mix is built.
   - Tripwire owner: Claude. Trigger date: 2026-08-21. Must be carried in `Build Checklist.md` and
     covered by the manifest hash.

**Degradation reason (for the record):** Codex Sol out of usage (verified 0% remaining
2026-08-17 10:02, resets 2026-08-20 07:16). Kimi K3 returned
`403 usage limit for this billing cycle` — the billing-cycle limit, not the rolling 5-hour window,
so no wait clears it. Lens C is therefore absent by the recorded cap path with the K3→K2.7 downshift
unavailable (the cap is account-level, not tier-level).

**Not explicitly answered at Gate F:** questions 1 (is the problem right) and 2 (are the success
criteria right). Sam engaged with the Brief and chose a lens option without contesting either, so
the run proceeds — but neither is recorded as affirmatively confirmed, and both remain open to
correction. Success criterion #5 (ablating `bass_out` must change at least one decision) and #4
(a blind A/B Sam prefers) are the two most likely to need his input later.

---

## D-02 — The 48-bar landing for the tail anchor (Sam, 2026-08-17)

**Question:** should the 30-second rule target ~30 s and accept landing at the 48-bar cap when the
incoming's drop is late, or prefer a shorter overlap where one exists?

**Sam:** *"yes, lets see if it works, we can change later if it doesn't but i think it will be ok,
as long as the bass swap lands at a cue point it will sound fine."*

**Decision:** target ~30 s; accept landing at the cap. Sam's stated acceptance criterion is that the
**bass swap lands on a cue point** — that, not the overlap length, is what he will judge it by.
Revisit if the first real mix disagrees. This concerns the separate tail-anchor plan, recorded here
because it was decided in the same session.

---

## Unresolved tensions

- **T-1.** The degraded Attack (D-01) means the highest-blast-radius change in this project's
  history gets one genuinely independent brain instead of two, until Thursday. C-01 is the
  mitigation, not a cure.
- **T-2.** Success criteria 1 and 2 are unconfirmed (see D-01).

---

## Invalidating assumptions

If any of these turns out false, the plan must be reopened:

1. **P1** — `_mix_cues` is the sole door into the swap decision. If another path can influence
   anchors, the whole "one chokepoint" design collapses.
2. **P2** — bass ablation currently changes 0/380 decisions. This is the baseline success criterion
   #5 is measured against; if the measurement was wrong, the criterion is meaningless.
3. **P7** — the rewiring does not rescue Revoloution. If false, the tail anchor becomes unnecessary
   and its plan should be dropped rather than built.
4. **P11 (bounded)** — the constant-BPM vs warp-grid divergence stays sub-beat. Measured worst case
   0.254 bars. If a track is found where it exceeds ~1 bar, every bar-valued cue is suspect and the
   rewiring must stop until the clocks are reconciled.

---

## Pre-mortem

*(To be completed after REVIEW-STABLE, before verdict — T3 requires a dedicated pass.)*

---

## D-03 — Live vs produced mixing: the entry/exit "conflict" is not a conflict (Sam, 2026-08-17)

**The apparent conflict.** Claude and MiniMax both called "entry and exit are derived from the swap
rather than chosen" the biggest defect in Plan v1. Published research says the opposite — Zehren:
*"the most important decision is where to place t2 (the switch point), as the other two points are
then chosen accordingly... if the switch point is correctly identified, the transition should sound
good regardless of the start and end positions."*

**Sam's resolution (accepted):** the two describe different disciplines.

> *"That's the live DJ version. This is the polished production DJ mix version. When you're doing
> it live you've got to almost guess where that bass swap's gonna be, then start the record and
> hope your bass points match up. In this case we can PICK the bass swap point, and if the start or
> end of the track don't line up perfectly, we can extend them with loops — and that's where the
> polished professional version gets its weight."*

A live DJ derives entry and exit because they have no choice: the record has already started. We are
producing, so we can choose all three and **bridge the gaps with loops**. Same model, more polish.

**Design consequence — this is now the target shape:**
1. **SWAP** is primary: the strongest cue point (phrase position, convergence-weighted)
2. **ENTRY** lands on a real marker on the OUTGOING before the swap (sparse host preferred);
   the incoming's intro is looped BACKWARD to reach it
3. **EXIT** lands on a real marker on the INCOMING after the outgoing ends; the outgoing is looped
   FORWARD to reach it

All three land on markers; loops bridge. The loop machinery already exists
(`_plan_incoming_entry_extension`, the outgoing-outro loop) — what is missing is the anchor CHOICE
for entry and exit, plus the fact that on the production path neither entry mechanism currently
fires at all (`extend_incoming_entry=False` under INTERIM_V1; block (1) gated `if not landmark_mode`).

## D-04 — Research refinements (Sam, 2026-08-17)

All three accepted:
1. **Express the tail rule in BARS, not seconds.** 30 s ~= 16 bars at 128 BPM, which is why it
   works, but it drifts off-grid at other tempos (17.5 bars at 140). Sam: *"I think I was just
   trying to get my point across with the timing."*
2. **Include 8-bar phrase positions**, not only 16/32. The literature's working unit is 4-8 bars,
   and Vente already required bar 8.
3. **Tech house favours shorter, sharper transitions** ("quick cuts at phrase changes"); the
   median-25-bar default may be long for that end of the catalogue.

## D-05 — Filter sweeps are SHELVED, not rejected (Sam, 2026-08-17)

The 2026-05 log records filter sweeps being "dropped from default (conflict with bass cuts)", and
the 2026-08-17 audit flagged the capability as built-but-never-driven. Sam's correction:

> *"The filter sweeps weren't dropped because I didn't like them. They were dropped because we were
> having so much trouble getting the basics right. Once we've got our cue points bang on, then we
> can start talking about creative mix techniques. So it's shelved — not, we're not looking at it."*

**Sequencing rule this sets:** creative technique (filter sweeps, three-band EQ choreography, echo
throws, multiple transition archetypes) is gated behind correct cue points. Do not propose creative
features while the basics are unwired. Revisit once the rewiring is validated by ear.

---

## D-06 — Loops are not the first port of call; swap position is chosen for the shape it leaves (Sam, 2026-08-17, from the Nappp->Christoph correction diff)

**Context.** Sam hand-corrected the Nappp -> Christoph the Rise transition (`14.08.26 Mix V1 SW
Tweaks.als`) after finding today's generated version looped busy intro content instead of using the
track's own clean outro, and asked Claude to derive the pattern by diffing his edit against the
generated one. The diff and Sam's own explanation converged on the same read.

**Measured diff (verified against both ALS files):**
- Generated: Nappp's drop_3 cut at bar 152, then bridged to the true ending with a borrowed 5-bar
  loop from the INTRO (bars 27-32) — real audio, but the wrong part of the track, and it read as
  "busy" against the calm true outro sitting right there.
- Sam's correction: Nappp's drop_3 clip runs UNCUT all the way to the track's real end (bar
  120->168, no early split), then the genuinely clean 3-bar outro is repeated twice as the
  extension. No borrowed material, no early cut.
- Generated: Christoph's audible content starts at an arithmetic arrangement position (beat 532)
  with no evident musical reason.
- Sam's correction: Christoph's intro + first drop + first fill are merged into one continuous clip
  starting at beat ~480 — the same point Nappp's final drop begins — then the fill is REPEATED
  (a second copy immediately after) before Christoph's real second drop takes over as the payoff.

**Sam's own explanation (verbatim, cleaned up):**

> "There's such long tracks that you've got that first little drop section, but that doesn't need to
> be played loud... where I've done the bass switch, that can be used as the bass switch — it
> doesn't need to be that first high energy part. And then the outgoing track can run up to the fill
> of the incoming track, and then you can have the full drop later on... loops aren't there as a
> first port of call, get that bass switch right. If we'd have done the bass switch on the first
> bass bit, the outro would have seemed weird — a really quick outro. That's why I did the bass
> switch on the second drop section, so the outro can run over the entirety of that drop."

**The two rules this establishes, distinct from anything already in the plan:**

1. **Loops are not the first port of call — CORRECTED, this is not "last resort" (Sam, 2026-08-17,
   same session).** Claude's first pass at this rule overstated it: "last resort" implies a loop is
   only acceptable once every other option has failed, which would make an implementation reluctant
   to loop even when looping is genuinely the right call. Sam's actual position is narrower and
   better: CHECK whether the track's own natural remaining material already covers the gap before
   reaching for a loop — but once it genuinely doesn't, looping is a normal, legitimate tool, not a
   failure state. Prefer extending via the track's OWN natural remaining material (running the
   existing clip further, or repeating a genuinely clean/natural marker like a fill) FIRST; loop only
   when that natural material truly does not reach. Directly explains today's Nappp bug: raw overlap
   was already 40 bars (comfortable, no loop needed), yet a loop was added anyway — this rule would
   have prevented that, without needing to treat every future loop as a fallback failure.
2. **The swap position is chosen for the OUTRO SHAPE it leaves the outgoing, not just for being a
   valid marker.** A technically-legal early swap can still be wrong if it leaves the outgoing an
   abrupt, stub-like exit. On a long track the incoming does not need to arrive at its own peak
   energy — bringing it in quiet and early, swapping there, and using the incoming's own later
   markers (fill, second drop) as the payoff gives the outgoing room for a proper wind-down.

**Status: NOT YET CODED.** This is a genuinely new selection preference (not a "wire an existing
signal" step like today's fills/phrase/deep-intro work), touches the same loop-budget and swap-
selection logic already flagged as high-risk (D-03, the arrangement-first reorder), and deserves the
same discipline — baseline diff, review — before it changes behaviour. Logged here so it is not lost;
scoping the implementation is the next decision point with Sam.
