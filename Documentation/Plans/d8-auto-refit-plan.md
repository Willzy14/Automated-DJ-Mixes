# D8 plan: auto-attempt a stem-kick grid refit before hard-stopping on a bad beatgrid

**Status: DRAFT, not built. Per D8's own 2026-09-15 investigation note, this touches a
safety-relevant gate with a documented past incident (the 09.06.26 "Todd" bug — a beat grid
that didn't sit on its audio shipped, and warping drifted audibly). Per this project's own
`/codex-review` standing trigger, this plan needs a peer-reviewed pass BEFORE any code lands,
not solo blind building. Codex is durably capped today (confirmed live, resets ~2026-09-26);
routed to MiniMax + a Claude subagent per the standing capped-seat rule.**

## The problem, restated precisely

`Source/validate_beatgrid.py:enforce_beatgrid_quality` hard-stops the pipeline (`RuntimeError`)
when any track's beat grid fails the whole-track onset-vs-grid sweep test, unless
`--allow-bad-grids` is passed. `Source/refit_grid_from_stem.py` is a documented escalation path
— it refits the grid from Demucs drum-stem kick attacks instead of full-mix onsets or Ableton's
`.asd` ticks, specifically for percussion-dense material where the tick/onset detectors lock
onto anticipating percussion instead of the kick (the La Trumpter case in its own docstring).
Today nothing calls it automatically: a track that fails the gate is either fixed by a human
running the script by hand, or the whole commissioned run hard-stops until someone does.

## What the 2026-09-15 investigation already settled (don't re-derive)

- `refit_grid_from_stem.py` has zero automatic call sites in the live pipeline (grepped,
  confirmed).
- The item's own originally-cited motivating example (Arielle Free/Idris Elba, held-out set,
  16ms off) is murkier than it first reads: the FAIL message text that names the near-miss is
  generated ONLY by `check_grid`'s `stem_fitted=True` branch, which requires a
  `grid_overrides.json` entry with `phase_source: "drum-stem-kicks"` to already exist for that
  track — meaning a refit had ALREADY been attempted (by a human) for that specific message to
  have fired at all. No `grid_overrides.json` exists under that held-out project today
  (gitignored, no history to check), so it's unconfirmed whether AUTO-attempting a refit from
  scratch would have rescued that specific track — its "out of its 4-to-floor range" framing
  suggests a structural rhythm mismatch (Afro/Latin-influenced) a same-algorithm retry likely
  reproduces rather than fixes.
- The general automation gap (nothing tries the escalation automatically) is real regardless of
  whether that one example would have been rescued.

## Design

### 1. Extract `refit_grid_from_stem.py`'s core into an importable function

Today it's a `main()` reading `sys.argv` and printing to stdout, with a bare `sys.exit(main())`.
Split into:
- `attempt_stem_refit(wav: Path, project: Path) -> dict | None` — runs the existing
  separate/fit/refuse logic (`load_or_separate_stems`, `attack_onsets`, `lattice_fit`, the
  existing `ok = inliers.sum() >= 100 and iqr <= 30.0 and abs(med) <= 3.0` refusal gate,
  unchanged) and returns the override dict (the same shape currently written to
  `grid_overrides.json`) on success, `None` on refusal. Pure — does NOT write the overrides
  file itself.
- `main()` becomes a thin CLI wrapper: call `attempt_stem_refit`, print the same messages it
  prints today, write the file if not `--dry-run`. Byte-identical CLI behaviour, proven by the
  existing manual-usage path continuing to work unchanged (no existing test exercises this file
  today per a grep of `Tests/` — worth adding a basic CLI-still-works smoke test as part of this
  refactor, since none currently pins it).

### 2. Wire ONE bounded auto-attempt into `enforce_beatgrid_quality`

In `Source/validate_beatgrid.py:enforce_beatgrid_quality`, after computing `checks` and before
raising on `fails`:

```python
fails = [c for c in checks if c.verdict == "FAIL"]
if fails and not allow_bad_grids:
    fails = _attempt_auto_refits(fails, analyses, rb_matches, overrides, project)
if fails and not allow_bad_grids:
    raise RuntimeError(...)  # existing message, now also lists refit attempts that didn't help
```

`_attempt_auto_refits` (new):
- For each still-failing track, skip it if `overrides.get(name, {}).get("phase_source") ==
  "drum-stem-kicks"` already — **never retry a track that's already been through the stem-kick
  path once.** (A) this is the existing project convention (the override records provenance so
  it's judged on stem evidence, not the tick-biased sweep, per the existing
  `stem = ov.get("phase_source") == "drum-stem-kicks"` line already in this function), and (B)
  it structurally prevents any retry loop — the function can run at most once per track per
  pipeline invocation.
- For every other FAIL, call `attempt_stem_refit(track.path, project)`. On `None` (refused —
  too loose to trust), leave it in `fails` unchanged, but annotate the detail string so the
  eventual RuntimeError message says "auto-refit attempted, refused (fit too loose)" instead of
  just failing silently-from-the-human's-perspective a second time.
- On success (a dict), **do not trust the refit tool's own internal quality bar alone.** Write
  the override, then **re-run `check_grid` for that ONE track through the exact same
  verification `enforce_beatgrid_quality` already does** (not the refit script's own
  `inliers/iqr/med` thresholds, which measure fit-to-itself, not fit-to-what-the-gate-actually-
  checks). Only remove the track from `fails` if the RE-RUN verdict is PASS. If the re-run is
  STILL FAIL, keep it in `fails`, annotated "auto-refit attempted, still fails the beatgrid
  gate" — **the auto-refit is a recovery attempt, never a silent bypass of the gate itself.**
  This is the direct fix for the real risk a same-algorithm retry on structurally-mismatched
  material (the Afro/Latin case the 2026-09-15 note flagged) reproduces the same failure: if it
  does, the gate still catches it, exactly as it does today, just with a genuine attempt on the
  record.
- Playlist-complete discipline unchanged: a track that still fails after the auto-attempt is
  still a hard-stop (or `--allow-bad-grids` override), never a silent exclusion — this plan adds
  a recovery attempt, it does not touch the commissioned-mode completeness gate.

### 3. What this explicitly does NOT change

- `enforce_owned_grid_coverage` (the separate completeness gate) — untouched.
- The refit tool's own refusal thresholds (`inliers >= 100`, `iqr <= 30.0`, `abs(med) <= 3.0`) —
  untouched; they're a reasonable first filter, just not treated as sufficient on their own per
  point 2 above.
- `--allow-bad-grids` behaviour — unchanged; still the explicit human override for a track that
  fails even after the auto-attempt.

## REVISION 2026-09-22 (MiniMax review, BLOCKER found and fixed in this design)

**The "re-run check_grid" safety claim in point 2 above was theater as originally drafted, and
this revision fixes it before any code is written.** MiniMax's review of this plan traced
`check_grid`'s `stem_fitted=True` branch (`validate_beatgrid.py:309-321`) directly: it FAILs
only `if stem_kf_ms is not None and stem_kf_ms > STEM_KF_FAIL_MS (15.0)` — when `stem_kf_ms` is
`None`, it unconditionally PASSes. `enforce_beatgrid_quality` reads `stem_kf_ms =
ov.get("grid_vs_kick_ms")` from the override dict — and confirmed by re-reading
`refit_grid_from_stem.py`'s own override-writing block myself: **it never writes a
`grid_vs_kick_ms` key at all.** So "re-run the full gate" as originally drafted would ALWAYS
return PASS for a freshly-refitted track, regardless of fit quality, completely defeating the
stated purpose. This is a genuine, verified-correct BLOCKER, confirmed by reading the real code
myself (not taken on the review's word alone) — and it is a **pre-existing gap in the CURRENT,
already-shipped `refit_grid_from_stem.py`**, not something this plan introduces: any track a
human has EVER manually refitted with the existing tool has the same unconditional-PASS
exposure today, since the tool has never written this field.

**Fix, folded into point 1's extraction:** `attempt_stem_refit` must ALSO set
`"grid_vs_kick_ms": round(abs(med), 1)` in the override dict it returns — `med` (the fit's own
median residual in ms) is already computed by the existing `lattice_fit` call, so this is
wiring up an already-available number, not new computation. This fix applies to BOTH the new
auto-attempt path AND the existing manual CLI path (they share the same extracted function), so
building this plan also retroactively closes the pre-existing gap for manual refits — worth
calling out to Sam explicitly as a bonus fix, not just scope creep.

Also folded in from the same review pass (all MINOR, all real):
- The "already stem-fitted, skip it" guard is correct but **defensive-only, not load-bearing**:
  a track with an existing `phase_source=drum-stem-kicks` override already PASSes via the
  `stem_fitted=True` short-circuit before ever reaching `fails`, so it would never actually be
  offered to the auto-refit path in the first place. State this plainly rather than claim it's
  the thing preventing a retry loop.
- State explicitly what happens if `attempt_stem_refit` itself raises (Demucs failure, a
  `lattice_fit` edge case): the exception propagates out of `enforce_beatgrid_quality` exactly
  like any other exception today — intentional, not silently swallowed, but say so.
- The "0 changed pairs" no-regression claim for PASS-track corpus should be an explicit assertion
  in the validation step, not just "structurally guaranteed" prose — prove it, the same
  discipline every other change this session used.

## Validation plan (before this is considered built, not just coded)

1. **Real-data test, not just mocks**: run the new auto-refit path against the ACTUAL held-out
   project this item cites (or the current equivalent real corpus), on a REAL currently-failing
   track, and report what actually happens — rescued, refused, or rescued-but-still-fails-the-
   full-gate. This directly settles the 2026-09-15 investigation's own open question (does
   auto-refit help the real motivating case) instead of leaving it unconfirmed a second time.
2. **Regression**: the existing 380-pair-style corpus checks and the beatgrid test suite must
   show zero change for every track that currently PASSES the gate (the auto-refit path is only
   ever reached for FAILs, so this should be structurally guaranteed, but prove it rather than
   assume it).
3. **Unit tests**: `attempt_stem_refit` extraction is behaviour-preserving (CLI smoke test);
   `_attempt_auto_refits` correctly skips an already-stem-fitted track (no retry loop), correctly
   re-runs the full gate rather than trusting the refit tool's own thresholds, correctly leaves
   a refused or still-failing track in `fails`.
4. **Full suite green.**

## Open question for the reviewers (and ultimately Sam)

Is re-running the FULL `check_grid` verification after a successful refit worth the extra
Demucs-adjacent compute on every FAIL, or is the refit tool's own internal refusal threshold
judged sufficient given it already refuses a loose fit? This plan's position: given the
documented past incident was specifically about a grid that LOOKED locked but wasn't
audible-checked, trusting a second heuristic's own self-assessment without re-verification
through the same gate that caught the FIRST one would repeat the same class of mistake with
extra steps. Flagging this as a deliberate, arguable design choice rather than an obvious one.
