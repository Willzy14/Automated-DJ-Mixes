# Build brief: kick alignment check with a picture, built into the pipeline

Written 2026-11-23 by Claude (Sonnet 5.5) for Sam. Builder: a Sonnet subagent. Reviewers: MiniMax plus the lead's executing check. No em dashes anywhere (code, comments, output, docs, commit text). Python output ASCII only.

## Why

Sam heard a "flam" (two kicks slightly apart) in the blend Get Right into No Panties in `Listening Sets/Tech House 2026 Planned.als`. Every pipeline gate passed. A measured fix (No Panties' warp markers +6.4 ms) made it "tighter but can be better". Sam then sent a zoomed screenshot from Ableton (file: `C:\Users\Carillon\AppData\Local\Temp\claude\e27439d65a741d53\fdc9bde8-30e3-46e2-981c-f0cef04bee2e\images\2.png`, read it): two clips stacked, a beat line, the first kick's click starts before the line and the second kick's main hit starts about 90 px after it. Claude could see the problem by looking at that picture. Sam's point: the pipeline should do that look itself, on every mix, after everything is built: render the picture at each blend, look at it, and stop with a clear message when the kicks are not lined up.

Why the old gates miss it: `STEM_KF_FAIL_MS = 15.0` in `Source/validate_beatgrid.py` judges each track alone against its own fitted kicks (no sign, no pair), and `check_kick_flam` in `Source/render_check.py` ships disabled (false positives). Nothing compares the two tracks inside a blend. Read `Documentation/Plans/tighten-mix-fable-plan-2026-11-23.md` (sections 1 and 3, change 1 and 3) first.

## What to build (three files, one hook)

1. `Source/kick_alignment_check.py` (library). Input: a final ALS path, the audio folder, the project BPM not needed (the ALS has the markers). It must, read-only:
   - parse the ALS (gzip XML, `xml.etree` for READING only; never write an ALS) into tracks and clips (CurrentStart, CurrentEnd, Loop/LoopStart, WarpMarkers: SecTime in wav seconds, BeatTime beat) and the transitions (overlap = incoming track's first clip start to outgoing track's last clip end, in arrangement beats; source_beat = LoopStart + (arrangement_beat - CurrentStart); beat to wav seconds by interpolating the one-marker-per-beat list). Reuse and simplify the prototypes in `D:\Claude Workspace\Automated DJ Mixes\kick-offsets\pair\pair_lag.py` and `D:\Claude Workspace\Automated DJ Mixes\kick-offsets\measure_kicks2.py` (they are NOT in the repo; read them, then write clean repo code with tests; do not import them).
   - read only short windows of each wav with `soundfile` (seek to frame, read about 150 ms) so memory stays small (the Home PC has 16 GB and a memory gate; never load a whole wav).
   - for every integer arrangement beat in the overlap where BOTH tracks have a kick (kick beat: the 40 to 120 Hz envelope in the window is a clear peak, at least 3x the window median, and a sharp attack in the band above 150 Hz), measure the lag between the two tracks in ms with TWO estimators and report both:
     a. CLICK lag: leading edge of the above-150 Hz envelope (first point above about 10 percent of that kick's peak), plus the cross-correlation of the two above-150 Hz envelope windows, lags plus or minus 30 ms, parabolic sub-sample peak.
     b. MAIN-HIT lag: the steepest rise of the smoothed broadband (40 Hz and up) envelope, or the time the envelope first reaches 50 percent of its peak. Pick one, document why.
   - sign: positive = the INCOMING track's kick is later than the outgoing's. Prove it in a test by delaying one synthetic track a known amount.
   - per transition: number of shared kick beats, median click lag, median main-hit lag, interquartile range of each, first-half and second-half medians (drift), and a verdict.
   - verdict rules (constants at the top of the file, named, with Sam's note that he sets the real figure): `WARN_MS = 4.0`, `FAIL_MS = 8.0` applied to the WORSE of the two median lags in absolute value; fewer than `MIN_SHARED_KICKS = 16` shared kick beats must give `CANNOT_JUDGE` (never PASS); if the click lag and the main-hit lag disagree by more than 4 ms, add the flag `SLOW_ATTACK` (the No Panties case: click lines up, main hit does not) and verdict at least WARN.
2. The picture. For each transition write `Output/Kick Alignment/T<nn> <outgoing short> to <incoming short>.png` (a plain `matplotlib.figure.Figure` with the Agg canvas, never `pyplot`; or if pyplot is used, decorate with `frees_figures` from `Source/automated_dj_mixes/figure_memory.py` and follow the project's picture-memory rule). Layout, like Sam's screenshot: one column per chosen kick beat (4 columns, spread early/middle/late across the overlap, chosen from the shared kick beats; fewer if fewer exist), two rows per column: outgoing waveform on top in red, incoming below in pink, same x axis (minus 40 ms to plus 60 ms around the beat line, in ms), a dashed vertical line at the beat (arrangement beat grid, time 0), small ticks at each track's measured click and main-hit time, and the per-beat lag number as text. Title: transition number, both track names, verdict, median click lag and median main-hit lag. Size about 1800 by 600 px, readable at that size. The picture is for a human (and for Claude) to look at; it must make a 5 ms gap obvious.
3. `Tools/check_kick_alignment.py` (CLI): `python Tools/check_kick_alignment.py <ALS> --audio <dir> [--out <dir>] [--mode warn|fail]`. Prints one line per transition (`T01 Get Right to No Panties: WARN click +7.3 ms main +15.7 ms shared kicks 38 SLOW_ATTACK`), then a final line `LOOK AT THESE PICTURES: <out dir>`. Exit 0 in warn mode always (unless the ALS cannot be read), exit 1 in fail mode when any transition is FAIL or (in fail mode) CANNOT_JUDGE is not allowed to hide a FAIL. Mode default `warn` (Fable: warn only for the first two mixes, then Sam flips it); the env var `KICK_ALIGNMENT_MODE` overrides the default.
4. The hook: `Tools/run_mix_pipeline.sh` runs the CLI once, after the final validate (the `validate_als.py ... --arrangement-report` line, about line 137), writes pictures beside the Output, and prints the verdict lines and the LOOK line into the pipeline log. A failure in the CLI itself must not break the build in warn mode. Do not touch the other phases. Do not edit `commands/mix.md` (it is on the frozen Claude/Codex sync list; the lead handles that).

## Tests (`Tests/test_kick_alignment_check.py`), each must fail before the change and pass after

- Two synthetic kick tracks with a known delay (8 ms): the library reads 8 plus or minus 1 ms, sign correct both ways.
- A soft-attack fixture (a small early click then a main hit 10 ms later) against a sharp kick: click lag near 0, main-hit lag near 10, flag `SLOW_ATTACK`, verdict at least WARN.
- Fewer than 16 shared kick beats: `CANNOT_JUDGE`, never PASS.
- A PNG is written, non-empty, and a re-run is deterministic (same numbers).
- ALS parse: a tiny synthetic ALS (gzip XML written by the test) with two audio tracks and 8 beats of overlap gives the right transition and source-beat mapping.
- Memory: reading windows only (assert no function loads a full file; for example monkeypatch `soundfile.read` to refuse frames above 1 second worth).
- Pipeline hook: the shell script change is checked by executing the CLI against a throwaway tiny ALS and wavs and reading its exit code and output (the project rule: shell must be executed, not just read).

## Acceptance on real data (the lead will re-run these; paste the real output)

Audio folder: `D:\Claude Workspace\Automated DJ Mixes\Projects\21.11.26 Tech House 2026 V2\Audio`. Sets:
- A: `Listening Sets/Tech House 2026 Planned.als`
- B: `Listening Sets/Tech House 2026 Planned Sam Tweak Slide Project/Tech House 2026 Planned Sam Tweak Slide.als` (No Panties' warp markers moved +6.4 ms)

Expected from earlier measurement (click band, cross-correlation): A: T01 Get Right to No Panties about +7.3 ms (WARN or FAIL), T02 No Panties to Taste about -5.5 ms, control T03 Taste to TCFS about +1.1 ms (PASS), T06 and T07 CANNOT_JUDGE (only 2 and 3 shared kick beats). B: T01 about +0.9 ms click. KEY FALSIFIABLE TEST: Sam's screenshot shows that on B the Get Right to No Panties blend is STILL visibly off (the main hit trails the other kick). So on B, T01 must NOT come out as a clean PASS: either the main-hit lag exceeds WARN_MS or the tool must say honestly why not. If your main-hit estimator reads under 4 ms on B T01, say so plainly and look at the PNG yourself, compare with Sam's screenshot, and fix the estimator rather than the threshold. Report the numbers for both sets for all 8 transitions.

## Rules

- Work ONLY in the worktree `D:\Worktrees\dj-kick-align` (branch `work/kick-align-check`). Write nothing to the main checkout, and nothing on C: except tiny temp. Scratch and pictures for your own testing: `D:\Claude Workspace\Automated DJ Mixes\kick-align-build\`.
- Match existing style (look at `Source/validate_beatgrid.py`, `Tools/score_blends.py`, `Source/automated_dj_mixes/figure_memory.py`). No docstring bloat, no backwards-compat hacks, no new dependencies beyond what `requirements.txt` already has (numpy, scipy, soundfile, matplotlib are expected; check).
- Run your new tests, then the full suite with `PYTHONPATH=Source` (and the Audio Analysis Toolkit src if the suite needs it), pytest's `--basetemp` on a folder under `D:\Claude Workspace\Automated DJ Mixes\kick-align-build\pytest` (D: is exFAT, so if three cache or alias tests fail for that reason, name them and show the same three fail on `main`). Report counts.
- Commit on the branch with conventional commits (`feat(kick-align): ...`), body explains the why, ending with the line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Do not merge, do not push.
- Return: the branch commit hash, files changed, test counts before and after, the real-data tables for A and B, and one honest line on anything you could not verify.
