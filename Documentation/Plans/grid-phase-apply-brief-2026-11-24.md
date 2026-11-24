# Build brief: apply mode for the grid phase fix (MixPlan reconciliation accepts a recorded grid phase)

Written 2026-11-24 by Claude (Sonnet 5.5) for Sam. Builder: a Sonnet subagent. Reviewers: MiniMax plus an independent Opus subagent (Codex is capped until 2026-11-27, Kimi quota spent), then the lead's executing check. THIS TOUCHES A TRUST GATE (the MixPlan reconciliation), so it is held to the assembly-line standard: the gate must get STRICTER or stay equal for everything it checked before, and every new acceptance path must be provably unforgeable by accident. No em dashes anywhere (code, comments, output, docs, commit text). Python output ASCII only.

## Why

The grid phase fix (merged 2026-11-23: `Source/grid_phase_fix.py`, `Tools/fix_grid_phase.py`, hook in `Tools/run_mix_pipeline.sh`, TOOLBOX entry; read them first) writes `Output/Sections V<B> Phased.als` (drift fit plus slides on the warp markers, text-patched) and a report beside the original. Sam confirmed by ear that its output is as tight as his hand result, and said: "get the auto swap sorted". So the pipeline should be able to make the Phased file the FINAL file by itself. It cannot today: the MixPlan reconciliation (`Source/validate_mix_plan_als.py`, `Source/automated_dj_mixes/warp_contract.py`, `Source/automated_dj_mixes/mix_plan.py`) pins, per track, `warp_marker_count`, `warp_grid_sha256` (sha256 of the canonical JSON of the exact (SecTime, BeatTime) float pairs, see `summarize_warp_grid`) and `source_grid_bpm`, and fails the phased file with "warp grid mismatch" on every track the tool moved (the builder of the grid phase fix verified this; `validate_als.py` passes it).

## The design (decided by the lead; challenge it only with a concrete hole)

Do NOT loosen the pinned contract. Instead the grid phase tool RECORDS exactly what it changed, and the reconciliation verifies the phased ALS against that record while the pinned hash is still checked against the ORIGINAL markers.

1. The grid phase tool writes `Output/Grid Phase/grid_phase.json` whenever it writes a phased ALS (and only then). Contents: `schema` ("grid-phase/1"), `original_als_sha256`, `phased_als_sha256` (of the written phased file bytes), `tool` and the constants used (MAX_DRIFT_MS, MAX_SLIDE_MS), and per CHANGED track (key: the track's display name AND its document index): `original_markers` (list of [SecTime, BeatTime] as exact float reprs, one per WarpMarker of the track, in document order for the track's first clip, the same list the grid hash is made from), `max_abs_shift_ms`. Unchanged tracks are not listed. Use `repr(float)` or a string so floats round-trip bit-exactly through JSON (check `json.dumps` of floats round-trips on this Python; if not, store strings).
2. `Source/validate_mix_plan_als.py` gets an optional `--grid-phase <grid_phase.json>`. With it:
   - verify `phased_als_sha256` equals the sha256 of the ALS file being reconciled (a stale or foreign record fails);
   - for every track listed in the record: the grid hash, marker count and `source_grid_bpm` are computed from the RECORDED original markers (same canonical function, `summarize_warp_grid` logic) and must equal the plan's pinned contract exactly (so the pin is still honoured, against the original grid); then the ALS's actual markers must match the record: same count, identical BeatTime values, and for every marker |ALS SecTime minus recorded SecTime| <= `MAX_PHASE_SHIFT_MS` = 30 ms (a hard cap in the validator itself, not read from the record), and the markers of the ALS must stay strictly increasing in SecTime and BeatTime;
   - EVERY clip of a changed track must carry the same marker list (as today: `AudioClips on the track do not share one warp grid`);
   - a track that is NOT in the record must still match the pinned contract exactly (nothing may change silently), and a record entry for a track that is not in the plan or not in the ALS fails;
   - everything else the reconciliation checks (arrangement positions, warp mode, loops, etc.) is unchanged and still runs.
   - Without `--grid-phase` the validator behaves byte for byte as before (all existing tests pass unchanged).
   - Report the phase in the reconciliation output JSON and the PASS line (`grid_phase: N tracks, max shift X ms`) so a phased reconciliation can never be mistaken for a plain one.
3. `Source/grid_phase_fix.py` / `Tools/fix_grid_phase.py`: write `grid_phase.json` (item 1) with the same atomic/partial-file discipline as the ALS; remove a stale one with the stale phased file; a REFUSED or NOTHING_TO_DO run writes no record and leaves none behind.
4. `Tools/run_mix_pipeline.sh`: `GRID_PHASE_MODE` becomes `off` (case-insensitive), `suggest` (default, as now) or `apply`. `apply`: after the grid phase step wrote a phased file AND a record, run in this order and stop at the first failure keeping the original as final (loudly, never failing the build): (a) `validate_als.py` on the phased file; (b) the MixPlan reconciliation of the phased file with `--grid-phase` into `Output/MIX_PLAN_RECONCILIATION_PHASED.json`; (c) the kick check on the phased file must not have made any judged blend worse (the tool already guarantees this; do not duplicate, just require the tool's result was PHASED). If all pass, the phased file becomes the final: print `final = Sections V<B> Phased.als (grid phase applied)`; otherwise print `final = Sections V<B>.als (grid phase NOT applied: <reason>)`. The existing reconciliation of the unphased original keeps running in every mode. Any unknown mode value acts as `suggest` (as now), case-insensitive `off`.
5. Update the TOOLBOX entry, the module docstring, and delete the "apply mode needs..." OPEN note. Do NOT edit `commands/mix.md` (frozen sync list; the lead does it).

## Hardening the lead wants (each needs a test that fails without it)

- A record whose `original_markers` hash does not equal the pinned hash must fail even if the ALS matches the record exactly (forging a record to bless any ALS).
- A shift of 30.1 ms on one marker fails; a shift of 29.9 ms passes; the cap lives in the validator.
- Changing one BeatTime, dropping one marker, swapping two markers, a non-monotonic result, a NaN or infinite value, all fail.
- The record for the right tracks but the wrong ALS file (hash) fails; a record edited by hand to list an extra untouched track whose markers differ from the pin fails.
- A phased ALS reconciled WITHOUT `--grid-phase` still fails exactly as today (regression).
- `grid_phase.json` malformed, wrong schema, missing keys: a clear failure, never a crash trace, never a pass.
- Memory/time: no whole-wav reads; the validator only reads XML and JSON.

## Real data (the lead re-runs; paste real output)

Use a project that already has MIX_PLAN.json, ARRANGEMENT_REPORT.json and the final ALS and audio on this machine: `Test Project/06.10.26 DFTD Tech House/Output/` (`MIX_PLAN.json`, `ARRANGEMENT_REPORT.json`, `Sections V4.als`; audio under the project's `Audio/`; pass `--audio`). Steps: (1) reconcile the original (must PASS as today); (2) run the grid phase tool on it (it produced PHASED with drift on four tracks); (3) reconcile the phased file WITHOUT the record (must FAIL, same message as before); (4) reconcile WITH the record (must PASS and print the grid_phase line); (5) tamper (edit one recorded original SecTime by 1 microsecond; edit one phased marker by 31 ms; swap the record from another run) and show each FAILS with a precise message. If the DFTD mix gives NOTHING_TO_DO or REFUSED, say so and use a synthetic project built from the tests' fixtures instead. Then run the hook end to end on a throwaway tiny project (the pipeline tests have a stripped copy and stubbed python; add real-CLI coverage where cheap).

## Rules

- Work ONLY in the git worktree `D:\Worktrees\dj-grid-apply` (branch `work/grid-phase-apply`, already created, locked, based on current main). Nothing written in the main checkout; nothing on C: except tiny temp. Your scratch: `D:\Claude Workspace\Automated DJ Mixes\grid-apply-build\`.
- Match existing style. No docstring bloat, no backwards-compat hacks, no new dependencies.
- Tests: run yours, the grid phase and kick check tests, the pipeline tests, then the full suite with `PYTHONPATH=Source` and `--basetemp` under your scratch (D: is exFAT; name any exFAT-only failures and show the same fail on main). Known unrelated failure: `Tests/test_kick_model_integration.py::test_adapter_defaults_and_smoothing_do_not_import_torch`.
- Mutants you must kill and prove: the record hash check removed; the 30 ms cap removed or raised; the pinned-hash comparison done against the ALS markers instead of the recorded originals; the stale-ALS-hash check removed; apply mode accepting a failed reconciliation; the hook ignoring the reconciliation exit code; a track outside the record allowed to differ.
- Commits on the branch, conventional (`feat(grid-phase): ...`), body explains why, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Do not merge, push, or use `git stash` (a hook refuses it).
- Never lower a threshold to make something pass. Say plainly what you could not verify.
- Return: commit hashes, files changed, test counts before and after, the real-data steps 1 to 5 with real output, tests proven failing on main, mutants killed, and one honest line on anything unverified.
