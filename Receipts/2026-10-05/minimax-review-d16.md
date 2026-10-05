# Verdict: **CORRECTIONS**

The coalesce function itself is mostly correct but has gaps (Q1, Q2), and the residual swap-boundary check you left unfixed is structurally wrong and should be changed (Q3).

---

## Q1 — Can this mask a real defect?

**Yes, in three narrow but real shapes.** None of them break the invariants you cite in the docstring, but the function's "absorb any contiguous split inside the window" rule is more permissive than the docstring's "an extra start" (singular) wording suggests.

### 1a. `prev_end` handling when a clip's `CurrentEnd` is `None`

Correct, in two ways:

- The `contiguous` guard at line 97 short-circuits on `prev_end is not None`, so the function cannot raise `TypeError` when the previous clip lacked `CurrentEnd`.
- If a kept or dropped clip has `end=None`, `prev_end = end` on line 104 (and on line 100) sets `prev_end` to `None` for the next iteration. Every subsequent clip then has `contiguous = False`, so no further coalesce can happen. This is silent but conservative — the comparison just fails normally.

So malformed clips degrade safely. **No crash risk.** But it does mean that a single missing `CurrentEnd` early in the sequence freezes the rest of the coalesce pass into "kept" mode, which is fine for catching defects but worth a defensive test (see Q2).

### 1b. Concrete adversarial layouts

Layouts are for `WINDOWS = [(2756.0, 2788.0)]`, `EXPECTED = [2756.0, 2772.0]`. Trace what `_coalesce_automation_splits` returns.

**Adversary A — defect clip with the exact swap-half shape, sitting on top of a planned boundary:**
```
clips = [(2756.0, 2772.0), (2772.0, 2780.0), (2780.0, 2788.0)]
```
- (2756, 2772): planned → kept. prev_end=2772.
- (2772, 2780): planned (2772 in expected) → kept. prev_end=2780.
- (2780, 2788): not planned, inside, contiguous → **dropped**.

Output: `[2756.0, 2772.0]`. PASSES. The third clip is silently absorbed even though it is not the swap-half. Any defect clip whose start touches `prev_end` and sits strictly inside the window is masked, as long as the previous kept clip happened to land on a planned boundary.

**Adversary B — two adjacent bookkeeping splits (your docstring says "an extra start"; the code accepts any number):**
```
clips = [(2756.0, 2772.0), (2772.0, 2776.0), (2776.0, 2780.0), (2780.0, 2788.0)]
```
- (2756, 2772): planned → kept. prev_end=2772.
- (2772, 2776): planned → kept. prev_end=2776.
- (2776, 2780): not planned, inside, contiguous → dropped. prev_end=2780.
- (2780, 2788): not planned, inside, contiguous → dropped.

Output: `[2756.0, 2772.0]`. PASSES. Three extra starts were absorbed.

**Adversary C — bookkeeping split whose END overshoots the window (this is the one you might actually hit):**
```
clips = [(2756.0, 2772.0), (2772.0, 2796.0)]
```
- (2756, 2772): planned → kept.
- (2772, 2796): planned → kept.

Output: `[2756.0, 2772.0]`. PASSES. **However**, this defect is masked by the OLD code too — neither version checks `end` against `hi`. So not a regression. Still worth noting: the new coalesce adds no protection against clip-end overshoot.

**Adversary D — gap that wraps a bookkeeping clip:**
```
clips = [(2756.0, 2772.0), (2772.0, 2774.0), (2774.5, 2788.0)]   # 0.5-beat gap before clip 3
```
- (2756, 2772): planned → kept.
- (2772, 2774): planned → kept.
- (2774.5, 2788): not planned, inside, contiguous? `2774.5 - 2774 = 0.5` ≠ prev_end 2774 within `1e-6` → NOT contiguous → kept.

Output: `[2756, 2772, 2774.5]` ≠ expected. FAILS. Good — `test_gap_before_extra_clip_still_fails` covers this.

**Adversary E — bookkeeping clip whose start is exactly at the window edge (line 95's strict `<`):**
```
WINDOWS = [(2756.0, 2788.0)], clips = [(2756.0, 2772.0), (2772.0, 2788.0), (2788.0, 2796.0)]
```
The third clip starts at `2788.0`, which is `hi`. The strict `lo + 1e-6 < start < hi - 1e-6` rejects it. PASSES the "outside window" guard. Good — covered by `test_extra_clip_outside_loop_window_still_fails`.

### Bottom line for Q1

The three guards (`not planned`, `inside`, `contiguous`) correctly catch the documented defects (gap, stray outside window, duplicate outside window). They do NOT catch:
- A defect clip whose start happens to equal `prev_end` and sits inside the window (Adversary A).
- A chain of two or more contiguous "extra" starts (Adversary B). This contradicts the docstring's "an extra start" wording.
- A defect clip whose END overshoots the window (Adversary C). Pre-existing.

**Recommendation:** add a fourth guard that limits absorption. The cleanest, lowest-cost guard is "the bookkeeping clip's `end` must not push the cumulative coverage past the next planned boundary". Concretely: after a clip is dropped on line 100, set `prev_end = min(end, next_planned_boundary_or_window_hi)`, so any overshoot at the bookkeeping stage is forced into `prev_end` and the next contiguity check (or, equivalently, the comparison) catches it. Or, equivalently, refuse to drop more than one clip between two planned starts. Either guard fixes Adversaries A and B without touching the happy path.

---

## Q2 — Missing tests

The current five tests cover the happy path, the no-op case, gap detection, outside-window detection, and the first-clip invariant. Gaps:

1. **Chain of two bookkeeping splits** — verify or refute the Adversary B behaviour. Today the test asserts nothing about "more than one extra clip".
2. **Defect with the swap-half signature, layered on a planned boundary** — Adversary A. Either assert the current (over-permissive) behaviour with a comment that it is known, or assert "should fail" once you add a guard.
3. **Strict-window boundary**: `(start == lo)` and `(start == hi)` (line 95).
4. **`end = None`** — first clip has no `CurrentEnd`. Assert it does not crash and that subsequent clips are NOT coalesced (conservative).
5. **`end = None` mid-sequence** — first clip is fine, second has no `CurrentEnd`. Assert the third clip is not coalesced (defensive).
6. **Empty `loop_clips`** — should return `[]`.
7. **`expected_times` is empty** but there is a planned window — no edges dropped.
8. **Multiple windows** — e.g., `WINDOWS = [(2756, 2788), (2800, 2832)]` with one swap-half in each window.
9. **`expected_times` contains a `partial_beats` trailing boundary** — assert the partial-trailing boundary is treated as planned (your line 277 does add it to `expected_times`, but the function should be tested as receiving it).
10. **End-to-end test through `reconcile`** with a minimal ALS fixture that includes a `_tail_loop` clip split at the swap point. This is the one that actually proves the validator stops failing on a real mid-repeat split. None of the five current tests touch `reconcile`.
12. (Optional) **Adversary C**: a planned clip with `end` past the window. Pin pre-existing behaviour so a future change cannot silently regress.

Items 4 and 5 are the most important — `end=None` is the only realistic input where the current code can silently skip a coalesce, and there is zero coverage.

---

## Q3 — The residual "swap does not match the frozen outgoing loop boundary" check

Lines 315–340. The check fires when:
```
outgoing_loops AND NOT matches_loop_boundary AND swap_inside_loop
```

That is: there is an outgoing loop, the swap is NOT at a frozen repeat boundary, AND the swap IS inside the loop window.

This is **structurally incompatible with the coalesce you just added**. The whole reason the coalesce exists is that apply_automation splits a loop-repeat clip when the swap lands mid-repeat, i.e. precisely when `not matches_loop_boundary AND swap_inside_loop`. The frozen MixPlan's `loop_boundaries` (line 315) enumerate repeat boundaries only; the swap point is intentionally not in that list. So this check fires on the known-good mid-repeat swap every single time, regardless of whether apply_automation correctly split.

**Keeping it is wrong.** The validator will fail every transition where the planner encoded a mid-repeat swap, even when apply_automation produced exactly the bookkeeping split you just taught `_coalesce_automation_splits` to accept.

The right behaviour: drop the `swap_inside_loop` clause from the failing condition. The remaining "fail" set is "swap is not at a frozen loop boundary AND not inside the loop" — i.e., a swap that genuinely overshot or undershot the loop window. That is the only case the frozen plan can actually contradict, because anything inside the window is by construction what the planner knew and what apply_automation is allowed to handle via the bookkeeping split.

Concretely, the fix is one line:

```python
# Before (line 337)
if outgoing_loops and not matches_loop_boundary and swap_inside_loop:

# After
if outgoing_loops and not matches_loop_boundary and not swap_inside_loop:
```

That turns the check into the assertion the docstring at line 339 was trying to write. Everything below it (`checks.append(f"bass_swap:...")` and the conditional `loop_after_swap` check at lines 343–349) already uses the right logic — `loop_after_swap` should fire when "outgoing loop exists AND swap is not at a boundary", which is exactly the mid-repeat case. The current control flow only reaches the else branch when the error is NOT raised, so once line 337 stops firing on the mid-repeat case, the existing `loop_after_swap` check will correctly tag every mid-repeat swap as "tail_plays_under_incoming".

If you want belt-and-braces, layer on the clip-boundary proof (lines 351–373) for non-landmark policies too — that uses actual ALS clip boundaries (`_matches_clip_boundary`), which DO include the swap point when apply_automation split. That gives you the real-defect coverage you lose by dropping the `swap_inside_loop` clause. Without that, a non-landmark transition where apply_automation forgot to split will pass; with it, you catch it the same way landmark policies already do.

---

## Summary of corrections

1. **Q1 (real-defect masking):** Adversaries A and B are real. Add a guard at line 96–100 that limits absorption to a single clip between two planned starts, OR clamps `prev_end = min(end, next_planned_or_window_hi)` so overshoots surface. Pre-existing end-overshoot (Adversary C) is not from this change but is worth noting.
2. **Q2 (missing tests):** Add tests for `end=None` (first clip and mid-sequence), empty inputs, strict-window-edge boundaries, multiple windows, the partial-trailing planned boundary, and at least one end-to-end test that drives `reconcile` with a synthetic ALS fixture containing a mid-repeat split. Adversary A/B behaviours should be pinned explicitly so the decision is documented in the test suite.
3. **Q3 (residual swap check):** Remove `swap_inside_loop` from the failing condition at line 337. The check currently fires on every known-good mid-repeat swap. Optionally extend the landmark-only `_matches_clip_boundary` proof (lines 351–373) to all policies.

