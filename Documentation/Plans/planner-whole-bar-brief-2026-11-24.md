# Build brief: planner falls back to its best WHOLE-BAR alternative before the rule-based aligner (found by the fresh mix, 2026-11-24)

Written 2026-11-24 by Claude (Sonnet 5.5) for Sam. Builder: a Sonnet subagent. Reviewers: MiniMax plus an independent Opus subagent, then the lead's executing check. No em dashes anywhere. Python output ASCII only.

## The failure (real, on a never-seen mix)

Fresh six-track mix project `D:\Claude Workspace\Automated DJ Mixes\Projects\24.11.26 Fresh Mix\` (Output\planner.log, Output\runmix.log, Output\phase2.log). The pipeline planned 4 of 5 pairs, then for pair 3 (Nick Curly - All Smiles into Michael Canitrot - Renaissance) wrote `FALLBACK T3: decision for pair 3: entry_out_bar 168.5 is not a whole bar - decisions name whole musical bars, not fractional positions`, handed the pair back to the rule-based aligner, which raised `No paired section/dropout alignment ... inside the 16-48.0 bar window` and stopped phase 2. The planner's top pick for T3 (cost 7.81) was entry 168.5, swap 176, end 199.5 (it lines the swap up on the incoming's fill end at incoming bar 7.5). Its first listed alternative was entry 152, swap 160, end 184 (cost 8.37), all whole bars and a valid plan. Alternative 2 (entry 176.5) is fractional too.

## What to build

In `Tools/plan_mix_decisions.py` (`decision_for_plan`, `write_decisions`; read them and `Source/phrase_blend.py` `BlendPlan`, `Alternative`, `candidates`, `plan_blend`) and nowhere else unless truly needed:

1. When the top plan for a pair cannot be written as a decision because a bar value is fractional, try the planner's alternatives (and, if the alternatives list does not hold enough, `candidates(...)`) in cost order and use the FIRST whose decision values are all whole bars and which the existing decision validation accepts. Only if none exists does it fall back to the rule-based aligner as today. Do NOT round a fractional value to a whole bar (that changes the musical alignment the planner scored).
2. Say it plainly in the planner log and the decisions file: `T3: top plan (entry 168.5, cost 7.81) has a fractional bar; used alternative N (entry 152, swap 160, end 184, cost 8.37, +0.56)`. The cost difference to the top pick is always printed. A pair that had to use an alternative is listed in a summary line at the end (`n pairs used a whole-bar alternative: T3 (+0.56)`), and the same count goes into the planner's JSON.
3. `--allow-partial` and every existing behaviour for whole-bar tops are byte for byte unchanged (existing tests pass unchanged; the decisions file for a whole-bar mix is identical to before).
4. Do not touch `Source/phrase_blend.py` behaviour (no cost or rule changes). If a public helper there is truly needed, additive only with a test and a reason.

## Tests (each must fail on main and pass now; prove it)

- Fixture where the top plan is fractional and alternative 1 is whole: alternative 1 is used, logged with the cost difference.
- Top fractional, alternative 1 also fractional, alternative 2 whole: alternative 2 used.
- Top fractional and no whole alternative anywhere: falls back to the rule-based aligner exactly as before (the FALLBACK line unchanged).
- Whole-bar top: nothing changes (byte-identical decisions file against a stored expectation).
- Rounding is never done (a fractional value is never written).
- Mutants to kill and prove: always taking alternative 1 even if fractional; choosing the highest cost whole alternative instead of the lowest; skipping the validation of the chosen alternative; rounding the fractional top.

## Acceptance (the lead re-runs; paste real output)

1. Re-run the planner step on the fresh mix: copy the project's `Output` (not Audio or `_Stem Analysis`) to your scratch, run `Tools/plan_mix_decisions.py` the way `Tools/run_mix_pipeline.sh` does (read the script for the exact arguments) against the fresh mix cache and show pair 3 now uses a whole-bar alternative and the decisions file has all 5 pairs (or says exactly which pair still falls back and why).
2. Run it on the Tech House 2026 project (`D:\Claude Workspace\Automated DJ Mixes\Projects\21.11.26 Tech House 2026 V2\`) and show its decisions file is byte-identical to the one the pipeline wrote (all tops whole-bar).
3. Then run `Tools/run_mix_pipeline.sh` on a COPY of the fresh mix project (copy Audio and `_Stem Analysis`, never the original) with RESUME unset and the order `Disciples,Julien Fade,Nick Curly,Canitrot,Loaded Clipz,600 Degrees` (6 tracks) and report how far it gets and what stops it, if anything (the kick check and grid phase steps run at the end; their output belongs in the report). Use a branch checkout of your worktree for the scripts (run from your worktree root).

## Rules

- Work ONLY in the git worktree `D:\Worktrees\dj-planner-whole` (branch `work/planner-whole-bar`, already created, locked, based on current main). Nothing written in the main checkout or the original project folders; nothing on C: except tiny temp. Scratch: `D:\Claude Workspace\Automated DJ Mixes\planner-whole-build\`.
- Match existing style. No docstring bloat, no backwards-compat hacks, no new dependencies.
- Tests: yours, then the full suite with `PYTHONPATH=Source` and `--basetemp` under your scratch (D: is exFAT; name exFAT-only failures, show they also fail on main). Known unrelated failure: `Tests/test_kick_model_integration.py::test_adapter_defaults_and_smoothing_do_not_import_torch`.
- Commits on the branch, conventional (`fix(planner): ...`), body explains why, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Do not merge, push, or use `git stash`.
- Say plainly what you could not verify. Return: commit hashes, files changed, test counts, tests proven failing on main, mutants killed, the three acceptance results with real output.
