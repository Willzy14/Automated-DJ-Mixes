# Held-out replay — plan V2 (post dual review)

Date: 2026-08-12. Supersedes the V1 sketch. Reviewed by Codex (verdict: HOLD, fixes
below) and MiniMax (verdict: mostly sound, three verified leaks). Every code claim
below was verified directly against the source before being accepted.

## What the correction data actually says

Extracted from `SAM_TWEAKS_DIFF_V1.json` (the machine diff, not the prose summary):

| T | base beats | corr beats | entry level | corr in-swap (src/section) | corr out-swap | zero gates |
|---|---:|---:|---:|---|---|---|
| 1 | 140 | 132 | 0.2 | 64 / drop_3 | 628 / outro_1 | - |
| 2 | 72 | 104 | 0.112 | 64 / drop_1 | 384 / beat_dropout | - |
| 3 | 104 | **295.29** | 0.2 | 96 / drop_1 | 800 / drop_12 | **1564-1572** |
| 4 | 232 | 148 | 0.15 | **64 / intro_2** | 768 / outro_1 | - |
| 5 | 224 | 234.80 | 0.15 | **32 / intro_1** | 672 / outro_1 | - |
| 6 | 128 | 221 | 0.2 | 32 / drop_1 | 748 / outro_1 | - |
| 7 | 136 | 168 | 0.2 | 128 / drop_1 | 704 / outro_1 | - |

- Only **T3** exceeds the 256-beat landmark cap. T3/T5/T6 exceed the 192-beat standard cap.
- T4 and T5 swap on **intro** sections, which the current aligner cannot even propose.
- The T3 protected window is real and exact: 8 beats at arrangement 1564-1572.
- The diff contains **no** loop/repeat-group fields. Any claim about phrase sizes or
  repeat counts sourced "from the diff" is unfounded; the phrase figures live in the
  prose report and the corrected ALS only. (A peer review asserted such fields; they
  do not exist. Note also that a "4-bar phrase" and "16 beats" are the same thing.)

## Verified facts about the current code

- `_clean_tail_loop` (propose_arrangement.py:573) is reachable **only** when
  `USE_ALIGN_ENGINE = False`. In production it is dead code. The live chooser is
  `align_engine.pick_cue_bounded_drum_loop`, which already sorts `loop_windows`
  latest-first and already blocks `vocal_regions` and `fills`. Its length preference
  is `(8, 4, 7, 6, 5, 3, 2, 1)` **bars** — so 3-bar (12-beat) loops are already
  candidates, and 8/4 bars (32/16 beats) are already preferred.
- Overlap caps are defined **three times**: `align_engine` (bars), `propose_arrangement`
  (beats), `mix_plan` (beats). `MAX_LOOP_EXTENSION_BARS = MAX_OVERLAP_BARS - PHRASE_GRID`,
  so raising the cap silently raises the loop budget.
- `mix_plan.validate_mix_plan` whitelists `overlap_policy` to
  `("standard_48", "named_landmark_64")` and otherwise **trusts the string** — it does
  not demand evidence.
- `apply_automation._NEXT_ID` is a module global with no reset.
- `apply_automation._load_arrangement_report` falls back to a newest-first glob of
  `*ARRANGEMENT_REPORT.json` beside the ALS.
- `align_engine._incoming_drop_anchors` proposes **drop starts only**.
- Long blends hard-code sneak `0.15` in `apply_automation`.

## Revised plan — three commits, not one

### Commit 1 — refactor + isolation only (must be behaviour-neutral)

- Single source of truth for overlap/loop caps; the derived loop-extension budget
  becomes explicit rather than a subtraction from the cap.
- Frozen `TransitionPolicy` object threaded explicitly through alignment, loop
  planning, arrangement validation, report generation, automation and reconciliation.
  **No module-constant mutation.**
- Reset/inject the automation ID counter; require an explicit arrangement report in
  production and disable the newest-report fallback there.
- Require `report.mix_plan.plan_hash == mix_plan.plan_hash` in
  `validate_mix_plan_als.py`.
- **Parity proof:** compare **decompressed ALS XML**, not the gzip bytes (the writer
  records a timestamp, so the compressed file is not reproducible). Plus an
  `interim -> sam -> interim` run in ONE process asserting the two interim outputs match
  — this is the test that catches policy leakage.

### Commit 2 — `sam_v1` behaviour

- Independent `entry` / `bass_swap` / `exit` / `protected_windows` anchors. Planned cues
  are stored separately from **final resolved anchors**, which are frozen only after all
  loop/cut mutations. Anchors carry track-instance / clip / cue **IDs**, not bare source
  beats (repeated source material makes bare beats ambiguous).
- **Broader swap candidates**: enumerate intro-section entry points (`intro_1`,
  `intro_2`, ...) alongside drop starts, so T4 (intro_2 @ 64) and T5 (intro_1 @ 32) are
  proposable at all.
- **Explicit "no loop / natural exit" candidate** that competes with loop candidates and
  wins when the cue is already satisfied (this is T4's actual fix).
- **Exit selection + enactment**: an exit candidate selector plus clip truncation, so
  T1's "finish on the final dropout" is actually performed rather than just recorded.
- **Protected windows wired into `apply_automation`** — a dataclass field without an
  automation consumer is dead metadata. Raw dropout landmarks drive automation without
  being promoted to structural clips.
- **Loop scoring inside the live chooser** (`pick_cue_bounded_drum_loop`), adding groove
  continuity / energy / distance-to-exit-cue. 3-bar (12-beat) candidates are **soft
  penalised, never excluded** — Sam's own note says 12 happened to work.
- **Sneak level frozen** at one deterministic value for this A/B and recorded in the
  MixPlan. A free 0.11-0.20 band adds an uncontrolled variable to the experiment.

### Commit 3 — extended lane, experimental scope only

Commissioned delivery stays capped at 64 bars until this lane passes a held-out listen.

The extended contract must **freeze and independently validate**, not trust a string:
intro candidate ID + exact source range + length (exactly 16 or 32 beats); bass-off,
vocal-free, fill-free status; groove-continuity score; repeat count (cap 6 — the observed
maximum); the deterministic gain and its reason; exit cue ID + kind + confidence +
distance in beats, from a **semantic whitelist** (not any section boundary); bass-swap
cue IDs on both clocks; protected-window IDs crossed; and the final resolved geometry.
Predeclared tolerance <= 4 beats. Requires `entry < swap < exit`, adequate post-swap fade
room, and no masked high-confidence dropout. The validator rejects an extended policy
string that arrives without its evidence.

Codex's circularity warning is the reason for the whitelist: if the exit is chosen by
searching for a nearby boundary and that same boundary is then cited as the evidence
justifying the extension, the gate proves nothing.

## Listening test — corrected

**Neutral .als filenames are not blind.** Ableton visually discloses transition length and
arrangement shape the moment the set opens. So:

- Compare **metadata-stripped WAV renders**, randomized, never labelled A/B.
- Whole-mix pair plus randomized per-transition excerpts.
- Sealed mapping generated after rendering.
- **One A-vs-A duplicate as a noise twin.** If a confident systematic difference is
  reported on the twin, the protocol is not discriminating and the result is void.
- All automated checks (ALS gate, MixPlan reconciliation, report binding) pass before
  anything is heard.
- Log which `sam_v1` components fired per transition — a bundled win cannot otherwise be
  attributed.
- Per-transition record: preference, confidence, bass/vocal clash, masked dropout,
  awkward repetition, exit quality, technical failure.

## Pre-registered falsification record

- **CLAIM:** `sam_v1` yields more musically acceptable held-out transitions than
  `interim_v1`, with no new technical or masking failures.
- **NULL:** no consistent blind preference on the same tracks and sequence.
- **NOISE TWIN:** randomized A-vs-A duplicate.
- **KILL:** park or revise if B wins fewer than 5 of 7, loses more than 1, or causes any
  beat/grid error, audible bass/vocal clash, masked protected dropout, or an
  unjustified extended-lane authorization.
- **SAMPLE:** one held-out mix, ~7 transitions, one expert — enough for a fatal-error
  screen and a directional lead, underpowered for deployment.
- **REGIME:** must survive a second held-out mix with different structural patterns
  before becoming a default.

## Known blind spots, stated up front

- **T1- and T7-type behaviour may not differentiate.** T7's real win was exposing a
  masked dropout, which depends on the deferred promotion rules. Expect little or no
  audible difference there; the signal lives in T2-T6-type transitions.
- Transition accents (percussion hit + audible decay tail) remain deferred so that a
  win or loss stays attributable.

## Held-out material

15 deep/soulful-house tracks at `Test Project/12.08.26 Heldout Replay/Audio`, verified
by normalised title against all 77 tracks used in the 23.06.26, 24.06.26, 25.06.26 Car
Mix and 16.07.26 Fresh Mix projects. One ambiguous match was dropped rather than risk
contaminating the held-out set. Final set is whatever passes the beatgrid gate.
