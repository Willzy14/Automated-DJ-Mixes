**VERDICT A: CORRECTION** (re-verification is theater, not a real gate — must fix before this ships)

**Plan A — findings:**

- **SEVERITY: BLOCKER.** The "re-run `check_grid`" step that the plan claims is the direct fix for the silent-bypass risk cannot fail. Cite: `validate_beatgrid.py:check_grid` sets `stem_fitted=True` from `ov.get("phase_source") == "drum-stem-kicks"` (line ~397), and `verdict_from` for `stem_fitted=True` returns **PASS unconditionally when `stem_kf_ms` is None or ≤ 15ms** (lines 309-322). The plan's auto-refit writes `phase_source: "drum-stem-kicks"` (see `refit_grid_from_stem.py:113`) but **never writes `grid_vs_kick_ms`** (confirmed by grep — zero occurrences in `refit_grid_from_stem.py`). So the re-run is always PASS and the auto-refit's silent-bypass claim is invalid for the exact Afro/Latin / structurally-mismatched case the plan worries about. **Falsifier**: a track whose drum-stem kick fit clears the refit script's own `inliers>=100, iqr<=30, abs(med)<=3` thresholds but sits >15ms off its own kicks (e.g., percussion-band congas the script doesn't reject) — plan A would silently ship it as PASS because re-run `check_grid` short-circuits. **Fix (one-line)**: either (a) have `attempt_stem_refit` compute and write `grid_vs_kick_ms` (offset of fitted grid vs its own kicks, then `STEM_KF_FAIL_MS = 15.0` actually catches a bad refit), or (b) re-run `check_grid` with `stem_fitted=False` so the whole-track onset-vs-grid test is the actual ruler. Option (a) matches the docstring's intent on line 295 (`"the upstream detector already verified it sits on them"`) and is the smallest delta.

- **SEVERITY: MINOR.** The plan's claim that the `phase_source=="drum-stem-kicks"` guard "structurally prevents any retry loop" is true but the guard is **unreachable in normal flow**: a track with an existing override already passes via the `stem_fitted=True` short-circuit at lines 309-322, so it's never in `fails` to begin with. The guard is defensive-only. **Fix**: state this honestly in the plan (defensive, not load-bearing), or drop it.

- **SEVERITY: MINOR.** The plan doesn't address what happens if `attempt_stem_refit` raises (Demucs failure, lattice_fit edge case on a track that already failed). An unhandled exception propagates out of `enforce_beatgrid_quality` and hard-stops the pipeline — probably desired, but unstated. **Fix**: one line in the plan — "exceptions from `attempt_stem_refit` propagate to the existing `RuntimeError`; refused fits return `None` and remain in `fails`".

- **SEVERITY: MINOR.** The "380-pair corpus should show zero change" claim is hand-wavy: the auto-refit path loads Demucs / drum-stem cache for every FAIL, not for every PASS. If the act of loading stems has any side effect (it doesn't here, but the plan doesn't audit that), PASS-track corpus could move. **Fix**: add an explicit assertion to the validation plan that PASS-track corpus is byte-identical (not just "structurally guaranteed").

**Invariant that does hold**: the never-retry-twice guard is correct as written and the refused-vs-failed-vs-respected state machine in the design is sound.

---

**VERDICT B: DROP** (park it — do not build this round)

**Plan B — findings:**

- **SEVERITY: MAJOR (resolves to "build it later, not now").** Zero real usage, zero defect, optional polish on a C6-tier path that runs on every transition of every mix. The plan itself honestly flags this as the open question. The warning at the legacy path's consumption site already surfaces the "hint set but ignored" case at runtime, which is the only silent-failure class worth caring about. **My honest call**: **park it.** Build when (a) a real project actually needs the hint and (b) the warning proves insufficient. Building now adds new code paths into a function that runs on every transition of every mix, for zero current value, and the very purpose of the existing `_mix_cues` + `emit_hint_fields` gate precedent was to keep these surfaces inert until evidence warranted them.

- **SEVERITY: MINOR (would need cleanup if it ever does get built).** The plan claims "reuse the existing cut-construction helper" for the hint-driven `intro_cut`, but **there is no cut-construction helper** — block (2) of `plan_fill_or_cut` (line 2299) builds `FillCutSpec` inline. The plan's mental model is off; the implementation would need to factor out a helper or duplicate the `0 < cut_to < intro_end` sanity check. Cite: `align_engine.py:2299`. **Fix (when built)**: extract a small `_build_intro_cut_spec(host_section, intro_end, first_drop_in, arr)` helper used by both block (2) and the new hint path.

- **SEVERITY: MINOR (would need cleanup when built).** The mutual-exclusion story is sound but under-described. The plan says "set the SAME `intro_loop`-style mutual-exclusion flag" — but block (2) is gated by `if not intro_loop`, and the hint-driven cut is *itself* an intro cut, so setting `intro_loop=True` correctly suppresses block (2). That works (verified by reading lines 2207, 2271, 2296, 2299), but the plan's wording "the automatic `incoming_intro`/`intro_cut` logic skips itself" could be misread as "the flag means loops, so setting it for a cut is weird". **Fix (when built)**: rename `intro_loop` → `intro_filled` in the same commit (the variable already serves loops-and-cuts mutually-exclusive purpose), or document explicitly why the misnamed flag is the right gate for an intro-cut hint.

- **SEVERITY: MINOR (would need clarification when built).** `loop_source_sec` is described as "an override for automatic loop-source selection" without scoping which call sites of `pick_clean_drum_loop` are affected. The plan needs to name them: `plan_fill_or_cut` calls `pick_clean_drum_loop` from blocks (1), (1a's upstream), and (3) — incoming-intro intro, incoming-intro outro fallback, and outgoing-tail outro. Apply the hint at all three, or only at (1)/(1a)? **Fix (when built)**: state explicitly which call sites the hint overrides.

- **SEVERITY: MINOR (would need clarification when built).** The "still run the picker's own quality checks" claim for `loop_source_sec` skips `pick_clean_drum_loop`'s vocal/fill blocking (`_blocked` at `align_engine.py:2014`). A hand-authored hint that overlaps a vocal region would currently be flagged unmeasured by `evaluate_loop_quality` but might still pass on benign metrics. **Fix (when built)**: also run `_blocked` against the hint window — a hand-authored loop source landing in a vocal region should be rejected loudly, not just unmeasured.

- **Invariant that does hold**: the precedent for bridging `track_hints.json` sec → Track bar fields via `_sec_to_bar` next to `_resolve_stem_key` (lines 2687-2698) is exactly right for both new hints — same shape, same site, no new machinery needed.

---

===REVIEW-COMPLETE===

