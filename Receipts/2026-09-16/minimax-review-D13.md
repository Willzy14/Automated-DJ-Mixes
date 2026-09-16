Now I have all the information I need. Let me provide the review.

# REVIEW: D13 Claude-arranged mode

## 1. CODE CORRECTNESS

**alignment_from_decision (align_engine.py:2474-2530) and fills_from_decision (2532-2568)** are mostly well-defended but have several unguarded edges:

- **`swap_in_bar` is not validated against `i.n_bars`.** The check `handoff_out > o.n_bars + 1e-6` only bounds the swap against the outgoing. A `swap_in_bar > i.n_bars` (e.g. `swap_in=100` on a 50-bar incoming) passes silently and produces a swap that references source positions past the incoming's end. The legacy anchor search enforces this via `MIN/MAX_SWAP_PROGRESS` bounds; the decision path does not.

- **`swap_in_bar < 0` is not rejected.** Only `swap_in <= trim` is checked, so a negative `swap_in` with `intro_trim=0` (`0 <= 0` is False) produces a handoff earlier than the entry. No `ValueError`.

- **`swap_progress` is computed but never gated against `MIN_SWAP_PROGRESS=0.25` / `MAX_SWAP_PROGRESS=0.95`.** This is exercised in the real decisions file: **T5 (Pat Premier 80 bars, entry=64, trim=16, swap_in=31) produces progress=31/32 = 0.97, which exceeds MAX_SWAP_PROGRESS**. The legacy path would refuse; the decision path accepts. Whether this is by design (decisions override progress) is undocumented in the doc but consistent with the build's behaviour.

- **Fractional `entry_out_bar` / `swap_in_bar` would propagate as fractional bar/beat values throughout** (no rounding in `alignment_from_decision`, and downstream `_plan_marker_loops` uses `float()` arithmetic). This is benign at the plan level but could surprise downstream automation math.

- **`_decision_names_match` is loose**: `a.startswith(b[:30]) or b.startswith(a[:30])`. A 30-char prefix collision silently matches the wrong track — the function claims equality on the first 30 chars. The legacy `_resolve_stem_key` defends against prefix ambiguity by requiring exactly one match; `_decision_names_match` does not.

- **Edge cases the staged tests don't cover** (and should): `swap_in_bar < 0`, `swap_in_bar > i.n_bars`, fractional values, `pair_index` collisions (silently overwrite), `pair_index=0` or > len(tracks) (silently ignored), and progress bounds.

**apply_loops.cut_named_clip_front_and_pull (1048-1086) is solid.** Refuses `cut_beats <= 0`, missing clip, and `cut_beats >= clip length`. The `T + 0.01` threshold in `shift_clips_from_beat` keeps the cut clip itself stationary. The ALS test (`test_als_front_cut_moves_source_not_position_and_pulls_the_rest_in`) confirms `LoopStart` moves from 496 → 536 (+40), `CurrentEnd` 576 → 536 (-40), `LoopEnd` unchanged, and `outro_1`'s `Time` 576 → 536 (-40). This is consistent with the propose_arrangement `_plan_marker_loops` operation on `source_start_beats` (line988: `+cut_beats`).

**(Earlier I misread the test_arrangement_decisions.py fixture for `test_front_cut_shortens_the_clip_pulls_later_clips_in_and_records_the_als_edit` as having `src=576.0`. On re-read, the fixture is `_section("drop_5", "drop", 496.0, 576.0, 496.0)` — `src=496.0`, fresh-clip convention. That fixture is consistent:496 + 40 = 536 matches the assertion. Retracting that earlier concern.)**

## 2. WRITE-UP vs RAW DATA

The write-up has three factual errors against `phase3c_claude_arranged_vs_sam_tweaks_dryrun.txt`:

**T1 — "same total bars" claim is FALSE.**
The table says: "tail loop shape only: 2bx7 decided vs Sam's 1bx16 (same total bars)". The dry-run's `tail_loop_changed:2bx7+0b->1bx16+0b` is **14 bars vs 16 bars, NOT the same**. The geometry line `tail after swap 32->34` confirms Sam has 2 more bars of effective tail (50 vs 48 total overlap). The "(same total bars)" parenthetical is unsupported by the dry-run and should be deleted.

**T5, T6, T9 — sneak values are REVERSED.**
The dry-run convention is `Claude->Sam`:
- T5: `sneak_changed:0.1->0.1194` → Claude=0.10, Sam=0.12. Write-up says "sneak 0.12 decided vs 0.10 Sam" (swapped).
- T6: `sneak_changed:0.2->0.15` → Claude=0.20, Sam=0.15. Write-up says "sneak 0.15 decided vs 0.20 Sam" (swapped).
- T9: `sneak_changed:0.2->0.15` → Claude=0.20, Sam=0.15. Write-up says "sneak 0.15 decided vs 0.20 Sam" (swapped).

Three transitions, same reversal. The dry-run's `Claude->Sam` ordering matches every other field (entry, swap, overlap, tail loops), so the sneak line should be read the same way. The write-up should read "0.10 decided vs 0.12 Sam" (T5), "0.20 decided vs 0.15 Sam" (T6/T9).

**T2, T3, T10, T4, T7, T8, T11 — verified clean against the dry-run.** T7's "entry +1 bar, swap -1 bar" matches `entry_moved_out:+1bars` + `swap_moved_in:-1bars` and the `entry 106->107, swap 9->8` numbers. T7's sneak "0.10 decided vs 0.53 Sam" matches `sneak_changed:0.1->0.5302` (Claude0.10, Sam 0.5302 ≈ 0.53).

**Headline numbers** — "10 of 11 within 1 bar" checks out: T4 is the structural-difference exception (swap removed), the other 10 are within 1 bar (T7 is +1/-1 with handoff same). "5 of 11 exact-to-the-beat" is ambiguous in interpretation; under the strictest "byte-identical entry+swap+handoff AND no tail-loop difference AND no sneak change", it yields T2, T10 only (2). Under "byte-identical entry+swap+handoff regardless of tail loops or sneak", it yields T1, T2, T3, T5, T6, T8, T9, T10, T11 (9). The number5 fits no obvious definition; it should either be re-stated or removed.

## 3. DOC vs CODE

`mix_md_new_section.md` is largely accurate against `alignment_from_decision` + `fills_from_decision`:

- `pair_index` (required at the propose_arrangement.py loader via `int(d["pair_index"])`) — doc says required ✓
- `out_track` / `in_track` (optional; checked if given) — doc says optional ✓
- `entry_out_bar` (required via `decision["entry_out_bar"]`) — doc says required ✓
- `intro_trim_bars` (optional, defaults to 0 via `or 0.0`) — doc says optional, default 0 ✓
- `swap_in_bar` (required; must be `> trim`) — doc says required, must be after `intro_trim_bars` ✓
- `swap_cue` / `out_cue` (optional, embedded in `handoff_kind` and `paired_cues`) — doc says optional, cosmetic only ✓
- `reason` (optional, ends up in `notes`) — doc says optional ✓
- `tail_loop {source_start_bar, source_end_bar, reps, partial_bars, target}` — doc lists all 5 fields; **doc doesn't distinguish required vs optional in the nested dict**. Code requires `source_start_bar`, `source_end_bar`, `reps` (no `.get` defaulting); `partial_bars` defaults to 0; `target` defaults to `'tail'`.
- `outgoing_cut {clip, cut_bars}` — doc lists both; code requires both, validates `cut_bars > 0` ✓
- `outro_skip {clip, skip_bars, keep_end_bars}` — doc lists all three; code requires all three, validates `skip_bars > 0` AND `keep_end_bars > 0` ✓

Minor doc improvements:
- `tail_loop.source_end_bar` must be `> source_start_bar` (code enforces; not in doc).
- `cut_bars` / `skip_bars` / `keep_end_bars` must be `> 0` (code enforces; not stated in doc).
- The doc's "Phase 3 (below) runs unchanged against a Claude-arranged output" sentence is technically correct.

The "What it cannot express" section (levels + no-swap crossfade) matches the raw dry-run's classification (T4's `swap_removed` and the sneak deltas on T5/T6/T7/T9).

## Other findings (unasked)

- **T5 progress=0.97** in the decisions file violates `MAX_SWAP_PROGRESS=0.95` and the legacy path would have rejected it. The decision path accepts. The run was apparently successful, so this is by design (decisions override the progress gate). This should be documented explicitly in `mix_md_new_section.md`'s "What it cannot express" section (or somewhere) — currently a silent override.
- The write-up's "5 of 11 exact-to-the-beat, on a mix where the RULE-BASED pipeline (V1/V2) differed from Sam on all 11" appears in a paragraph about mode fidelity. Given the write-up's table marks 6 transitions as "exact" and3 as "exact bars" (T5/T6/T9), the "5" is unmoored from the table and should be reconciled.
- The dry-run's "Correct: 0/11" is correctly explained by the write-up (automation point counts, not geometry). No defect.
- The headline phrase "10 of 11 transitions land within 1 bar" is consistent with the data (T4 is the one exception, with structural-difference classification `swap_removed`).

VERDICT: CORRECTION write-up has1 false claim ("same total bars" on T1) and 3 reversed sneak values (T5, T6, T9) versus phase3c dry-run; doc has minor nested-field optionality omissions but no factual errors; code has unguarded `swap_in_bar<0`, `swap_in_bar>i.n_bars`, fractional values, and progress-bounds edges the staged tests don't cover (T5 in the real decisions file actually exercises the progress-bounds gap at 0.97 > 0.95).


===MINIMAX-ASK-DONE exit=0 session=pi nonce=6a7a1327349a4ab38bcca3727f824e5c===
