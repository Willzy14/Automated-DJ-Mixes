# Build brief: replay Sam's hand tweak through the blend planner (Tools/score_tweak.py)

Written 2026-11-24 by Claude (Sonnet 5.5) for Sam. Builder: a Sonnet subagent. Reviewers: MiniMax plus an independent Opus subagent, then the lead's executing check. No em dashes anywhere (code, comments, output, docs, commit text). Python output ASCII only.

## Why

The blend planner (`Source/phrase_blend.py`: `plan_blend`, `candidates`, `_score`, `BlendPlan`; driven by `Tools/plan_mix_decisions.py`) plans every transition from hand-set cost rules. Nobody knows whether it plans the BEST mix or only a rule-satisfying one. Sam hand-tweaked the planner's own output on a real mix; his tweak is the only independent evidence of what "better" means for those eight blends. Fable's plan (read `Documentation/Plans/tighten-mix-fable-plan-2026-11-23.md`, section 2 and change 4, and `Documentation/Plans/tighten-mix-fable-brief-2026-11-23.md`) asks for exactly this replay: for each transition put Sam's placement through the planner and report whether it is the planner's top pick, one of its listed alternatives, reachable but ranked N (with the cost term that cost it), or outside the planner's vocabulary. `Tools/score_blends.py` already does this for the seven DFTD answer-key blends; model the new tool on it.

## Inputs

- Baseline set (what the planner produced, the bounce Sam heard): `Listening Sets/Tech House 2026 Planned.als`.
- Sam's tweak: `Listening Sets/Tech House 2026 Planned Sam Tweak Project/Tech House 2026 Planned Sam Tweak.als` (same nine tracks in the same order; Sam changed geometry on transitions 4 to 8: entries, overlaps, intro trims, a cut break, loops; transitions 1 to 3 untouched).
- The planner's content for these tracks: sections, fills and per-bar bass/kick come from the pipeline's analysis. The lead is running the full pipeline for this mix on this machine right now (project `D:\Claude Workspace\Automated DJ Mixes\Projects\21.11.26 Tech House 2026 V2\`, cache `_Stem Analysis`, Output with `MIX_PLAN.json`, `Sections V<N>.als`, and the decisions file the planner wrote). It will finish during your build; check for `Output\runmix.log` ending in "DONE". Until it exists, develop and test on the DFTD data in `Documentation/Golden Sections/` (`Content 2026-11-20 DFTD.json`, `Answer Key 2026-11-20 DFTD.json`, `Holdout ...`, `Pairs/`) and on synthetic fixtures. How to turn the cache into planner tracks: see `convert_track`, `track_order` and `write_decisions` in `Tools/plan_mix_decisions.py` (reuse, do not copy).
- Geometry decoding from an ALS: `Source/learn_from_correction.py` already parses entries, intro trims, swap moves per track, loops found by shape, outro cuts and overlap from an ALS (`parse_sections_als`, `ordered_tracks_from_clip_records`, `extract_track_automation`); reuse it. The bass-swap points are in the ChannelEq low band automation (the learner's `bass_points`); Sam's tweak may have changed them too.

## What to build

`Tools/score_tweak.py` (CLI) plus a small library module if cleaner (`Source/tweak_replay.py`), `Tests/test_score_tweak.py`.

1. Decode, per transition i (outgoing track i, incoming track i+1), the PLACEMENT in each of the two ALS files in the planner's own terms (the `BlendPlan` fields: `in_starts_at_out_bar`, `bass_switch_out_bar`, `out_end_bar`, `out_looped_bars`, `in_intro_looped_bars`, `out_cut_bars`, `in_intro_cut_bars`, loop source, trim) with bars in the outgoing track's bar numbers as the planner uses them. Say in the report which fields could be decoded exactly and which only approximately; refuse (say "not decodable") rather than guess.
2. Round-trip check of the decoder: for the BASELINE ALS, decoding must give back the placement the planner itself wrote for that transition (the planner's `plan_blend` result for the same pair, or the decisions file), to within one beat. A decoder that cannot reproduce the planner's own output is not trusted: report the mismatch and stop.
3. For every transition report: Sam's placement; the planner's top pick; whether they are the same (`full_key`); if not, where Sam's placement sits among `candidates(...)` (rank N of M by cost, or in the planner's `alternatives`, or "outside the vocabulary" with the reason: for example a 1-bar intro cut the planner forbids, a tail stop without a loop that `MIN_SIDE_BARS` forbids); the cost of both placements term by term (`BlendPlan.terms`, via `_score` on Sam's placement) and the term that makes the difference.
4. A trivial baseline printed beside every transition: "16 bars in, 16 bars out, swap on the outgoing's outro start" (see `Documentation/Plans/v5-sam-tweaks-analysis.md` for the shape) scored by the same cost function, and whether Sam's placement or the planner's pick is closer to it. If the planner does not beat the trivial baseline on cost AND does not match Sam more often, the report must say so plainly.
5. A summary: transitions where Sam changed nothing (planner placement kept), top pick equal to Sam's, Sam's in the planner's top 3, vocabulary gaps (named), and a list of the rule-like patterns visible (for example "no intro trims" or "swap onto the incoming drop"): list them as observations with the bar numbers, NOT as fitted rules. Do not build a learned scorer (the earlier attempt lost to a trivial baseline).
6. Output: a plain-text report and a JSON file in `--out`, nothing else written; inputs never modified.

## Acceptance (the lead re-runs; paste real output)

- Tests with synthetic ALS pairs: decoding recovers known placements; the round-trip refusal fires on a deliberately broken decoder; vocabulary-gap and rank reporting work on fixtures where the answer is known (rank 1, rank 3, outside vocabulary); planner-equals-Sam case.
- Real data: on the Tech House set once the pipeline project exists (until then DFTD), all eight transitions with the table above, the decoder round trip result on the baseline, and the summary. Transitions 1 to 3 (untouched by Sam) must come out "Sam kept the planner's placement" (that is a check on the decoder).
- Mutants you must kill and prove: decoder off by one bar; rank computed against the wrong candidate list; trivial baseline using the planner's pick; swap point read from the wrong track; the round-trip check disabled.

## Rules

- Work ONLY in the git worktree `D:\Worktrees\dj-score-tweak` (branch `work/score-tweak`, already created, locked, based on current main). Nothing written in the main checkout; nothing on C: except tiny temp. Scratch: `D:\Claude Workspace\Automated DJ Mixes\score-tweak-build\`.
- Do not change `Source/phrase_blend.py` behaviour. Additive helpers only, each with a test, if you truly need a public hook (say why).
- Match existing style (`Tools/score_blends.py`). No docstring bloat, no new dependencies.
- Tests: yours, then the full suite with `PYTHONPATH=Source` and `--basetemp` under your scratch (D: is exFAT; name exFAT-only failures and show they also fail on main). Known unrelated failure: `Tests/test_kick_model_integration.py::test_adapter_defaults_and_smoothing_do_not_import_torch`.
- Commits on the branch, conventional (`feat(score-tweak): ...`), body explains why, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Do not merge, push, or use `git stash`.
- Never loosen a tolerance to make something pass. Say plainly what you could not verify.
- Return: commit hashes, files changed, test counts, the real-data report (all eight transitions) and summary, the round-trip result, mutants killed, and one honest line on anything unverified.
