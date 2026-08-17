# Brief — Rewiring the analysis → arranger bridge

**Slug:** `arranger-signal-rewiring` · **Date:** 2026-08-17 · **Architect:** Claude (Opus 5)
**State:** FRAMED — awaiting **Gate F** (Sam: "problem, criteria, tier — right?")

---

## 1. The decision

Every signal the analysis layer produces is either wired into the arrangement decision or it is not.
Today, four are wired and roughly a dozen are not. **How do we connect the unwired ones —
completely, in a verifiable order, without conflicts and without silently changing mixes for the
worse?**

Sam's framing: *"I have built so much in the project that is currently unused that I want to make
sure it all goes in with no conflicts."* The two failure modes he named are **omission** (something
stays unwired and nobody notices) and **conflict** (signals fight each other once connected).

## 2. Tier

**T3 FULL.** Two triggers:

- **Blast radius** — changes the anchor set for *every* transition in *every* mix; touches
  `align_engine.py`, `propose_arrangement.py`, `stem_detector.py`, `transition_policy.py` and the
  test suite. `analysis.py` / `amplitude_analysis.py` / `stem_grid.py` are shims over the shared
  **Audio Analysis Toolkit**, which **Ableton Project Setup also depends on**.
- **Trust** — Sam acts on the resulting mixes without independently re-deriving whether the wiring
  is right, and is explicitly relying on this plan for completeness.

Not triggered: money (no direct financial path yet), irreversibility (git-versioned, fast rollback),
autonomy (Sam reviews every mix before it goes anywhere).

### ROOM DEGRADATION — must be resolved before Attack (blocking)

T3 requires three lenses, and the floor is **Lens A + Lens B, never lower**. Today:

| Lens | Brain | Status |
|---|---|---|
| A — Adversary | Codex Sol | **UNAVAILABLE** — out of usage, verified 0% remaining this morning; resets 2026-08-20 07:16 |
| B — Operator/adoption | MiniMax M3 | Available (two good reviews delivered today) |
| C — Verifier | Kimi K3 | **UNAVAILABLE** — `403 usage limit for this billing cycle`; **not** the rolling window, so waiting does not clear it |

**Lens A is below the floor.** Per the protocol this is "pause or get Sam's named acceptance" — it
may not be silently degraded. Options, for Sam:

- **(a) Sequence it** *(recommended)* — run Frame → Ground → Diverge now; hold **Attack** until
  Codex returns Wednesday 20th. Nothing is blocked: the sketches and evidence are the slow part.
- **(b) Named acceptance** — run Attack with MiniMax alone plus a fresh-context Claude adversarial
  lens, recorded in the Decision Record as a degradation with its reason. Weaker: two Anthropic
  lineages are not two independent brains.
- **(c) Top up Kimi** — restores Lens C but not Lens A; does not by itself meet the floor.

## 3. Success criteria (testable)

1. **No omission.** A machine-checkable inventory exists mapping every signal in
   `SECTIONS_STEM_*.json` + the hints file to one of exactly three states: WIRED (with the consuming
   call site), DELIBERATELY-NOT-WIRED (with the recorded reason), or NOT-YET. A test fails if a
   signal appears in the JSON and in none of the three lists.
2. **No silent conflict.** Anchor-set changes are attributable per signal: enabling exactly one
   signal changes the arrangement in a way that can be diffed and explained.
3. **Regression-visible.** A fixture pins `paired_cues`, `arr_offset_bars`, `overlap_bars`,
   `swap_progress`, `handoff_kind`, `alignment_policy` and `overlap_policy` for a known corpus,
   *before* any signal is wired. Today's golden fixture pins none of these.
4. **Musically better, not just different.** At least one blind A/B on a real mix where Sam prefers
   the rewired output, or an explicit accepted "no worse, and better-grounded".
5. **The bass model is live.** After the change, ablating `bass_out` changes at least one decision.
   Today it changes zero across 380 pairs — that is the baseline to beat.
6. **Fewer unalignable pairs.** Measured baseline (captured 2026-08-17 from unmodified code, before
   any wiring): of 380 ordered pairs across the 14.08.26 corpus, **245 align and 135 (36%) raise**.
   Wiring should reduce 135 without inflating overlaps or weakening the caps. Failures that remain
   should be explainable (genuine cue deserts like Revoloution), not arbitrary.
7. **More handoff variety.** All 245 successful alignments today use just **four** handoff kinds,
   and 156 of them (64%) are the same one (`section:drop:end->drop`); the other three are
   `landmark:kick_dropout:end->drop` (52), `landmark:kick_dropout:start->drop` (23) and
   `section:break:end->drop` (14). That concentration is a symptom of anchor poverty, not musical
   preference. Expect the distribution to broaden — and treat any signal that gets wired but never
   once wins a handoff as evidence the wiring is inert.

**Baseline artifact:** `baseline_alignments.json` in this folder — all 380 pairs with
`handoff_bar_out`, `arr_offset_bars`, `overlap_bars`, `swap_progress`, `handoff_kind`,
`alignment_policy` and paired-cue bars. **sha256[:16] = `72cb9c3f84cf23b0`.** Captured before any
change; this is the diff target for criterion 3 and must not be regenerated after wiring begins.

## 4. Hard constraints (eliminative — a design that violates one is out)

- **Fail-closed output is preserved.** Commissioned reconciliation must not become more likely to
  reject a valid mix.
- **No global `n_bars` redefinition.** A musical-end value ships as a sibling field. Changing
  `n_bars` shifts every overlap computation and could tip currently-passing transitions over the
  `[16, 48]` edge.
- **Additive before subtractive.** No signal may *remove* an anchor the engine can reach today
  without that being an explicit, separately-reviewed decision.
- **One signal per step.** No step wires two signals at once — criterion 3.2 is unachievable
  otherwise.
- **The Audio Analysis Toolkit stays byte-compatible** for Ableton Project Setup, or the change is
  co-ordinated with that project explicitly.

## 5. Verified premises (evidence-linked; tested today unless marked)

| # | Premise | Status |
|---|---|---|
| P1 | `_mix_cues` (`align_engine.py:256-282`) is the sole door into the swap decision | **VERIFIED** — read + traced |
| P2 | Ablating `bass_in`/`bass_out`/`bass_out_is_end` changes 0 of 380 pair decisions | **VERIFIED BY MEASUREMENT** (audit 2 ran the engine) |
| P3 | Landmarks supply 164/357 (46%) of candidate cue bars; 22/28 paired cues involve one | **VERIFIED BY MEASUREMENT** (audit 3) |
| P4 | `fills` (191 across 20 tracks) reach only a loop-exclusion mask, never an anchor | **VERIFIED** — `align_engine.py:648, 677-678` |
| P5 | The four hint fields have zero refs in `align_engine.py` / `propose_arrangement.py`; `intro_skip_bars` + `loop_source_sec` are dead behind `USE_ALIGN_ENGINE` | **VERIFIED** |
| P6 | `_handoff_candidates` / `_score_lineup` / `LIKE_ENERGY` never execute — `align_pair:482-483` early-returns for any track with landmarks, and 20/20 have them | **VERIFIED** |
| P7 | Wiring fills + kick_cues + loop_windows + bass_out + major_cues + hints does **NOT** rescue the blocked Revoloution transition — its legal zone (bars 148-163) stays empty; only bar 136 is added | **VERIFIED BY MEASUREMENT — tested at Frame time specifically to avoid planning on a false premise.** The tail-anchor fix is therefore independently justified, not superseded |
| P8 | MIK exposes no cue points to lose (`mutagen` tags `None` on 20/20; no cue table in `MIKStore.db`) | **VERIFIED** |
| P9 | Filter sweeps are unwired **by decision**, not omission (`ai-activity-log.md:42`) | **VERIFIED** |
| P10 | The 48-bar search cap is deliberate and test-pinned, not a bug | **VERIFIED** — 3 independent confirmations; `Tests/test_arrangement_safety.py:48` |
| P11 | The two routes into the arranger disagree about what a bar is — `load_track` reads raw `start_bar` off the constant-BPM clock while `segments_from_stem_sections` maps through the warp grid | **TESTED — TRUE BUT BOUNDED; NOT A BLOCKER.** Measured by extracting all `WarpMarker` pairs from `Output/Sections V2.als` (13 matchable tracks, 3,080-15,390 markers each) and interpolating each section's `start_sec` to its true warp-grid beat. `start_bar` is a pure constant-BPM restatement of `start_sec` on 20/20 tracks (max residual 0.05 s), so `load_track`'s bars ARE constant-BPM bars — confirmed. Worst divergence vs the warp grid: **0.254 bars (~1 beat, 0.478 s) on Revoloution**; 8/13 tracks under 0.01 bars. **Why it does not block:** `_mix_cues.add()` rounds to whole bars, and the EXISTING section cues already carry this identical error from the same clock. New bar-valued cues inherit the same error, not a new one — the risk is pre-existing and uniform across signals, not introduced by wiring. Recorded as a real but sub-beat inconsistency worth its own fix later; it does not gate this plan |

## 6. The known design conflicts (what Attack must solve)

Named now so no reviewer has to rediscover them:

1. **`weighted_score` inflation — the biggest one.** `_search_anchors` ranks candidates partly by
   `weighted_score`, which *sums* the weights of all coinciding cues. Adding 191 fills and 136 kick
   cues means the score starts tracking **how many cues a track happens to have**, not how musical
   the alignment is. A track dense in fills would win on count alone. Any design that just adds cues
   without addressing the scoring function will silently change every mix in a direction nobody
   chose.
2. **Double-counting the same musical event.** `bass_out` sits within 2 bars of `section:drop:end`
   on ~50% of pairs, and `COINCIDE_TOL_BARS` is 2 — so wiring bass_out risks counting one musical
   event twice, compounding conflict 1.
3. **Cue-weight collision semantics.** `add()` merges by `max(weight)` and appends labels. Agreement
   is therefore free (good), but a high-weight signal landing on a low-weight bar silently promotes
   that bar. Weight assignment is a design decision, not a detail.
4. **`fills` serve two opposed roles.** Today they mean "don't loop here". Wiring them as anchors
   means "swap here". Those are compatible in principle — anchor at the fill, don't use it as loop
   content — but the interaction must be stated, not assumed.
5. **Rescue-pass ordering.** Under `INTERIM_V1` the existing rescue pass is empty
   (`allow_intro_phrase_swaps=False`), so any new pass is really the second in production and must be
   pinned explicitly so it stays last under `SAM_V1`.

## 7. Non-goals

- Not fixing the observation gap (render + flam/clipping checks). Real, separately carded.
- Not the stereo-width cue. Pinned by Sam.
- Not reviving `cue_candidates.py` wholesale — only the capabilities it uniquely carried
  (confidence weighting, provenance) are in scope, and only if a lens argues for them.
- Not the learning loop (`pair_history`, `genre_priors`, ground-truth YAML). Separately carded.
- Not the tail anchor / 30-second rule — separate, already reviewed, independently justified by P7.

## 8. Gate F — the question for Sam

1. Is the **problem** right: connect every unwired signal, completely and attributably, without
   conflicts?
2. Are the **success criteria** right — especially #5 (bass ablation must change a decision) and #4
   (a blind A/B you actually prefer)?
3. Is **T3** right, and which **Room-degradation option** — (a) sequence and wait for Codex
   Wednesday, (b) named acceptance to run degraded now, or (c) something else?
