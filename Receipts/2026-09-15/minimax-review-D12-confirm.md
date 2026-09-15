VERDICT: SOUND

Q1 — `_pinned_swap_event` reproduces `_find_bass_swap_beat` (learn_from_correction.py:752). Confirmed by code inspection and tracing `test_reliability_is_judged_on_the_delta_event_not_a_second_finder`: both iterate arrangement order, apply the same `_scope_points` filter (margin=40), the same `_source_beat(t, clips, origin)` mapping (same clips/origin used at call site line 1137), the same source window [src_lo-10, src_hi+10], and the same `v < 0.8` test. For c the trace gives pinned=(1085, 85) and `_find_bass_swap_beat` returns 85 — identical source beat.

Q2 — Falling-edge rule in `_find_swap_arr` (learn_from_correction.py:728). For the standard DJ automation representation `(t, 1.0), (t, 0.18)` the kill point's prev is always 1.0 ≥ 0.8, so the kill is caught; verified against T6 (2932→2932 fall) and T9 (4212→4212 fall). MINOR edge case: a kill at the first point in the window (prev=None) is missed. Falsifier: `out_bass=[(80, 0.18)]` with overlap window [70,110] → returns None instead of 80. Mitigation: `_pinned_swap_event` reproduces the source-space finder and still finds 80; the reliability gate then flags `swap_arr=None` as unreliable (`_delta_event_reliable` line 776 returns False when `true_kill_arr is None`). Cite `_find_swap_arr` line 728.

Q3 — `_repeat_groups` union + tail-repeat gate (learn_from_correction.py:632). Traced three new tests:
- `test_bridge_from_a_skipped_gap_is_not_a_loop`: bridge_1 source [300, 340] vs `_merge` of played = [(0,256),(500,700)]; `_covered` correctly returns False (300 < 500-0.01), no loop. Old [min,max] check would have falsely detected a loop here — the correction is the documented intent.
- `test_one_off_repeat_of_the_previous_clips_tail_is_a_loop`: c1=672, p1=672 → `tail_repeat=True`, reps=1, group emitted as "1bx1+0b".
- `test_one_off_revisit_of_earlier_material_is_an_edit_not_a_loop`: c1=48, p1=256 → `tail_repeat=False`, reps<2 → skipped, no group.

Q4 — Existing field semantics. `_sides` is added at line 925 and popped at line 1045 before the entry is written (`diff_to_jsonl_entry` consumes `td.geometry`); no leakage. `bass_swap_reliable` semantics tighten (now also requires finders agree within 1 beat AND not on loop), but it's computed at write time only — existing pair_history entries are not re-evaluated and remain valid.

FOUND UNASKED:

- MINOR: `_find_swap_arr` prev tracking (line 728) carries across the window boundary, so a kill with prev<0.8 outside the window can miss a real fall inside. Falsifier: `out_bass=[(50, 0.5), (90, 0.18)]`, window [80,110] → prev=0.5 inside, no fall, returns None. Mitigation is the same as Q2 — `_pinned_swap_event` finds the source-space kill, gate flags `swap_arr=None` as unreliable. Cite `_find_swap_arr` line 728.

- MINOR: `_find_swap_arr` prev=None on the first in-window point (line 731). Falsifier as in Q2. Same mitigation.

- INVARIANT: all 12 pre-existing record keys at lines 926–937 retain name, order, and value semantics. Cite `_geometry_diff` line 925.

- INVARIANT: `_pinned_swap_event` cannot diverge from `_find_bass_swap_beat` in source-space selection — both use the same iteration order (arrangement) and the same predicate chain. The two can only disagree on whether the kill is a "falling edge" (arrangement-space concept) vs any low point (source-space concept); that disagreement is the divergence the gate is designed to catch.

- INVARIANT: test count is 15 (matches brief). 4 new tests cover the corrections (`test_bridge_from_a_skipped_gap_is_not_a_loop`, `test_one_off_repeat_of_the_previous_clips_tail_is_a_loop`, `test_one_off_revisit_of_earlier_material_is_an_edit_not_a_loop`, `test_reliability_is_judged_on_the_delta_event_not_a_second_finder`).

===REVIEW-COMPLETE===


===MINIMAX-ASK-DONE exit=0 session=pi nonce=601a248648f74b08825f21e0ca4d0be9===
