I've completed the review. Here's what I found across the three dimensions the brief asks for.

---

## REVIEW FINDINGS

### 1. CORRECTNESS of `shadow_swap_preference` and its wiring — SOUND

**Similarity math, identical to `find_similar_pairs`:**
- BPM score: `(0.0 if bpm_diff > BPM_MATCH_TOLERANCE else 1.0 - bpm_diff/BPM_MATCH_TOLERANCE)` — same shape in both
- struct_score: `max(0.0, 1.0 - (out_dist + in_dist) / 30.0)` — byte-identical
- weights: 0.3 BPM + 0.7 structure — identical
- threshold: 0.1 default in both (`min_similarity=0.1` here, hardcoded `0.1` in `find_similar_pairs`)

One micro-divergence, not a bug: `shadow_swap_preference` does `if pair.bpm_out is None: bpm_score = 0.0`, while `find_similar_pairs` falls through `pair.get("bpm_out", 0)` → bpm_diff=128 > tolerance → score 0. Identical outcome, two paths.

**`exclude_project`:** Simple `if pair.project == exclude_project: continue` filter inside the scoring loop — excludes every pair from that project, no off-by-one or late-filter risk.

**"Report-only" guarantee, confirmed by full read of `propose_arrangement.py`:** Only two references to `shadow_swap_preference` exist in the entire file:
1. The assignment site (`analysis.shadow_swap_preference = shadow_swap_preference(...)`)
2. `generate_report`'s `if ov.shadow_swap_preference: t["shadow_swap_preference"] = ov.shadow_swap_preference` (write to JSON)

`validate_arrangement_plan`, `apply_playback_policy`, `compute_aligned_positions` (align_engine), the `arrange_overlap` path, `_plan_loop_extensions`, `_plan_marker_loops` — none read `shadow_swap_preference`. The field is not used by `align_pair` or `_search_anchors` (those don't exist in this codebase by those names; the real alignment lives in `align_engine.compute_aligned_positions`, which never sees the field).

**Test quality:** The 6 `shadow_preference` tests in `test_canonicalize_pair_history.py` discriminate real bugs rather than restate the implementation:
- `test_shadow_preference_weights_by_similarity_hand_verified` (line 375): hand-computes `(1.0*10 + 0.7*20)/(1.0+0.7) = 14.118` and asserts `≈14.1` — would catch any off-by-one in the weighting or weighted-average formula
- `test_shadow_preference_excludes_itself_out_of_existence_returns_none` (line 401): exercises the lone-project case, would catch an `exclude_project` bug that left self-pairs in
- `test_shadow_preference_below_similarity_threshold_returns_none` (line 409): with a known-modest similarity of 0.56 below a strict 0.6 cutoff — catches a missing threshold check
- `test_shadow_preference_caps_at_max_results` (line 423): catches a `max_results` ignored bug

### 2. CORRECTNESS of evaluation harness — SOUND

**Leave-one-project-out actually enforced:**
```python
prediction = shadow_swap_preference(
    held_out.bpm_out or 128.0, ...,
    canonical_pairs, exclude_project=held_out.project,
)
```
Every iteration passes `held_out.project` as `exclude_project`, and `shadow_swap_preference`'s filter is the FIRST thing the inner loop does — no indirect leakage possible.

**Sign and tolerance correct:**
- `abs(shadow_delta - held_out.delta_beats) <= tolerance_beats` — `DELTA_TOLERANCE_BEATS = 4.0`, matching the canonicaliser's own tolerance.
- `abs(0.0 - held_out.delta_beats) <= tolerance_beats` for baseline.
- Sign is `prediction - truth`, which is what "error magnitude" means. Correct.

**Baseline fairness:** Both predictors are scored on the **same** set of pairs (`covered_pairs=31/31`, so no coverage gap to exploit). The "predict zero" baseline wins because ~70% of real corrections ARE zero-delta — that's a genuine property of the corpus, not an unfair advantage. The script even explains this in its own docstring and prints an `UNDERPOWERED WARNING` for the corpus size.

**Three spot-checks against the raw `pair_history.jsonl`:**

| Eval row | claimed true_delta | raw fields in pair_history.jsonl | arithmetic |
|---|---|---|---|
| `Black Book x Defected V2 / pair 9` | `-32.0` | claude=5296, sam=5264 | 5264−5296 = **−32** ✓ |
| `02.09.26 House 10 / pair 5` | `32.0` | claude=480.0, sam=512.0 | 512−480 = **+32** ✓ |
| `15.09.26 August Releases Mix / pair 7` | `-0.050420152763763326` | claude=460.0, sam=459.94957984723624 | 459.949579...−460.0 = **−0.050420152763763326** ✓ (full precision) |

**Conflict exclusion verified:** The eval has 31 rows = 31 canonical pairs. Black Book pairs 3, 4, 5, 7 are the 4 known conflicts from the module docstring (v21_v22_initial vs auto_diff disagree on either delta or verdict, spread > 4.0 beats) — none appear in the eval rows. `Black Book x Defected V2` total = 5 (pairs 1, 2, 6, 8, 9), matching by_project.

**Internal consistency test:** `test_real_corpus_evaluation_runs_and_every_row_is_internally_consistent` rebuilds `shadow_within_tolerance` and `baseline_within_tolerance` from raw fields per row, catches a class of bug where the flags and the underlying deltas drift apart.

### 3. BURN LIST WRITE-UP vs REALITY — MOSTLY ACCURATE, ONE DEFECT

| Claim | Reality | Verdict |
|---|---|---|
| "31 canonical pairs" | `summary.total_pairs = 31` in JSON; matches expected from real corpus (5 BB + 9 H10 + 7 TH + 10 AugRel = 31, after the 4 Black Book conflicts are excluded) | ✓ |
| "shadow hit rate 19% (6/31)" | `summary.shadow_hit_rate_on_covered = 0.1935…` = 19.35%; `by_project` shadow_hits sum = 2+2+0+2 = 6 | ✓ |
| "baseline 68% (21/31)" | `summary.baseline_hit_rate_on_covered = 0.6774…` = 67.74%; manual count of rows where `true_delta ∈ [−4, 4]` = 21 (18 zero-delta + House10 pair 7 + AugRel pair 2 + AugRel pair 7) | ✓ |
| "LOSES to baseline by 15 pairs" | `summary.shadow_beats_baseline_on_covered = -15` | ✓ |
| Decompressed XML hash `3c92fdc3dd76e475ecfed08091d08df7` identical across runs | 32 hex chars = 128-bit (MD5-shaped, unusual but legal for a fingerprint). The `gzip mtime in header` explanation matches gzip's default behavior — `apply_loops.compress_als` doesn't pass `-n` to gzip (consistent with the claim). Cannot independently verify the exact bytes from the staged files alone, but the explanation is sound. | ✓ (reasoning); hash itself not directly verifiable here |
| **"Full suite 845 -> 851 (6 new tests, `Tests/test_canonicalize_pair_history.py`)"** | 6 new tests in `test_canonicalize_pair_history.py` (`test_shadow_preference_*`) **PLUS** 6 new tests in the entirely-new `test_evaluate_shadow_swap_preference.py` (`test_evaluate_*`, `test_summarise_*`, `test_real_corpus_evaluation_*`). Total **12 new tests** in two files. If the suite was 845 before, it should be **857 after**, not 851. | ✗ |

**The "845 → 851" figure is off by 6** — it accounts for the 6 `shadow_preference` tests but silently omits the 6 evaluate tests, which the same paragraph two sentences later explicitly credits ("`Source/evaluate_shadow_swap_preference.py`** (+ 6 tests, `Tests/test_evaluate_shadow_swap_preference.py`)). The fix is either `845 → 857` or some other figure that accounts for both files. Sam needs an accurate test count to trust the "all tests still pass" claim.

---

### UNASKED FINDINGS

1. **T4 imprecision, already acknowledged in the burn list entry itself.** `load_records` reports `"missing field(s): ['sam_bass_swap_beat']"` for AugRel pair 4 when `sam_bass_swap_beat` is `null`, not absent. Honest about it; not fixed in Step 1. The fix would be distinguishing `record.get(f) is None` (could be missing OR null) vs `f not in record` (literally absent). Out of scope per the burn list.

2. **The negative result is statistically credible, not a measurement artefact.** A 48-percentage-point gap (68% vs 19%) on 31 pairs means 15 pairs would need to flip sign in the same direction for shadow to match baseline. The `UNDERPOWERED WARNING` is fair, but the direction is robust. The causal story the burn list tells — "similarity-weighted average regresses toward the small numbers because most of the corpus IS zero" — matches what the data shows: e.g. Tech House Heldout pair 8 has `true_delta=0` and `shadow_delta=-48.0`, drawing heavily on House 10 pair 8 (-64) and pair 9 (-64); the model is reaching for the magnitude but smoothing it down to -48 because other pairs in the top-3 are 0.

3. **`shadow_swap_preference` is correctly invoked at exactly one call site** (line 1581-ish of `propose_arrangement.py`, inside the overlap-analysis loop) and is never imported anywhere else. The function lives in `canonicalize_pair_history.py`, but no other Source/ module imports it. So "report-only" is enforced not just by "no readers" but also by "no other call sites."

4. **The `shadow_delta` precision is `round(..., 1)`** in the function's output (suggested_delta_beats is rounded to 0.1 beats). The eval JSON preserves this rounding. The AugRel pair 7 row is the only one with sub-1 precision (`-0.050420…` for `true_delta`, `0.0` for `shadow_delta`), and that's because the *true* delta comes from the raw `459.94957984723624` beat field Sam's arranger produced. That asymmetry — shadow rounded to 1 decimal, truth at full precision — is fine for the eval (the within-tolerance flag treats them both numerically), but it's a quirk worth knowing about if anyone reads the JSON forensically.

5. **Conflict exclusion is doing real work in the eval.** If the 4 conflicts were INCLUDED as canonical, they'd contribute 4 rows where the duplicate's `delta_beats` differs from the other duplicate by 32-96 beats — none of them would match shadow within 4 beats, and none would match the baseline (their delta isn't 0). That'd push the headline down further and dilute the shadow-vs-baseline comparison. The current "exclude conflicts" call is right; without it the eval would be a less fair test of the leave-one-project-out hypothesis.

---

VERDICT: CORRECTION — "Full suite 845 → 851" undercounts the new tests: 6 `shadow_preference` tests in `test_canonicalize_pair_history.py` + 6 entirely-new tests in `test_evaluate_shadow_swap_preference.py` = 12 new tests; suite delta should be `845 → 857` to match the `+6` parenthetical and the `( + 6 tests, Tests/test_evaluate_shadow_swap_preference.py)` clause two sentences later. The signal itself (shadow 6/31 vs baseline 21/31) is correctly measured and the negative result is trustworthy.


===MINIMAX-ASK-DONE exit=0 session=pi nonce=3ad64d5fe565445dbc450a10789602d4===
