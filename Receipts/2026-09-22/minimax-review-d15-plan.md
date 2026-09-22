I have everything I need. Let me write the review.

---

# D15 plan review — verdict

**SOUND, with 6 specific refinements needed before code lands.** None are blockers, but several are plan-level gaps (not just code-level details), and one of them is the plan calling its own judgment-call fix an "open question" when the codebase's own docstring already answers it. Burn list D15 entry can be drafted, but only after the plan is revised.

---

## What's verified against the actual code

I read `align_engine.py` at the cited line ranges and `propose_arrangement.py` around the candidate consumer. Line numbers in the plan are within ±5 of current source (close enough — descriptions match exactly).

- **D1 mechanism, confirmed.** `landmark_targets` (built ~2326-2325 in current source) is one flat merged list; `sorted(set(landmark_targets))` (line 2336) sorts by bar only. The success branch at ~2366-2367 sets `nxt = candidate_nxt; target_name = candidate_name; chunk = candidate_chunk; break` — first-success wins. A `landmark:*` at bar 40 will indeed beat a `section:break_1` at bar 44. T1 reproduction is plausible from the code.
- **D2 mechanism, confirmed.** The last-resort block lives inside `if nxt is not None and outro is not None:` at line 2381. `nxt` is `None` from declaration (line 2332) and only ever set inside the success branch. So in landmark mode, `nxt is None` ⟺ no successful candidate. The last-resort branch is structurally unreachable whenever `pick_cue_bounded_drum_loop` fails for every reachable candidate. T3 reproduction is plausible.
- **D3 mechanism, confirmed.** `report_landmark_candidates` (line 787-829) iterates only `track.musical_landmarks`. The consumer in `propose_arrangement.py` (`_final_landmark_candidates` at line 2041, fed into the report's `musical_landmark_candidates` field at line 2259-2262) iterates `alignment.landmark_candidates`, which is the output of `report_landmark_candidates`. So `section:*` candidates are completely absent from the report. Closing this is genuinely worth doing.
- **T2's "worked by coincidence" claim, supported.** T2 worked because Detlef's `break_2` happened to be the first reachable candidate with no closer competing landmark. Post-D1 fix, this is preserved (Detlef's `break_2` would still be picked) — but if some historical transition relied on a landmark winning over a section, T2's case study doesn't disprove that. The plan's "every case where the verdict changes gets human-read" rule is exactly the right discipline for this.

---

## Question 2: is Fix D1 safe?

**The plan's framing here is wrong.** It says: "Open question, flagged for review rather than assumed: is this safe across the existing corpus?" That treats it as an unsettled judgment call. It isn't.

The function's own docstring (line ~2208) says, verbatim: *"OUTGOING-OUTRO loop — loop the outro forward to REACH the incoming's next **section marker**."* That's the design contract. `landmark:*` entries are not in the contract — they're auto-detected kick-gap events that got swept into the same pool incidentally (the `candidate_roles` field on `musical_landmarks` lists `transition_boundary`, `automation_pivot`, `transition_end`, `incoming_ownership`, `bass_swap_candidate` — explicitly does **not** include `outro_loop_target`).

What the plan flags as an open question is actually already settled by the function's own docstring and the data model. The corpus replay is still essential as a regression net, but "should section-win be unconditional" is the wrong question — the right question is "does the corpus replay show any case where landmark-win was the right answer that we're now breaking?" That's an empirical question, not a design question.

**Refinement 1 (plan-level):** Rewrite Fix D1's open question. Drop the "is this the right default" framing; replace with "post-fix corpus replay must show zero cases where landmark-win was load-bearing for correctness." If the replay surfaces such a case, that's a new finding — not a refutation of the design.

**Refinement 2 (plan-level):** Add to "What this explicitly does NOT change" that the `locked_swap_gap` short-candidate logic (~2345-2353) and `loop_budget` filtering are unchanged by the partitioning. The current structure of the candidate loop is preserved; only the candidate set is partitioned into two passes. (Trivial but should be explicit so a reviewer doesn't have to infer it.)

---

## Question 3: is Fix D2 correct?

**Conceptually correct, but the plan underspecifies two things.**

a) **The plan only mentions tracking `candidate_nxt` (target bar). It should also track `candidate_target_name`.** Look at line 2422: `target_marker_name=target_name` and line 2428: `f"...to {target_name or 'marker'} {nxt:.0f}"`. When the fallback fires post-fix, the produced `FillCutSpec` needs both fields populated from the tracked candidate — otherwise the report's `loop_target_marker` field (set in `propose_arrangement.py:973`) will be empty, defeating half of what the fix is for.

b) **The plan doesn't say when exactly `candidate_nxt` gets set inside the loop.** The current loop has TWO gates before the chunk search is even attempted: loop_budget (`if candidate_gap > loop_budget + 1e-6: continue`) and the swap-gap short-candidate check (only fires AFTER chunk is found, at ~2345-2353). The plan's wording "swap-gap-satisfying" suggests both must pass — meaning `candidate_nxt` is set only when the candidate is BOTH within budget AND not flagged as short-swap. That's the right semantics, but should be made explicit, because if it isn't, a sloppy implementation could set `candidate_nxt` for a short-swap candidate and then propagate into the fallback with a target that should have been a hard failure.

c) **The "no candidate reachable at all" case is correctly not rescued — IF the fallback is gated on the tracked candidate.** Currently the fallback gates on `nxt is not None`. Post-fix, it must gate on `candidate_nxt is not None` (or `candidate_target_name is not None` if you prefer that as the sentinel). The plan implies this but doesn't say it. If a reviewer reads the plan and infers "fallback now fires whenever no chunk is found," they'll panic — three cases would be wrong: (i) no candidates exist at all, (ii) all candidates exceed loop_budget, (iii) all candidates are short-swap. All three correctly leave `loop_source: none` post-fix, but ONLY if the gate is on `candidate_nxt` not `nxt`.

**Refinement 3 (plan-level):** Spell out in Fix D2's body: "track `candidate_nxt` AND `candidate_target_name`; set them only inside the success-of-budget-and-not-short-swap region (i.e., right after the loop_budget filter passes, before `pick_cue_bounded_drum_loop` is called — NOT inside any failure/short-swap branches); gate the last-resort fallback on `candidate_nxt is not None` (not `nxt is not None`)." Three sentences, removes all ambiguity.

---

## Question 4: is the validation plan sufficient?

**Mostly yes, with three concrete gaps.**

a) **No negative-case test for the fallback firing.** Synthetic test #3 covers the positive case (every-candidate-fails-but-outro-passes → fallback fires). It doesn't cover the negative case (every-candidate-fails-and-outro-also-fails → fallback doesn't fire, AND the spec notes that). Post-fix, a regression in `_assess_loop_candidate` or in the gate condition could silently turn a `loop_source: none` into an unwanted `loop_source: outro` — and the existing test #3 wouldn't catch it because it doesn't run a case where the fallback would have failed.

b) **No test that the `i.sections` empty case is handled.** If the incoming track has no `sections` (rare but possible — e.g., a minimal track record), then `landmark_targets` has only `landmark:*` entries. Post-fix, this should behave identically to pre-fix. Not testing this leaves a regression vector for tracks with atypical section data.

c) **The full-corpus replay is correctly specified (human-read every changed verdict, not just count).** However, the plan doesn't reference WHERE the corpus lives or how to invoke the replay. This is the second project (Track-Release-Pipeline, presumably), and the plan should name it. Otherwise, the next person reading D15 in three months won't know how to execute validation step 2.

d) **Minor:** The plan doesn't address downstream JSON consumer schema for the D3 fix. Adding `section:*` entries to `musical_landmark_candidates` means those entries need fields analogous to landmark entries (`landmark_id` becomes `section_name`, `type` becomes `section_type`, `duration_beats` is `end_bar - start_bar` in beats). Anyone consuming the report's `musical_landmark_candidates` field assumes all entries have `landmark_id`. This should be flagged as "schema change, downstream consumers may need a docstring/field check."

**Refinement 4 (validation):** Add a fourth synthetic test: "all named candidates fail quality AND the outro-itself last-resort also fails quality → `loop_source: none`, no spec appended, but the new transparency note records that a target was sought." Also add a synthetic test for the empty-`i.sections` case (verify post-fix output equals pre-fix output byte-for-byte). Also reference the corpus-replay script by path.

**Refinement 5 (D3):** Schema change. Either name the new section-entry field schema explicitly (e.g., "section entries use `landmark_id` field as `section_name`, omit `type`/`section_name`/`candidate_roles` which don't apply") or call this out as a follow-up doc PR.

---

## Question 5: anything else structurally wrong?

A handful of smaller things, none of them fatal:

- **Other landmark policies.** `LANDMARK_POLICIES` (line 238) is `("paired_landmarks_v2", "tail_anchor_rescue_v1", "claude_decisions_v1")` — three policies, all routed through the same `landmark_mode` branch in `plan_fill_or_cut`. The fix touches all three. The plan only names `paired_landmarks_v2`. **Refinement 6:** add a line saying "applies to all three landmark policies, since they share this code path; `paired_landmarks_v2` is named because it's the production default, but validation should include at least one of the other two as a smoke test (probably `claude_decisions_v1` since `tail_anchor_rescue_v1` may have no current corpus representation)."

- **The shared-helper refactor in Fix D3 is the right instinct but should be a two-stage commit, not bundled.** Stage 1: extract the helper, prove byte-identical output across the corpus. Stage 2: apply D1's section-first preference via the helper. If you bundle them and the corpus diff shows unexpected changes, you can't tell whether the regression came from the refactor (mechanical) or the new logic (semantic). **Refinement 7:** say "D3 refactor is staged — helper extraction first, byte-identical proof, then logic change in a follow-up commit." Otherwise, debugging a corpus diff is a headache.

- **Risk-of-regression not stated explicitly.** Plan implies "this is a low-risk correctness fix to a documented bug," but doesn't say it. A one-line "upside if everything works: T1/T3 land correctly on real mix, full corpus byte-identical for 95%+ of transitions, the rest get verified by human-read diffs. Downside if it breaks: existing transitions that worked get worse targets or fail outright, requires revert; revert restores today's known-buggy state. Acceptable risk because today's state has a confirmed silent abandonment in T3-class cases, so any fix is net-positive if it doesn't introduce a new regression." would close the loop for Sam. Optional but recommended.

- **`_print_loop_rejections` output noise.** Post-fix, the last-resort fallback will run `_assess_loop_candidate` on T3's outro material; if that also fails, `_print_loop_rejections` will print rejection details. Pre-fix, this code path was unreachable in landmark mode. So logs will be slightly noisier. Not a bug, just a heads-up. Optional.

- **Plan is appropriately scoped.** Three concrete fixes, three small changes, clear success criteria for each. Not bloated.

---

## Summary of plan-level changes I want before code lands

In priority order:

1. **Rewrite Fix D1's "open question" framing.** The docstring already answers it. Commit to "section first, unconditionally" and make the corpus replay an empirical check, not a design check. (Refinement 1)
2. **Spelling out Fix D2's tracked values and gate condition.** Track both `candidate_nxt` AND `candidate_target_name`; set both only inside the budget-pass-and-not-short-swap region; gate the fallback on `candidate_nxt is not None`. (Refinement 3)
3. **Add a fourth synthetic test for the fallback-fails-itself case** (and a fifth for empty-`i.sections`). (Refinement 4)
4. **Reference the corpus-replay script by path** in validation step 2. (Refinement 4)
5. **Name the other two landmark policies** that the fix touches; add at least one as a smoke test. (Refinement 6)
6. **Two-stage the D3 refactor** — extract helper first, prove byte-identical, then apply logic. (Refinement 7)

The D3 schema question, the explicit-not-changed list, and the risk-of-regression paragraph are nice-to-haves but not gating.

Once those land, the plan is complete and ready to implement.

