I've now traced the full control flow. Here's my verdict.

# Peer review: D15 implementation**Verdict: SOUND.**

The implementation correctly realizes the plan as revised after the dual review. I traced every flagged concern against the actual code, ran each of the 6 new tests mentally against the pre-fix logic to confirm they exercise what they claim, and verified both `ValueError` sites in the fallback path are correctly distinguished from the primary path's unreachable-by-construction raise.

## 1. Does the actual code match what the plan specified?

Yes, all four sub-fixes match the revised plan.

**D1 two-tier loop (`Source/align_engine.py:2413-2491`)**
- The implementation uses `for tier in (sorted(set(section_targets)), sorted(set(landmark_targets))):` followed by an inner `for target_source_bar, candidate_name in tier:`. Functionally equivalent to a single re-sorted list with key `(0 if section else 1, bar)` — within each tier candidates are bar-ascending, and the `chunk is not None` outer-loop break ensures the first successful chunk wins.
- `short_swap_candidate` is initialised before the tier loop (line 2457) and the `ValueError` check happens after both tiers (line 2490). This is the specific scope the Claude subagent's review flagged as mandatory — splitting into two independent loops each with their own post-loop check would have let a sections-only pass raise before the landmarks tier or the D2 fallback could fire.

**D2 `candidate_nxt`/`candidate_target_name` tracking (`Source/align_engine.py:2442-2450`)**
- Set on the first candidate passing BOTH `candidate_gap <= loop_budget + 1e-6` AND `candidate_gap + 1e-6 >= locked_swap_gap`. Matches the plan's "first candidate that would be viable if a clean chunk existed" — `pick_cue_bounded_drum_loop`'s success is not consulted here. The `nxt is None and outro is not None and candidate_nxt is not None` D2 transfer (line 2496) correctly reuses `nxt`/`target_name` so every downstream line (gap math, the `FillCutSpec` `target_marker_name` field) is unchanged code, just newly reachable.

**D2b dedicated field (`Source/align_engine.py:787-796`, `Source/align_engine.py:2608-2612`, `Source/propose_arrangement.py:2271`)**
- Dedicated `dict | None = None` field on `Alignment`, with a docstring that records the real reason a dedicated field was chosen over routing through `al.notes` (the existing "suppressed" forwarding would silently swallow the note — exactly the bug the plan review caught). Set only when `candidate_nxt is not None and not any(s.kind == "outgoing_tail" for s in specs)`, so it's never recorded when a loop was actually produced. Threaded into the JSON report via `t["outgoing_loop_abandoned"] = al.outgoing_loop_abandoned`.

**D3 section entries + selected-tagging (`Source/align_engine.py:837-871`, `Source/propose_arrangement.py:2040-2056`, `Source/propose_arrangement.py:2121-2123`)**
- `report_landmark_candidates` emits `section:{name}` entries with `type: "section"` from `incoming.sections`. The naming convention exactly matches `_outro_section_target_candidates` (line 2232), so `_candidate_matches_target` correlates by direct string equality (`landmark_id == target_marker`). Landmark entries correlate via `f"landmark:{landmark_id}:end" == target_marker` — both formats are produced by `_outro_section_target_candidates` / `_outro_landmark_target_candidates` respectively (lines 2232, 2248). The intro-loop target `"section:last_drop"` (set at `Source/propose_arrangement.py:1084`) deliberately doesn't match any incoming candidate's `landmark_id` — exactly the explicit scoping the plan review demanded.

## 2. Is the `via_d2_fallback` fix actually correct?

Yes, and I traced both sites against the code.

**Primary path's `raise` is unreachable by construction.** `pick_cue_bounded_drum_loop` (lines 2048-2056) pre-filters lengths: `gap_bars % length == 0` (no `required_boundary_bars`) OR `required_boundary_bars % length == 0 AND remainder <= 1` (with `required_boundary_bars`). Combined with `repeats <= policy.max_loop_repeats` and `repeats >= 1`, any chunk it returns has a clen where `natural_reps * clen + remainder = gap`. In the consumer (lines 2559-2565), `reps = min(natural_reps, max_loop_repeats, loop_budget // clen)` cannot reduce `natural_reps` from the `max_loop_repeats` side (pre-filter caps it) or from the `loop_budget // clen` side (since `gap <= loop_budget` is enforced at line 2433 before reaching `pick_cue_bounded_drum_loop`). So `used + partial = gap` exactly. The raise at line 2567 is unreachable for primary.

**Fallback path's `raise` is reachable.** `pick_clean_drum_loop` (lines 1969-2017) returns chunks of length `pref` (4 or 2) WITHOUT the pre-filter — no `gap_bars % length == 0` check, no `repeats <= max_loop_repeats` check. The "loop the outro section itself" mechanism (lines 2546-2560) also uses `pref` directly. With `pref=4, gap=30, max_loop_repeats=8, loop_budget=24`: `reps = min(7, 8, 6) = 6`, `used = 24`, `partial = min(0, 0) = 0`, `used + partial = 24 < 30 = gap`. This is exactly the scenario the corpus replay caught in 7 real transitions.

**Both `ValueError` sites are guarded by `if not via_d2_fallback:` then `chunk = None`:**
- Line 2567 (`abs(used + partial - gap) > 1e-6`) — guarded.
- Line 2581 (`outro_start + used + partial < handoff_bar_out`) — guarded.

Primary path: `via_d2_fallback = False` (never set True outside the transfer block at line 2524). Both guards evaluate as `True not False = True`, raise preserved. But unreachable in practice — verified.
Fallback path: `via_d2_fallback = True` (set at line 2524 when D2 transfer fires). Both guards evaluate as `True not True = False`, raise suppressed, `chunk = None`. Falls through to the abandonment check at line 2608, which sets `outgoing_loop_abandoned` since no `outgoing_tail` spec was produced. Correct.

The `short_swap_candidate` raise at line 2490 (the existing primary failure mode) is NOT guarded by `via_d2_fallback`. This matches the plan's explicit precedence: "a short-swap-only situation still raises as today". When a primary chunk was found but too short, all later candidates also failed → primary is responsible, raise is the right user-facing signal.

## 3. Any NEW defect this implementation introduces?

None that the plan review couldn't have caught. The three specific concerns the reviewer flagged:

**(a) Two-tier loop's interaction with `short_swap_candidate` across tiers:** ✓ Scoped correctly. Single accumulator initialised before the tier loop (line 2457), modified inside the inner loop (line 2465), checked after both tiers (line 2490). The `continue` after setting `short_swap_candidate` correctly keeps the inner loop iterating to find a later viable candidate.

**(b) Whether `candidate_nxt` tracking could pick a DIFFERENT candidate than primary:** It can — e.g., A is viable and gets `candidate_nxt = A`, but A's `pick_cue_bounded_drum_loop` returns None and B's succeeds → primary picks B, `candidate_nxt` stays at A. This divergence only matters when D2 fires (chunk is None at end of tier loop), in which case all primaries failed and `candidate_nxt`'s "first reachable" is the correct target by plan design. The plan explicitly accepts this.

**(c) `_final_landmark_candidates` selected-tagging correlation:** ✓ Both formats work correctly. Section entries: `landmark_id == target_marker` where both are `section:{name}`. Landmark entries: `f"landmark:{landmark_id}:end" == target_marker` where `target_marker` is `landmark:{raw_id}:end`. `track_role != "incoming"` filter correctly skips outgoing candidates. The `not target_marker` early-return correctly handles `None` and `""` (intro-loop's `"section:last_drop"` doesn't match any candidate's `landmark_id`, so those remain `selected: False`, which is the explicit scoping per the plan review).

## 4. Are the 6 new synthetic tests actually testing what they claim?

Mixed but appropriate. Tests 1, 3, 4, 6 are bug regression tests (would fail against pre-fix). Tests 2 and 5 are regression guards (would also pass pre-fix, but exercise real edge cases the fix could plausibly have broken).

| # | Test | Pre-fix outcome | Post-fix outcome | Classification |
|---|---|---|---|---|
| 1 | section-preferred-over-nearer-landmark | landmark wins (bar 24 < 28) | section wins | ✓ bug regression |
| 2 | landmark-still-used-when-no-section-reachable | landmark wins (section filtered by gap) | landmark wins | regression guard (ensures fix is preference, not removal) |
| 3 | fallback-fires-when-every-candidate-fails | no tail (nxt never set) | tail via D2 | ✓ bug regression |
| 4 | no-loop-when-fallback-fails-and-reason-recorded | `outgoing_loop_abandoned` field doesn't exist | field set | ✓ bug regression + D2b feature |
| 5 | empty-incoming-sections-falls-through | landmark wins (same) | landmark wins | regression guard (empty-sections edge case) |
| 6 | report-includes-named-sections | 1 entry (landmark only) | 2 entries (landmark + section) | ✓ bug regression |

Tests 2 and 5 are valid as regression guards — they pin the fix's "preference not removal" behaviour and the empty-sections edge case respectively, both of which a poorly-thought-through implementation could plausibly have broken. They're not "trivial" — they exercise real code paths with real data shapes.

## 5. Anything else wrong, missing, or worth flagging?

**Existing test update** (`Tests/test_arrangement_safety.py:507-561`): correctly reflects the new intentional shape — expects 3 candidates = 1 landmark + 2 sections (drop_1, outro_1 from `_track`'s default sections). All section-coordinate assertions match the post-fix `report_landmark_candidates` math (`incoming_start_beat + start_bar * 4`, etc.). Comment block explains the count change. ✓

**Non-landmark mode is preserved byte-identically.** Verified: `else: nxt = next((arr + s["start_bar"] for s in i.sections if (arr + s["start_bar"]) > o.n_bars + 1), None)` (line 2405) is the original logic. `if landmark_mode and outro is not None:` (line 2432) gates the entire tier-loop machinery. `candidate_nxt is None and outro is not None and candidate_nxt is not None:` (line 2496) is False in non-landmark mode (candidate_nxt is None). `via_d2_fallback = False` (line 2454) by default. ✓

**One minor observation, not a defect:** in the D2 fallback success path, the `FillCutSpec.target_marker_name` reports the FIRST viable target (e.g., A's name) even when the actual loop geometry uses a different (later) `nxt`. This is by plan design ("Reuses nxt/target_name so every downstream line is unchanged code") — the named target IS the target, the audio material just comes from a different window. The naming is honest: the loop is targeting that named boundary; what part of the audio it uses to get there is implementation detail. Worth noting if Sam ever wants to distinguish "loop aimed at X, audio sourced from Y", but not a defect.

**Plan-staging deviation, honestly disclosed:** the plan asked for two commits (helper extraction proven byte-identical, then logic change). Implemented as one continuous pass. The corpus replay achieved the same differentiation via the 215-changed-pair read-through rather than git history. This is a process deviation, not a code defect — the per-pair categorization (82 rescued + 122 cosmetic renames + 11 genuine target changes,0 regressions) is more thorough than the staged-commit approach would have produced. Noted for the record.

**Pending real-data work** (not in scope for this code-level review, flagged for completeness): Sam's by-ear check on the re-bounced mix, and the ear-check on transitions where the now-reachable last-resort fallback newly fires (it "may carry bass" per its own comment). The code's transparency on this is correct — the `_note_unmeasured_acceptance` instrumentation is preserved, and `outgoing_loop_abandoned` distinguishes "no loop needed" from "tried but failed" in the report.

---

**Final verdict: SOUND. Ready to mark D15 done at the code level pending the by-ear check that's outside this review's scope.**

