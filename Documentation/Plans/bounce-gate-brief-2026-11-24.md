# Build brief: the bounce check as a report-only gate the mix-building AI must act on (Sam approved 2026-11-24)

Written 2026-11-24 by Claude (Sonnet 5.5) for Sam. Builder: a Sonnet subagent. Reviewers: MiniMax plus an independent Opus subagent, then the lead's executing check. No em dashes anywhere (code, comments, output, docs, commit text). Python output ASCII only.

## Why

`Source/bounce_check.py` / `Tools/check_bounce.py` (merged today, read them and `Tests/test_bounce_check.py` first) measure each join on the rendered bounce. Sam: "add it as report-only, but something the AI that is building the mix will act on, in the sense of taking a look itself if the report brings something up." So: never fail a build, but when a join looks wrong the report must say so unmistakably, name what to look at, and leave a file the building AI cannot miss. Today the CLI prints every "outside the accepted range" measure, which on the Tech House original bounce flags 5 of 8 joins, mostly sub-band tone steps between two different tracks, which are normal. A gate that cries wolf will be ignored, so it needs a narrower, defensible "needs a look" rule.

## What to build

1. **An attention rule** in `Source/bounce_check.py` (named constants, a comment saying they are starting figures that Sam will set, like the kick check's): a join "needs a look" only for measures with a direct audible meaning, and only when outside Sam's accepted range by a material margin:
   - loudness step (`loud_step_lu`) and blend peak vs sides (`loud_peak_vs_sides_lu`): outside by at least 1.0 LU;
   - low-end level vs sides (`low_level_vs_sides_db`): outside by at least 3.0 dB (a phase hole or a double bass pile-up);
   - low correlation change (`low_corr_drop`, concerning end only, as now): outside by at least 0.05;
   - width step: outside by at least 3.0 dB;
   - a kick flam state of SEEN (not "too few beats", not "not discriminating", not "not measurable");
   - NOT attention-worthy by themselves: per-band tonal steps (incl. sub), worst-bar levels, flam "not measurable". They stay in the report as information.
   The margins are starting figures; make them constants and report how many joins flag on each of the 14 real bounces already in `Config/bounce_check_calibration.json` (the corpus of `bounce_check_ranges.json`) and on the Tech House original (`Listening Sets/Tech House 2026 Planned.wav` with its `.als`): expected, the Tech House original flags join 1 (flam seen) and join 6 (loud step +5.4 LU, outside by 1.96); say what else flags and whether any flag looks like noise. Do NOT tune margins to hit those two; report what the figures give.
2. **An attention file** written by the CLI beside the pictures: `ATTENTION.md` (and the same in the JSON under `attention`): `NEEDS A LOOK: join 6 POP > Lift Off (25:27.3-26:25.8): loud step +5.4 LU, outside Sam's accepted range (max +3.4) by 2.0; picture: <path>`, one block per flagged join, and when nothing flags a single line `no join needs a look (N joins checked)`. A silent, not-measured or start-warning situation is also written there (`START WARNING`, `bounce does not fit the arrangement`) because those mean the numbers cannot be trusted. The file is always written (so its absence means the check did not run).
3. **Exit codes stay report-only.** Exit 0 for every completed check, whatever it found; 2 only when it could not run (as today). Add a one-line stdout summary at the very end: `BOUNCE CHECK: 2 of 8 joins need a look (J01, J06): see <ATTENTION.md path>` or `BOUNCE CHECK: no join needs a look`.
4. **Pipeline hook** in `Tools/run_mix_pipeline.sh`: after the kick/grid steps and the final is chosen, if a bounce WAV already sits beside the final ALS in `Output/` with the same basename (`<final basename>.wav`, e.g. `Sections V3 Phased.wav`; also accept `<project> Mix V<N>.wav` beside `<project> Mix V<N>.als` is NOT needed, keep it to the same-basename rule and say so), run `Tools/check_bounce.py` on it with `--als <final ALS>` into `Output/Bounce Check/`, report-only (a crash never stops the build; log "bounce check did not finish (status N)"), and print its one-line summary into the log. If no bounce exists print `bounce check: no bounce beside the final yet (render it, then run Tools/check_bounce.py)`. `BOUNCE_CHECK_MODE=off` (any case) skips it. Remove any stale `Output/Bounce Check/ATTENTION.md` of an earlier build at the start (as the grid phase step does), so an old report cannot pass for this build's. Add hook tests in `Tests/test_run_mix_pipeline.py` in the same style as the grid and kick hook tests (stubbed python): bounce present, absent, off, tool crash, stale attention removed.
5. **TOOLBOX** entry updated. Do NOT edit `commands/mix.md` (frozen sync list; the lead does it).

## Tests (each must fail on the old code)

- Attention rule per measure at each margin edge (0.99 vs 1.0 LU, 2.9 vs 3.0 dB, 0.049 vs 0.05); tonal steps and worst-bar never flag alone; a flam "too few beats" does not flag; a flam SEEN does.
- ATTENTION.md content for a flagged join and for none flagged; written even when the check ends early (not measured join, start warning).
- CLI exit 0 on a flagged result; last stdout line format.
- Mutants to kill and prove: margins ignored (any outside flags), tonal flagged, flam not-measurable flagged, attention file only written when something flags, exit code 1 on flagged, hook run when mode is off.

## Rules

- Work ONLY in the git worktree `D:\Worktrees\dj-bounce-gate` (branch `work/bounce-gate`, already created, locked, based on current main). Nothing written in the main checkout; nothing on C: except tiny temp. Scratch: `D:\Claude Workspace\Automated DJ Mixes\bounce-gate-build\`.
- Match existing style. No docstring bloat, no backwards-compat hacks, no new dependencies. Do not change any measurement or the ranges file.
- Tests: yours, `Tests/test_bounce_check.py`, `Tests/test_run_mix_pipeline.py`, then the full suite with `PYTHONPATH=Source` and `--basetemp` under your scratch (D: is exFAT; name exFAT-only failures and show they also fail on main). Known unrelated failure: `Tests/test_kick_model_integration.py::test_adapter_defaults_and_smoothing_do_not_import_torch`.
- Commits on the branch, conventional (`feat(bounce-check): ...`), body explains why, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Do not merge, push, or use `git stash`.
- Never loosen a margin to make a join flag or not flag. Say plainly what you could not verify.
- Return: commit hashes, files changed, test counts, the per-bounce table of which joins flag on the 14 real bounces (and on the Tech House original with ATTENTION.md pasted), mutants killed, and one honest line on anything unverified.
