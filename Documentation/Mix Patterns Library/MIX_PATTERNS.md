# Mix Patterns — Learned Rules

Rules derived from Sam's corrections to Claude's automation proposals. Each rule has a source (which diff taught it) and a confidence (how many times the pattern has been confirmed).

---

## Rule 1: NEVER bass-swap at the overlap boundary

**Source**: V21→V22, transitions T3 (Saudade→Renegades) and T4 (Renegades→Revoloution)
**Confidence**: 2/2 (corrected both times it occurred)
**Status**: HIGH CONFIDENCE — baked into apply_automation.py

When the incoming track's first drop START lands within BOUNDARY_MARGIN (64 beats / 16 bars) of the overlap end, don't use it as the bass swap point. It gives too little time for the outgoing to fade and creates a hard cut.

**What to do instead**: Fall back to the outgoing's outro start or the nearest structural section change well inside the overlap.

**V23 test results**: Rule correctly moved T3 swap from boundary drop → outro at 1792 (matches Sam's correction). Also moved T7 swap from boundary (4304) → outro at 4256 — Sam accepted the boundary swap on T7, so this is slightly over-correcting. Boundary avoidance may need exceptions when the overlap end coincides with a natural structural handoff. Needs more data.

**V24 test results**: V24 confirmed boundary avoidance is correct. T3 still matches (swap@1792). T7 now accepted at 4256. But exposed TWO bugs:
- **Bug A**: Priority 2 (outro) wasn't using `_inside_overlap()` — T4 selected outro@2368 (only 32 beats from end) without boundary check. FIXED: priority 2 now uses `_inside_overlap()`.
- **Bug B**: Rule 2 (two-stage bass) could push the full kill to the boundary even when the swap was safely inside. T4's two-stage placed partial@2368 + full kill@2400 (exactly at boundary). FIXED: two-stage bass now disabled when `kill_beat` fails `_inside_overlap()`.

With both fixes, T4 would correctly fall to priority 3 → drop@2304 (96 beats from end), matching Sam's V24 correction.

**Implementation**: `_inside_overlap()` helper + `BOUNDARY_MARGIN = 64` constant in apply_automation.py.
**Priority**: (1) incoming first drop IN overlap but NOT near boundary, (2) outgoing outro start NOT near boundary, (3) outgoing last fill/break/drop NOT near boundary, (4) midpoint@16.

---

## Rule 2: Two-stage bass reduction for gentle transitions

**Source**: V21→V22, transition T5 (Revoloution→Route 94)
**Confidence**: 1/1 (single occurrence, watch for repetition)
**Status**: EMERGING — baked into apply_automation.py (conservative trigger)

Instead of a hard bass kill (1.0 → 0.18) at the swap point, Sam sometimes uses a partial cut first, then full kill later:

- Stage 1: Cut to ~0.52 (-6dB) at the outgoing outro start
- Stage 2: Full kill to 0.18 at the incoming's build/drop start (48 beats later in this case)

This creates a gentler transition where the outgoing's bass presence diminishes gradually rather than vanishing instantly.

**When to use**: When the outgoing has a long outro (≥32 beats) AND the incoming has a build section. The partial cut lets both tracks coexist briefly at reduced bass levels.

**V23 test results**: Fires on T2, T4, T5, T9. The learn_from_correction.py diff tool needed its bass threshold updated from 0.5→0.8 to detect the partial cut (0.52) as a "swap" rather than skipping to the full kill. Shape differs from Sam's V22 curve but intent matches on T5.

**V24 test results**: Sam used hard kills (not two-stage) on both T4 and T9. T4's two-stage was wrong because the full kill landed at the boundary (Rule 1+2 conflict, now fixed). T9 had an arrangement shift (overlap moved -32 beats) making comparison unreliable. Confidence stays at 1/1 — the two V24 "rejections" have confounding factors, not clean evidence against two-stage.

**V24 safeguard added**: Two-stage bass now disabled when `_find_incoming_build_drop()` returns a beat that fails `_inside_overlap()` check. This prevents the full kill from landing in the boundary zone.

**Implementation**: `_outro_length() >= 32 and _has_build_section()` trigger in `plan_transitions()`, guarded by `_inside_overlap(kill_beat)`. Partial at swap point, full kill at `_find_incoming_build_drop()` boundary.
**Sam's values**: 0.5166 for the partial cut (≈ -5.7dB, roughly half the linear gain).

---

## Rule 3: Two-stage volume drop at bass swap

**Source**: V21→V22, transition T9 (Kids→Sapian)
**Confidence**: 1/1 (single occurrence)
**Status**: EMERGING — DISABLED in apply_automation.py (false positives)

Instead of a gradual volume fade starting at the bass swap, Sam sometimes does an instant partial volume drop at the swap point, THEN a gradual fade:

- Instant drop: 1.0 → 0.5623 (-5dB) at the swap beat
- Gradual fade: 0.5623 → 0.0 over ~100 beats (25 bars)

**V23 test results**: Section-count threshold (>=14) caused false positives on T1 (Adam Ten, 29 sections — was correct without two-stage vol) and T5 (Revoloution, 16 sections — not vol-corrected). Only T9 (Kids, 18 sections) actually needed it. DISABLED until we get ≥3 observations to calibrate. Section count alone is a poor predictor.

**Sam's values**: 0.5623 for the instant drop (≈ -5.0dB).

---

## Rule 4: Lower sneak volume for percussive intros

**Source**: V21→V22, transition T8 (Professor X→Kids)
**Confidence**: 1/1 (single occurrence)
**Status**: EMERGING — baked into apply_automation.py (conservative trigger)

My default incoming sneak volume is 0.2 (the level the incoming track enters at during the overlap, before ramping to unity at the bass swap). Sam lowered Kids' sneak from 0.2 to 0.1.

**When to apply**: When the overlap is very short (≤80 beats / ~20 bars). With a short overlap, the incoming must sneak in quieter to avoid clashing with the outgoing at full energy.

**V23 test results**: Three trigger iterations:
- V23-A: `intro_clips >= 3 OR intro_len <= 32` — WAY too broad, 7/9 false positives
- V23-B: `overlap <= 80 AND intro_clips >= 3` — missed T8 (only 1-2 clips in window)
- V23-C: `overlap <= 80` — correct, fires only on T8 (62-beat overlap)

**Implementation**: `overlap_len <= 80` in `plan_transitions()`, uses `VOL_SNEAK_LOW = 0.1`.

---

## Rule 5: Volume fade follows bass swap position

**Source**: V21→V22, transitions T3 and T4
**Confidence**: 2/2 (confirmed on both corrected transitions)
**Status**: HIGH CONFIDENCE — apply automatically

When the bass swap moves, the volume automation must move correspondingly:

- **Incoming volume ramp**: Reaches unity (1.0) at the NEW bass swap point, not the old one. A shorter overlap-to-swap distance means a shorter ramp.
- **Outgoing volume fade**: Starts fading from the NEW swap point. A longer swap-to-overlap-end distance means a longer fade.

My V21 code already linked volume to bass swap, but the absolute positions shifted because the swap moved.

---

## Rule 6: Extend outro loops to improve overlap

**Source**: V21→V22, arrangement change on Professor X
**Confidence**: 1/1
**Status**: ARRANGEMENT PATTERN — applies to loop decisions

Sam added a ~7.5-bar outro loop extension to Professor X (bar 1155.5→1163) and moved Kids 8 bars earlier (bar 1140→1132). This widened the Professor X→Kids overlap from ~15 bars to ~31 bars, giving more room for the transition.

**Principle**: When two tracks barely overlap, extend the outgoing's outro with a loop AND/OR start the incoming earlier, rather than accepting a cramped transition. Arrangement and automation are interdependent.

---

## V21 Results (original proposal): 5/9 correct

| Transition | Swap choice | Status |
|-----------|-------------|--------|
| T1: Adam Ten → Savana | incoming drop_1 (beat 592) | ✓ correct |
| T2: Savana → Saudade | outgoing outro_1 (beat 1072) | ✓ correct |
| T3: Saudade → Renegades | boundary drop (beat 1856) | ✗ corrected → outro 1792 |
| T4: Renegades → Revoloution | boundary drop (beat 2400) | ✗ corrected → drop_5 2304 |
| T5: Revoloution → Route 94 | hard kill at outro (beat 2848) | ✗ corrected → two-stage bass |
| T6: Route 94 → Ease My Mind | outgoing outro_1 (beat 3376) | ✓ correct |
| T7: Ease My Mind → Professor X | boundary kill (beat 4304) | ✓ accepted by Sam |
| T8: Professor X → Kids | sneak 0.2, boundary kill | ✗ corrected → sneak 0.1 |
| T9: Kids → Deep House Pumpin' | hard kill + linear fade | ✗ corrected → two-stage vol |

## V23 Results (rules baked in): ~6-7/9 effective

V23 vs V22 comparison is noisy because V22 includes arrangement changes. Transitions marked with corrections=[] have matching swap positions but minor point-count differences (expected from two-stage bass shape changes and V22 arrangement shifts).

| Transition | V23 rules fired | V23 vs V22 status |
|-----------|----------------|-------------------|
| T1 | — | ✓ OK (perfect match) |
| T2 | R2:two-stage-bass | ~ swap correct, shape differs |
| T3 | R1:boundary-avoidance | ✓ swap@1792 matches Sam's fix |
| T4 | R1 + R2:two-stage-bass | ~ swap moved, may be arrangement noise |
| T5 | R2:two-stage-bass | ✓ two-stage applied (shape differs) |
| T6 | — | ✓ OK (perfect match) |
| T7 | R1:boundary-avoidance | ~ swap moved to outro (Sam accepted boundary) |
| T8 | R4:low-sneak | ~ swap correct, point count diffs |
| T9 | R2:two-stage-bass | ~ arrangement shift noise |

**Key insight**: Comparing against V22 is unreliable because V22 has arrangement changes that shift automation positions. The clean V23 vs V21 diff (same arrangement) shows the rules fire exactly where expected.

## V24 Results (Sam's corrections to V23): 6/9 raw, ~8/9 effective

V24 includes arrangement changes on Renegades (-32 end), Professor X (-30 end), Kids (-32 end), Deep House (-32 shift). After accounting for arrangement noise:

| Transition | V24 raw verdict | Arrangement change? | Effective verdict | Detail |
|-----------|----------------|--------------------|--------------------|--------|
| T1 | OK swap@592 | — | CORRECT | |
| T2 | OK swap@1072 | — | CORRECT | |
| T3 | OK swap@1792 | — | CORRECT | Rule 1 confirmed again |
| T4 | FIX swap -64 | Renegades end -32 | REAL CORRECTION | Two-stage pushed kill to boundary. Bugs A+B fixed. |
| T5 | OK swap@2848 | — | CORRECT | |
| T6 | OK swap@3376 | — | CORRECT | |
| T7 | OK swap@4256 | — | CORRECT | Rule 1 over-correction now accepted |
| T8 | FIX point diffs | Professor X end -30 | ARRANGEMENT NOISE | Swap@4592 correct; trailing points removed due to shorter track. Sam also used instant vol kill. |
| T9 | FIX swap -32 | Kids -32, Deep House -32 | ARRANGEMENT NOISE | Same relative swap position (64 beats into overlap). Sam used hard kill (rejected two-stage). |

**Key findings from V24:**
1. **Rule 1+2 conflict discovered and fixed**: Two-stage bass could push the full kill to the boundary, violating Rule 1. T4 had partial@2368 + kill@2400 (boundary). Both bugs now fixed.
2. **Priority 2 (outro) boundary bug fixed**: Outro was exempt from `_inside_overlap()` check. Now enforced.
3. **T7 boundary over-correction resolved**: Sam accepted swap@4256 in V24, confirming Rule 1's outro fallback works for this transition.
4. **Two-stage bass still 1/1 confidence**: V24 "rejections" on T4 and T9 were confounded by boundary bug and arrangement shift respectively. No clean counter-evidence yet.
5. **Arrangement changes common**: Sam adjusted 4/10 track positions in V24. Automation diff tools must always check for arrangement shifts before classifying corrections.

---

## Open Questions

1. **When does two-stage bass become the default?** Only seen once (T5). If Sam uses it on 3+ transitions across multiple mixes, promote to automatic. V24 did NOT add counter-evidence (T4 was a boundary bug, T9 was arrangement shift).
2. **Is 0.1 vs 0.2 sneak predictable from the audio?** Could use intro RMS energy or percussion density to auto-decide. Need more examples.
3. **Does the two-stage volume drop (Rule 3) correlate with track energy?** Kids→Sapian is a high-energy pair. Maybe it's about preventing energy dips during hot transitions.
4. **How does this change across genres?** All 9 transitions here are house/techno at ~129 BPM. Different patterns may emerge at different tempos or with different structures.
5. **Instant volume kill for very short post-swap overlap?** T8 in V24: Sam used instant vol kill (1.0→0.0 at swap beat) when Professor X was shortened to end at the swap point (0 beats post-swap). May be a pattern for overlaps where swap is at or very near the track end. Need more data.
