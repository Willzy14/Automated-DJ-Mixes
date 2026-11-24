# Build brief: per-estimator thresholds for the kick check (Sam approved 2026-11-24)

Written 2026-11-24 by Claude (Sonnet 5.5) for Sam. Builder: a Sonnet subagent. Reviewers: MiniMax plus an independent Opus subagent, then the lead's executing check. No em dashes anywhere (code, comments, output, docs, commit text). Python output ASCII only.

## Why

The kick check (`Source/kick_alignment_check.py`, merged 2026-11-23; read it and its tests first) judges every blend with one pair of figures, WARN 4 ms and FAIL 8 ms, applied to the worse of its click and main-hit medians. Sam listened to five versions of the same mix and the results do not fit those figures. He approved these new thresholds on 2026-11-24 ("yes, adopt the thresholds"):

- CLICK lag (cross-correlation above 150 Hz, the main number): WARN 2.5 ms, FAIL 5.0 ms.
- MAIN HIT lag: WARN 8.0 ms, FAIL 14.0 ms.
- EDGE lag: report only, never part of the verdict (his approved result has edge +4.7 and +5.5 ms; a copy he rated best of three had +9.0).
- SLOW_ATTACK flag (|click median minus main median|) fires above 8.0 ms (was 4.0); EDGE_DISAGREES (|edge median minus click median|) fires above 8.0 ms (was WARN_MS, 4.0). Their effect is unchanged (raise PASS to WARN only).
- Unchanged: MIN_SHARED_KICKS 16, SCATTER_MS 8.0 (its own literal), MINORITY_FRACTION 0.20 (now counted per estimator against that estimator's FAIL figure), the corroboration rule for scattered estimators (a scattered estimator may fail a blend only if the OTHER estimator's median is at least ITS OWN warn figure), the OFF_GRID rule, CANNOT_JUDGE reasons, the drift and half-blend rules (a half at or beyond an estimator's FAIL figure is a drift flam).

The evidence (all measured on Sam's Tech House 2026 mix, blends 1 = Get Right to No Panties and 2 = No Panties to Taste; click / main / edge in ms, positive = incoming later):

| Version | Blend 1 | Blend 2 | Sam's words |
| --- | --- | --- | --- |
| planned (original) | +10.1 / +16.5 / +7.5 | -5.7 / -8.5 / +0.5 | flam heard on both |
| No Panties slid +6.4 | +4.1 / +10.2 / +1.1 | +0.6 / -2.1 / +6.9 | "tighter but can be better" |
| +8.5 | +2.0 / +8.1 / -1.0 | +4.3 / +0.6 / +9.0 | "tightest" of three |
| +12 | -1.5 / +4.6 / -4.5 | +6.5 / +3.6 / +12.5 | not picked |
| Get Right drift fixed, NP +5 | -0.3 / +6.7 / -2.9 | -0.9 / -3.4 / +5.5 | "tighter" |
| automatic tool output | +0.5 / +7.3 / -2.1 | -1.3 / -4.2 / +4.7 | "as tight as I would get it manually" |

## What to build

1. In `Source/kick_alignment_check.py`: replace the single `WARN_MS`/`FAIL_MS` pair by named per-estimator constants `CLICK_WARN_MS = 2.5`, `CLICK_FAIL_MS = 5.0`, `MAIN_WARN_MS = 8.0`, `MAIN_FAIL_MS = 14.0`, `SLOW_ATTACK_MS = 8.0`, `EDGE_DISAGREE_MS = 8.0` (with one comment line: starting figures Sam set on 2026-11-24 from five listening comparisons). Remove `WARN_MS` and `FAIL_MS` rather than aliasing them (no compatibility shims); update every user. The verdict logic in `judge` (and the helpers `_minority`, the explanatory texts, the picture's worst-beat score at line ~716, and every message that prints a FAIL or WARN figure) must use the figure of the estimator concerned. The worst-beat selection for the pictures must score a beat by its lag relative to ITS estimator's warn figure so the main hit is not always chosen just because its numbers are bigger. Edge never decides a verdict.
2. Update the CLI output, TOOLBOX entry and the module docstring to the new figures.
3. `Source/grid_phase_fix.py` uses the kick check's thresholds (`kac.WARN_MS`, `kac.FAIL_MS`, `DRIFT_MIN_MS = WARN_MS`, `needs_fixing`, "already good" rule, the STILL FLAMMING report). Make the SMALLEST edits that keep that tool's behaviour what it is today on real data: `DRIFT_MIN_MS` becomes its own literal 4.0 (the 4 ms noise rule must NOT follow the new 2.5 ms click warn figure; with 2.5 Taste's 3.7 ms swing would be wrongly fitted); the grid tool's "needs fixing" test and "already good" rule use `kac.CLICK_WARN_MS` for the click median and `kac.CLICK_FAIL_MS` for a half; the "edge or main got worse by more than" report keeps a 4.0 literal of its own. Another builder is editing `grid_phase_fix.py` in a different worktree at the same time (apply mode); keep your edits there to these few lines so the merge stays trivial, and list every changed line in your report.
4. Tests: update the existing tests that pin the old figures; add tests that fail on the old code for each new rule: verdict per estimator (click 2.4 PASS, 2.6 WARN, 4.9 WARN, 5.1 FAIL; main 7.9 PASS, 8.1 WARN, 13.9 WARN, 14.1 FAIL), edge never decides (huge edge lag with fine click and main is PASS and only the EDGE_DISAGREES flag appears when the gap is above 8 ms, and then PASS becomes WARN), SLOW_ATTACK at 8.0 not 4.0, the corroboration rule per estimator, MINORITY per estimator, the worst-beat picture selection. Mutants to kill and prove: click figures used for main (and the reverse), edge allowed into the verdict, SLOW_ATTACK back to 4, DRIFT_MIN_MS tied to the click warn figure, a half checked against the wrong estimator's fail figure.

## Acceptance (the lead re-runs; paste real output)

Run `Tools/check_kick_alignment.py` on these real sets (audio folder `D:\Claude Workspace\Automated DJ Mixes\Projects\21.11.26 Tech House 2026 V2\Audio`) and report all eight transitions each. Expected verdicts for blends 1 and 2 (any other verdict means a bug or a wrong reading of the table: say which, do not tune):
- `Listening Sets/Tech House 2026 Planned.als`: blend 1 FAIL, blend 2 FAIL (click -5.7), blend 3 PASS.
- `Listening Sets/Tech House 2026 Planned Sam Tweak AUTO Phased Project/Tech House 2026 Planned Sam Tweak AUTO Phased.als` (Sam's approved result): blend 1 PASS, blend 2 PASS (no WARN from SLOW_ATTACK or EDGE_DISAGREES at 8 ms).
- `Listening Sets/Tech House 2026 Planned Sam Tweak GetRight Fix NP 5 ms Project/...NP 5 ms.als`: both PASS.
- `Listening Sets/Slide Tests 2026-11-23/Tech House 2026 Planned Sam Tweak Slide Project/...Slide.als` (+6.4): blend 1 WARN (click +4.1, main +10.2), blend 2 PASS.
- `Listening Sets/Slide Tests 2026-11-23/Tech House 2026 Planned Sam Tweak Slide 8.5 ms Project/...8.5 ms.als`: blend 1 WARN (main +8.1), blend 2 WARN (click +4.3).
- `Listening Sets/Slide Tests 2026-11-23/Tech House 2026 Planned Sam Tweak Slide 12 ms Project/...12 ms.als`: blend 1 PASS, blend 2 FAIL (click +6.5).
Also run `Tools/fix_grid_phase.py` on `Listening Sets/Tech House 2026 Planned Sam Tweak Project/Tech House 2026 Planned Sam Tweak.als` and compare the output markers with `D:\Claude Workspace\Automated DJ Mixes\grid-phase-build\lead2\A\Phased.als` (made by the current main before this change): every marker must be identical to within 0.001 ms, and the report's verdicts before and after must be listed (now stricter thresholds: say which blends changed verdict).

## Rules

- Work ONLY in the git worktree `D:\Worktrees\dj-thresholds` (branch `work/kick-thresholds`, already created, locked, based on current main). Nothing written in the main checkout; nothing on C: except tiny temp. Scratch: `D:\Claude Workspace\Automated DJ Mixes\thresholds-build\`.
- Match existing style. No docstring bloat, no backwards-compat hacks, no new dependencies.
- Tests: run yours, the grid phase and kick check tests, the pipeline tests, then the full suite with `PYTHONPATH=Source` and `--basetemp` under your scratch (D: is exFAT; name exFAT-only failures and show they also fail on main). Known unrelated failure: `Tests/test_kick_model_integration.py::test_adapter_defaults_and_smoothing_do_not_import_torch`.
- Commits on the branch, conventional (`feat(kick-check): ...`), body explains why, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Do not merge, push, or use `git stash`.
- Never lower a tolerance to make a verdict come out right. Say plainly what you could not verify.
- Return: commit hashes, files changed (and every changed line in `grid_phase_fix.py`), test counts before and after, tests proven failing on main, mutants killed, the six real-data tables with the verdicts (expected versus actual), the grid phase marker comparison, and one honest line on anything unverified.
