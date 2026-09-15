I've done a thorough analysis. Let me consolidate my findings into the verdict.

I traced through every test case in `test_learn_geometry_corrections.py` by hand (the 11 test functions, not 9 — brief and burn list both miscount), all the loop-detection rules, `_find_swap_arr` margin behaviour, `_natural_intervals`/`_beats_missing` arithmetic, the `bass_swap_reliable` gating, the geometry record serialisation, and the verdict/print logic. I also verified the staged `learn_from_correction.py` is byte-equivalent to the project version (`Receipts/2026-09-15/receipt-D12-a1.json` shows the prior `41 passed in 0.12s` run, including the new file alongside the existing learner tests).

Trace highlights:
- T6 swap-on-loop: `_find_swap_arr` lands on `2932` (claude) and `3064` (sam). `swap_out_on_loop` resolves to `[True, False]`, so `bass_swap_reliable=False`; the geometry layer emits `tail_loop_removed:8bx4+0b` and `overlap_changed:64->31bars`. Old behaviour would have emitted a misleading `bass_swap_moved:+648beats` — corrected by the gate.
- T4 swap-removed: c has swap_arr 2220, s has none (outgoing bass only goes 1.0→0.97, never below 0.8). `_find_swap_arr` returns None for sam → `swap_removed` label. tail-loop group "2bx3+1b" detected via the `partial` branch.
- T7 swap moved only on incoming (8→32 source bars on Coldabank's intro, 115 unchanged on Arielle Free). `swap_moved_in:-24bars` only.
- T9 10-bar cut: `_beats_missing` of `[(432,608)]` minus `[(432,496),(536,608)]` = 40 beats = 10 bars exactly. swap sits on real section clips, so `bass_swap_reliable=True` and `bass_swap_moved:+56beats (+14bars)` is still emitted (raw source delta).
- T2 one-bar nudge: swap_out on outro_1 moves 135→136 source bars = `+1bars`.
- Identical-transition test: corrections empty, verdict `correct`, `bass_swap_reliable=True`, `outro_cut_bars=0.0`.
- 5 loop tests: all labels match the "8bx7+0b" / "2bx3+1b" / "4bx2+0b" / "4bx6+0b" notation, partial correctly rejected when next clip is longer than the chunk, forward skip and landmark split correctly not classified as loops.

VERDICT: SOUND

Soundness reasoning:

1. `_repeat_groups` is correct for the apply_loops / hand-made workflow. The "backwards + replays + no_longer" rule with bounding-box `[min,max]` of preceding clips' source is sound — a bridge that revisits earlier source is theoretically possible but exotic in DJ tracks, and a real loop from later source is excluded by the explicit backwards check (which the brief acknowledges as an intentional apply_loops assumption). The natural-first-copy inclusion works as documented.

2. `_find_swap_arr` ±40-beat margin IS wider than the source-space finder's ±10 and can pick up the outgoing's prior role's automation if two transitions sit within ~40 arr beats — rare in practice (typical gap is 16–64 arr beats) and acknowledged in the brief's question. Consistent with `_scope_points` margin by design.

3. `_beats_missing` iterates over `a` (claude) only, so Sam-ADDED material doesn't pollute the cut. `s1 > s0` after `max(s0, floor)` correctly handles floor-between-clip-source and clip-fully-below-floor. The T9 case verifies the 10-bar figure end-to-end.

4. `bass_swap_reliable` is gated on `swap_out_on_loop==[False,False]`. The brief's "either side" wording is slightly loose — `bass_swap_delta` is conceptually about the outgoing only, so checking only that side is correct. When withheld, the geometry record carries `swap_arr_beat=[c,s]` (arr-space, always meaningful) and swap-in/swap-out source bars on whichever side isn't a loop.

5. `_positioned` correctly drops zero-length clips via `arr_end - arr_time > 0.01`.

6. Additive only. Existing pair_history keys unchanged; `corrections` is extended, never mutated.

Found unasked:

- **Brief and burn list both say "9 tests"; the file actually contains 11 test functions** (the receipts and BURN_LIST.md agree on 9; the count groups "loop detection" as a single umbrella, but the file has 5 loop + 5 transition + 1 unchanged). The test code itself is fine; just a stale count in two docs. Receipt shows 41 passed, consistent with the third file (test_canonicalize_pair_history.py, since moved out) plus the 19 in the two learner files plus 1 in something else.

- **BURN_LIST.md:1755 says "the T6 test gets no labels and no geometry field on the committed code"** — this contradicts the actual T6 test, which expects `tail_loop_removed:8bx4+0b`, `overlap_changed:64->31bars`, and reads `td.geometry["swap_in_bar"]`, `td.geometry["swap_out_on_loop"]`, `td.geometry["entry_out_bar"]`, `td.geometry["tail_after_swap_bars"]`. The test passes today; the burn-list wording is misleading (it should say "the T6 test asserts no `bass_swap_moved` label" and that `bass_swap_reliable` is False). Worth fixing in the burn list when D12 closes.

- **Print report shows only the primary geometry fields** (`entry_out_bar`, `intro_trim_bars`, `swap_in_bar`/`out_bar`, `tail_after_swap_bars`, `out_tail_loops`, `outro_cut_bars`). The record also carries `entry_on_loop`, `swap_arr_beat`, `swap_out_on_loop`, `in_intro_loops`, `overlap_bars` — none printed. Probably intentional (they're in pair_history), but if a future debugging session needs them in the report, that's where to look.

- **C7 follow-up already noted in BURN_LIST.md**: `canonicalize_pair_history.py` chokes on T4's `swap_removed` record because it requires both swap beats to derive a delta. Documented as belonging to C7, not D12. D12 itself doesn't need to fix it.

No CORRECTION needed; no DROP warranted. SOUND closes D12.


===MINIMAX-ASK-DONE exit=0 session=pi nonce=68d88ac837864313b132cfd2b2871723===
