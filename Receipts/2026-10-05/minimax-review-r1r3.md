I have enough context. Let me write the review.

---

# Review: R1/R3 outgoing-loop simplification

## Verdict: **SOUND with corrections** (gate-able, not rubber-stamp; ship after the corrections below)

The change is a faithful implementation of the rules in `Documentation/Plans/v5-sam-tweaks-analysis.md` §4. The 380-pair replay numbers (215 → 94, 0 status changes, 27 raises unchanged, 7 retargeted) are consistent with the `tools/d15_outro_loop_replay.py` diff discipline the team already uses. But there are concrete holes the patch leaves open. Five of them are real, two are nitpicks.

---

## Q1. `al.handoff_bar_out <= o.n_bars` — vacuous or meaningful?

**Vacuously true in production, with one real edge case it does protect.**

In all three `LANDMARK_POLICIES` (`align_engine.py:238`) the swap is built this way:
- `paired_landmarks_v2` (`_search_anchors`, `align_engine.py:1551-1632`): `handoff_bar_out = outgoing_anchor` where the anchor is filtered by `PHRASE_GRID <= overlap <= max_overlap_bars` and the cues include `add(track.n_bars, "track_end", 5)` (`_mix_cues`, `align_engine.py:1015`). In normal flow `<= n_bars`.
- `tail_anchor_rescue_v1` (`_search_tail_anchor_rescue`, `align_engine.py:1439`): `out_anchor = _nearest_cue(outgoing, out_pt_raw, window_start, o.n_bars)` — explicitly bounded by `n_bars`.
- `claude_decisions_v1` (`alignment_from_decision`, `align_engine.py:2764-2773`): raises if `handoff_out > o.n_bars + 1e-6`. By construction `<= n_bars`.

**Real edge case the check DOES protect:** `_mix_cues` (`align_engine.py:1015-1019`) emits `add(section["end_bar"], "section:{label}:end", 3)`. If a `SECTIONS_STEM` file has a section whose `end_bar` overshoots `n_bars` (the v5 analysis flags several real tracks with section/clip mismatches: RSquared outro 191 in clip names vs 184 in SECTIONS_STEM, Zaro's whole layout), `paired_landmarks_v2` can legitimately produce `handoff_bar_out > n_bars` and the overlap window still passes `PHRASE_GRID..max_overlap_bars`. In that case the `<= n_bars` gate is the ONLY thing preventing R1/R3 from "ending" the outgoing 1-2 bars before the swap. So the check is a data-quality safety net, not a vacuous one — but only fires on a class of bad input the team already knows exists.

**"Is removing the loop ever UNSAFE?"** When `handoff_bar_out <= n_bars`, the swap is at or before the outgoing's natural audio end (`o.n_bars`). The outgoing's native content covers the swap; the loop's job is only to extend PAST the swap. If E sits on a section line, extending is unnecessary — the mix just runs incoming-only from the swap to the next section. That's the standard natural-end mix shape and is exactly what Sam did in T2/T9 (`v5-sam-tweaks-analysis.md` §1, T2: E=32 = Detlef drop_2, loop removed; T9: E=32 = Shilla break_1, loop removed). The 380-pair replay confirms 0 status changes, which is the empirical proof that no transition that previously raised now silently undermixes. **Not unsafe.**

**Recommendation:** Either drop the `<= n_bars` check (and rely on the raise in `alignment_from_decision` plus the `paired_landmarks_v2` overlap constraint) or add a one-line comment explaining it's a data-quality guard, not a swap-position guard. The check as written is correct; the *intent* of "swap not reachable without the loop" is not what it expresses.

---

## Q2. R1 ±2 window — dead air when E is up to 2 bars BEFORE the next section?

**No dead air, but the report's `section` field can name the wrong section. Minor.**

The ±2 window is on `current_incoming_bar` (which is `E = o.n_bars - arr`), and the match uses `next((s for s in i.sections if abs(float(s["start_bar"]) - current_incoming_bar) <= 2), None)` (`align_engine.py:2417-2418`). When E = 20 and there's a section at 18, R1 fires, the loop is skipped, and the outgoing plays to bar `n_bars` (incoming bar 20) — which is 2 bars PAST the section at 18.

Two separate things:
1. **The audio is fine.** The incoming is playing during incoming bars 18–20 (the section the report names is at 18; the outgoing runs through it). The 2-bar "gap" between E and a hypothetical section at 22 is incoming-only material, not silence. Unless the incoming's pre-section region is itself silent (a content issue the pipeline can't detect), there's no dead air. The v5 analysis §1 confirms this is Sam's intended shape: 6/10 transitions land at 32–33 bar overlap, with the outgoing naturally trailing after the swap.
2. **The `section` field is misleading.** `next()` returns the FIRST match. If sections are ordered by `start_bar` (which they are, from `SECTIONS_STEM` JSON), a section BEFORE E will be picked over one AFTER E, even though the outgoing's natural end (E) is actually AFTER the named section. The test `test_r1_skips_natural_end_on_section_line` exercises section_bar=18 (the before-E case) but only asserts the `section` field equals `"break_1"` — it doesn't assert anything about the actual mix geometry, so the misleading naming is not caught. The `test_r1_does_not_skip_when_swap_needs_loop` test exercises the handoff-past-n_bars case (good coverage there) but the report-correctness gap is real.

**Recommendation (small):** prefer sections with `start_bar >= current_incoming_bar` when both exist within ±2. Either break the tie to the larger `start_bar` (the actual nearest section on the "future" side) or report both. The current behavior is correct musically; only the audit field is wrong.

---

## Q3. ValueError suppression — can it hide a real safety-limit failure?

**Constructed case shows: no real safety failure is hidden, but the suppression rationale is slightly off-target. The replay numbers are the proof.**

Construction: `handoff_bar_out = 110`, `outro_start = 100`, `o.n_bars = 110`, `arr = 90`, so E=20, `locked_swap_gap = 10`, `optional_reach = True` (110 ≤ 110). Suppose `i.sections` has `break_1` at 22 (gap 2) and `break_2` at 25 (gap 5). Cap=12.

With `INTERIM_V1` cap on (`align_engine.py:2447-2455`):
- `break_1`: gap 2 ≤ 12, proceed. `pick_cue_bounded_drum_loop` returns `None` (no clean-drum window at bar 102-104 in this contrived outgoing). `short_swap_candidate` stays None. `capped_candidate` stays False.
- `break_2`: gap 5 ≤ 12, proceed. Same outcome.
- After loop: `chunk is None`, `short_swap_candidate is None`, so the suppression at `align_engine.py:2498-2499` doesn't matter (no ValueError was going to raise). The second `outgoing_loop_not_needed` set at `align_engine.py:2512-2516` fires (`capped_candidate and candidate_nxt is None and chunk is None` is False here because `capped_candidate` is False).

Now the actually-hiding case: change `break_1` to be at 25 (gap 5, < locked_swap_gap 10 → short_swap_candidate set if `pick_cue_bounded_drum_loop` returns a chunk) and `break_2` at 33 (gap 13, > cap 12, in budget). With cap on, `break_2` is skipped (`capped_candidate = True`). `short_swap_candidate` is set from `break_1`. The suppression fires (no raise). The mix proceeds with no loop.

**Is this a hidden safety failure?** No:
- `handoff_bar_out = 110 = o.n_bars` → the swap is at the very end of the outgoing's native audio. The outgoing's content covers the swap exactly.
- The "shortfall" that the original ValueError would have flagged is "`break_1` would end 5 bars before the swap". But `handoff_bar_out = n_bars` means the swap IS the end of the native audio — there is nothing to fall short of. The loop is genuinely not needed for the swap.
- After the swap, the incoming continues solo for whatever gap to the next section. That's a natural-end mix, exactly what the cap is designed to permit.

**Where the rationale drifts:** the R3 reason `"outgoing reach exceeds cap"` (`align_engine.py:2514`) reads as "the outgoing doesn't need to reach that far". More accurate would be "policy declines to plan a loop beyond the cap" — because the loop WAS needed (the original code was trying to plan one) and the policy overrode it. Minor wording, not a bug.

**Empirical proof:** 380 pairs, 27 raises unchanged, 0 new raises (`Documentation/Plans/v5-sam-tweaks-analysis.md` §5C + the patch's own numbers). The suppression cannot be hiding a real failure mode, because the corpus replay would have shown a new raise.

**Recommendation:** tighten the R3 reason to `"policy cap: outgoing reach exceeds {cap} bars"`. Add a direct test for the suppression itself (see Q5).

---

## Q4. State leakage

**Clean. One nit: the new field has no docstring; the sibling `outgoing_loop_abandoned` does.**

- **Reset:** `al.outgoing_loop_not_needed = None` at `align_engine.py:2276`, immediately after `policy = policy or _DEFAULT_POLICY` and before any branching. Runs in every mode (legacy + landmark) and every call. Correct.
- **Non-landmark path:** All R1/R3 logic is inside `if landmark_mode:` (`align_engine.py:2410`) and `if landmark_mode and outro is not None and not skip_outgoing_loop:` (`align_engine.py:2441`). The non-landmark `else` at `align_engine.py:2423-2425` computes `nxt` directly from `i.sections` past `o.n_bars + 1` and never touches `outgoing_loop_not_needed`. Unaffected. Correct.
- **`claude_decisions_v1` path:** `compute_aligned_positions` (`align_engine.py:2919-2921`) routes decisions to `fills_from_decision` (`align_engine.py:2783`) and non-decisions to `plan_fill_or_cut`. Since `fills_from_decision` does not call `plan_fill_or_cut`, R1/R3 never fire for decision-driven transitions. The `outgoing_loop_not_needed` field will always be None for decisions. Correct.
- **`tail_anchor_rescue_v1` path:** In `LANDMARK_POLICIES` (`align_engine.py:238`). `plan_fill_or_cut` is called for these. `handoff_bar_out = n_bars - 16` from `_search_tail_anchor_rescue` (`align_engine.py:1477`), so the `<= n_bars` guard is true and R1/R3 do apply. This is intended — the rescue's `E = in_anchor + 16` (typically bar 32 if `in_anchor` is the first drop) makes R1's "section within ±2 of 32" a real and probably-correct check.
- **R1 vs R3 mutual exclusion:** R1 sets `skip_outgoing_loop = True` (`align_engine.py:2419`), which makes the R3 guard `if landmark_mode and outro is not None and not skip_outgoing_loop:` (`align_engine.py:2441`) false. The two `outgoing_loop_not_needed` writes are mutually exclusive per call. Correct.
- **`outgoing_loop_abandoned` (the D15/D2b field):** Set at the end of the function (`align_engine.py:2639`). The patch's R1 path sets `skip_outgoing_loop = True`, which causes `section_targets` and `landmark_targets` to be empty lists; the `if candidate_nxt is not None and not any(s.kind == "outgoing_tail" for s in specs):` at the end checks `candidate_nxt`, not `skip_outgoing_loop`, so it can't fire spuriously for R1 skips. R3 leaves `candidate_nxt = None` when the cap suppresses all candidates, so `outgoing_loop_abandoned` is also not spuriously set. Both fields are correctly disambiguated.

**One nit:** the new `outgoing_loop_not_needed: dict | None = None` field at `align_engine.py:796` has no docstring. The sibling `outgoing_loop_abandoned` field at `align_engine.py:782-795` has a 13-line docstring explaining D15/D2b's transparency contract. Add a parallel docstring to the new field stating:
- Set when a transition decides NOT to plan an outgoing tail loop because of policy (R1: natural end on a section line; R3: policy cap).
- Mutually exclusive with `outgoing_loop_abandoned` (which means "tried but failed"). The combination is unambiguous: `abandoned=None and not_needed=None` → loop planned (or no loop needed and no candidate existed); `abandoned=X and not_needed=None` → tried, failed; `abandoned=None and not_needed=X` → policy declined; both non-None should be impossible.
- Keys present: `{"reason": str, ...}` with `reason ∈ {"natural end on section line", "outgoing reach exceeds cap"}`.

---

## Q5. Missing tests

Listed in priority order:

1. **Direct suppression test.** `test_r3_cap_cannot_create_short_swap_value_error` is named as a suppression test but the old-policy baseline it builds PRODUCES a loop (it doesn't raise). The test is actually verifying the cap REPLACES a working loop with no loop, not that it SUPPRESSES a would-have-raised ValueError. Construct a case where `break_1` is in cap but the clean-drum search returns `None` (short_swap_candidate set), and `break_2` is in budget but out of cap. With cap off, that case should raise the "Cannot plan outgoing tail loop" ValueError. With cap on, it should produce no tail and set `outgoing_loop_not_needed == {"reason": "outgoing reach exceeds cap", ...}`. This is the only test that actually exercises the suppression branch at `align_engine.py:2498-2499`.

2. **The second `outgoing_loop_not_needed` set is not asserted.** `test_r3_caps_optional_15_bars_but_keeps_10` with `section_bar=35` goes through the `capped_candidate and candidate_nxt is None and chunk is None` branch at `align_engine.py:2512-2516`, but the test only asserts `_tail(...) is None` — it never checks `alignment.outgoing_loop_not_needed == {"reason": "outgoing reach exceeds cap", "max_reach_bars": 12.0}`. Add that assertion.

3. **`outro is None` short-circuit.** R1 and R3 both have `and outro is not None` in their guards (`align_engine.py:2414`, `align_engine.py:2441`). The D15/D2 reasoning (§4 R4) explicitly calls out that R4 should fire for the no-outro case. Add a test that `outro is None` produces no `outgoing_tail` AND no `outgoing_loop_not_needed` set (because the "no outro" case is a separate rule, R4, not in this patch).

4. **Reset on re-call.** Test that calling `plan_fill_or_cut` twice with different policies on the same `Alignment` clears `outgoing_loop_not_needed` from the first call. One-line test.

5. **`tail_anchor_rescue_v1` policy path.** All five `test_outro_loop_simplify.py` tests use `alignment_policy="paired_landmarks_v2"`. The rescue path (line 1329) is in `LANDMARK_POLICIES` and the patch's R1/R3 logic applies to it. The rescue's typical `handoff_bar_out = n_bars - 16` makes the `<= n_bars` guard true; the R1 ±2 window around `E = in_anchor + 16` is a real check. Construct a rescue case (no cues in last minute, handoff at `n_bars - 16`) and verify the policy behaves as intended.

6. **Legacy (non-landmark) path unaffected.** Negative test: `alignment_policy="legacy_v1"` with the new INTERIM_V1 should produce the SAME tail spec as before the patch. The `_pre_simplify_policy()` helper in `test_arrangement_safety.py` covers this for the existing tests but the new INTERIM_V1 flags aren't tested in legacy mode.

7. **"Section BEFORE E" report correctness.** As noted in Q2: E=20, sections at 18 and 22, both within ±2. The current `next()` returns `break_1` (18). The outgoing actually plays through 18 to E=20. Either add an assertion that prefers the section AT or AFTER E, or document the behavior. Optional.

8. **Corpus replay regression test.** The replay numbers (215 → 94, 0 status changes, 7 retargeted) are documented in the plan but not pinned by a test. A small `Tests/test_r1r3_corpus_replay.py` that runs `Tools/d15_outro_loop_replay.py` against a frozen baseline JSON and asserts the exact counts would catch any future regression that reintroduces loops on the R1/R3 cases. The replay tool already supports the right diff (`d15_outro_loop_replay.py:99-129` post-patch); just wire it into pytest.

---

## Summary

| Concern | Severity | Action |
|---|---|---|
| `<= n_bars` check is vacuously true in normal flow | Low (data-quality guard, not safety) | Add comment or drop |
| R1 `next()` may name section BEFORE E | Low (report only, loop correctly skipped) | Prefer sections `>= E`; add test |
| R3 reason wording | Nit | Tighten to "policy cap: outgoing reach exceeds N bars" |
| `outgoing_loop_not_needed` missing docstring | Low (correctness OK, audit gap) | Add 5-line docstring |
| Suppression branch not directly tested | Medium | Add 1 test (item Q5.1) |
| Second `outgoing_loop_not_needed` set not asserted | Medium | Add assertion in existing test (item Q5.2) |
| `outro is None` / reset / rescue / legacy paths untested | Low–medium | 3 small tests (items Q5.3–5.6) |
| Corpus replay counts not pinned | Low | Add regression test (item Q5.8) |

**Ship-blockers:** none. The patch correctly implements the documented rules, the replay numbers are consistent, and the only behavior I can't verify by reading the diff is the suppression of a real ValueError — which the corpus replay's "0 new raises" empirically refutes. Items Q5.1, Q5.2, and the docstring are the highest-value follow-ups; the rest are hardening.

**One question only Sam can answer** (carrying forward `v5-sam-tweaks-analysis.md` §6.1): T2 in V3 had a 56-bar Jones loop that Sam explicitly okayed; T2 in V5 has the same pair with the loop removed. R1 implements the V5 call. If a future correction re-adds a long loop where E was within ±2 of a section line, R1 will be falsified live — and the test corpus will catch it because the baseline JSON will diff. That's the right failure mode.


===MINIMAX-ASK-DONE exit=0 session=pi nonce=909990afea90459ca03213532246c6a7===
