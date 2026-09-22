VERDICT A: SOUND
VERDICT B: SOUND

## A) D4 — honest None-pass-through for missing camelot

**Confirmed in code:**
- `orchestrator.py:644` no longer fabricates `"1A"` — `track_dicts = [{"camelot": a.camelot, ...} for a in analyses]` lets `None` flow through. No remaining `or "1A"` literal anywhere (`grep` clean).
- `sequencer.py` `_edge_cost` returns `_W_UNKNOWN_KEY = _W_SMOOTH * 2 = 2000.0` when either side is None — this exactly equals the cost of a `power_mix` (score=2, `_W_SMOOTH * (4-2)`), i.e. the documented harmonic midpoint. The docstring says this on purpose: "letting BPM ordering decide among ties without the harmonic term taking a side." Neutral-cost choice is defensible — and it's measurably neutral, not 0 (which would be a quieter bug re-introducing the original "free identical match" failure).
- `sequencer.py` `_count_clashes` `continue`s on either-missing-camelot. **Invariant holds**: `apply_energy_arc` does `_count_clashes(proposed_dicts) > _count_clashes(tracks)`; both calls use the same exclusion rule so unknown-key pairs are silently absent from both sides of the comparison. The "did this ADD a clash" comparison stays self-consistent — confirmed by hand-trace and by the absence of any path where an unknown-key pair could inflate one count and not the other.
- No-regression pin `test_known_key_pool_byte_identical_to_pre_fix_behaviour` uses a fully-known pool where `_edge_cost` takes the `compatibility_score` path identically to pre-fix — the only behavioural change is in the None branch, so the regression surface is fully contained.

**Test design** (5 new tests): all five tests are well-shaped to distinguish pre-fix from post-fix behaviour — `test_edge_cost_unknown_key_is_neutral_not_a_fabricated_match` pins cost == `_W_UNKNOWN_KEY` and `< _W_CLASH` (pre-fix with the orchestrator's "1A" default would have either cost 0 for two-unknown or `_W_CLASH` for known/unknown, both wrong); `test_two_unknown_key_tracks_not_falsely_pulled_together` is the headline falsifier — pre-fix the unknown pair cost 0 (identical-key best case) vs known-smooth 1000, so the unknown pair would be preferred neighbours; post-fix they're 2000 vs 1000 and the smooth pair wins.

**Caveat on "proved-the-test via git stash":** cannot independently verify — workspace has no `.git/`. The tests themselves are the proof-the-test, and they're well-shaped for it; just flagging that the burn-list narrative claim is not auditable here.

**Minor (informational, not a finding):** if anyone bypasses the orchestrator and explicitly writes `{"camelot": "1A"}` into a track dict, `_edge_cost` will still treat `"1A"` as a real key (cost 0 against another `"1A"`), so the orchestrator fix is the load-bearing one and the sequencer fix is defense-in-depth that re-arms if a different writer reintroduces the substitution. Defensible — `_edge_cost` has no way to know "1A" is a sentinel.

## B) E8 — three schema-hardening fixes

### B.1 `_decision_names_match_exactly` cross-check (align_engine.py:2483–2487)

**Confirmed correct within scope:** the ambiguity gate `if (not _decision_names_match_exactly(track, wanted) and _decision_names_match(other, wanted))` only fires when `track` is a non-exact match AND `other` would also loosely match — exactly the radio-edit-vs-extended-mix shape. `test_ambiguous_prefix_match_is_refused_not_silently_accepted` constructs a `shared` string of length ≥30 (verified `len(shared) >= 30` assertion) so the pre-fix `b[:30]`/`a[:30]` rule couldn't disambiguate, and the post-fix rule correctly raises on the truncated `in_track` while still working when `in_track` is given exactly.

**Scope limitation, confirmed:** the reviewer note's stated limitation holds — `alignment_from_decision(o, i, decision)` only ever receives the two tracks for THIS pair, so it cannot detect ambiguity against a third track that shares the same prefix but lives elsewhere in the mix. A wider check would need to live at `compute_aligned_positions` (where `tracks` is in scope) or higher. The staged fix is the right scope for the call site it sits in, and the limitation is correctly stated as a limitation rather than disguised as solved.

### B.2 `_validate_decision_pair_indices` (propose_arrangement.py:1258–1279)

**Correctness:** the duplicate-detection idiom `duplicates = sorted({idx for idx in raw_indices if idx in seen or seen.add(idx)})` works because `set.add()` returns `None` (falsy) — first-occurrence hits the `or seen.add(idx)` branch, takes the falsy `None`, and is excluded from the comprehension; subsequent occurrences short-circuit on `idx in seen` (which is True thanks to the prior side-effect) and ARE included. Traced for `[1,2,2,3]` → `{2}`, `[1,1,1]` → `{1}`, `[]` → `{}`, `[3,3,1,2,2]` → `{2,3}`. All correct. Out-of-range check uses `range(1, n_tracks)` which matches `compute_aligned_positions`'s `for k in range(1, len(tracks))` — so "valid pair_index" matches "loop index that will be visited." ✓

**MINOR — readability, not correctness:** the `sorted({... or seen.add(idx)})` idiom is clever-but-easy-to-misread and depends on `set.add`'s `None` return being falsy. A reviewer six months from now could plausibly "fix" it into a bug. Consider rewriting as an explicit loop (4 lines, clearer intent):
```python
seen: set[int] = set()
duplicates: set[int] = set()
for idx in raw_indices:
    if idx in seen: duplicates.add(idx)
    seen.add(idx)
duplicates = sorted(duplicates)
```
Not a blocker — flagging only because the idiom will become a maintenance trap.

### B.3 `_decision_bar` epsilon of 1e-6 (align_engine.py:2496–2509)

**Epsilon is well-calibrated.** At musical bar magnitudes (<10^4 bars), IEEE-754 float precision is ≪ 1e-6 absolute — JSON round-trip of any integer-valued bar (180 → "180" → 180.0, or 180.0 → "180.0" → 180.0) lands at distance 0. JSON round-trip of an arithmetic result (e.g. 0.1+0.2 → "0.30000000000000004") lands at distance ~1e-16, far inside epsilon. The test `test_fractional_bar_within_floating_point_noise_is_tolerated` pins exactly this: `179.9999999999` (distance 1e-10) → snapped to 180. Conversely `180.5` → rejected (distance 0.5 ≫ 1e-6). At bar values above ~10^9 the epsilon would start false-positive-rejecting legitimate JSON round-trips, but realistic mixes never reach bar 10^9 — a 10-hour mix at 120 BPM is ~18000 bars, still 10^4 below the precision cliff.

**Minor:** `float(value)` doesn't guard against `inf`/`nan`/`str` — `round(float('inf'))` raises `OverflowError`, `float('nan')` propagates through with `abs(nan - round(nan)) > 1e-6` raising the expected ValueError (lucky) but `round(float('inf'))` leaks a non-domain error. Not a real-world risk — decisions are read from a controlled JSON file — and the same risk exists throughout the codebase. Skip.

## Summary

Both fixes are SOUND. No CORRECTIONs. Three MINOR notes: (1) the bypass-via-explicit-"1A" risk in `_edge_cost` is defense-in-depth, not a finding; (2) the clever duplicate-detection idiom in `_validate_decision_pair_indices` is correct but a maintenance trap — rewriting as an explicit loop would be a quality-of-life improvement, not a fix; (3) the `_decision_bar` epsilon is well-calibrated but doesn't guard `inf`/`nan` — not in scope for this burn list.

**Independent verification of "git stash" proof-the-test claims:** not possible in this workspace (no `.git/`), but the tests themselves are well-designed to distinguish pre-fix from post-fix behaviour — I confirm them by hand-trace, not by execution. Listed here for completeness, not as a finding.

===REVIEW-COMPLETE===

