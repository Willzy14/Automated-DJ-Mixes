# Hardening Tracker — Module-by-Module

> Sam's directive (2026-08-20): list every module, then harden each one — review it, make sure it
> does everything it's supposed to, then follow it downstream to make sure it's wired into
> everything it's supposed to be wired into. **Living document**: a new module gets a row on
> creation; a module's "Wired downstream" cell is re-verified whenever it changes. Ordered by
> blast radius — the decision-making core gets deep passes, the long tail gets a light
> wired-or-orphaned check.
>
> Status legend: ✅ hardened (deep pass done, findings closed) · 🔶 in progress · ⬜ not started ·
> 🗑 pending removal (Rekordbox purge, item #10) · 💤 light-check only (low blast radius).
> "Wired?" = does its output actually reach a decision (the 2026-08-17 audit's core question).

## Core decision path (deep passes required)

| Module | Purpose | Status | Tests? | Wired? | Open findings |
|---|---|---|---|---|---|
| `stem_detector.py` | Section detection (kick/bass/energy cues, labels, soft rules) | 🔶 | strong (energy, soft-rules, property) | yes — core | R2 discriminating test (in flight); Codex CONCERN: raw-presence glitch sensitivity on boundaries (def5062 follow-up, for Kimi+Codex pass) |
| `align_engine.py` | Swap/anchor decisions (`_mix_cues`, tail rescue, CueConfig) | 🔶 | strong (baseline net, rescue, blocker-batch) | yes — core | bass_out failed held-out validation (needs rank-tuple reorder); major_cues/bass_regions/loop_windows-as-anchor still unwired (board) |
| `propose_arrangement.py` | Arrangement proposal, CLI, hints/TrackInfo | 🔶 | good | yes — core | literal-policy sweep (in flight); hints auto-derive plumbing (--write-hints path, queued) |
| `apply_automation.py` | Volume/EQ automation, snap, LUFS, reconcile call | 🔶 | good (blocker batch) | yes — core | Codex BLOCKER 6 remainder: missing automation targets only WARN (hardening in flight); `_wav_for_track` prefix-collision bug (board, 2026-08-17) |
| `als_generator.py` | ALS write | ⬜ | partial | yes — core | Codex: template truncation silently drops tracks (hardening in flight); clip-splitting at landmark swaps (V9 root cause — direction decision at Kimi+Codex pass) |
| `apply_loops.py` | Loop clip insertion | ⬜ | partial | yes — core | Sam's V10 ear-notes: loops cut in wrong place, one with silent gap — check after clip-splitting decision; loop windows never quality-checked against band energy (Tier A Phase 2 candidate) |
| `validate_als.py` | ALS corruption gate | 🔶 | good | yes — gate | Codex: no track-count/devices/envelopes checks (hardening in flight) |
| `validate_mix_plan_als.py` | MixPlan↔ALS reconcile gate | ✅ (2026-08-20 auto-wired) | good | yes — gate | reconcile's clip-boundary assumption for landmark cues — pending direction decision |
| `kick_model_adapter.py` | Kick Detector V3 bridge | 🔶 | good | yes — core | drums-stem cache (in flight) |
| `stem_section_probe.py` | Demucs envelopes + Tier A features + npz cache | 🔶 | good (Tier A) | yes — core | Tier A Phase 2 (wire features into decisions) pending review |
| `Tests/test_alignment_baseline.py` | The 380-pair regression net | 🔶 | n/a (is a test) | n/a | Codex BLOCKER 2: pin overlap_policy + full cue provenance + exception types (extension in flight) |

## Pipeline support (medium passes)

| Module | Purpose | Status | Tests? | Wired? | Open findings |
|---|---|---|---|---|---|
| `orchestrator.py` | Phase 1a pipeline driver | 🔶 | partial | yes | RB purge in flight; Codex CONCERN: sections-layout default doesn't force the stem detector (legacy MIK route possible on bare invocation) |
| `mix_plan.py` | Frozen MixPlan artifact | ⬜ | partial | yes | — |
| `warping.py` / `warp_contract.py` | Warp markers + certified grids | ⬜ | good | yes | — |
| `automation.py` | Envelope primitives | ⬜ | partial | yes | — |
| `sequencer.py` | Track ordering | ⬜ | partial | yes | — |
| `validate_beatgrid.py` | Beatgrid gate | ⬜ | good | yes — gate | audit note: independent `.asd` tick ruler computed then discarded (board, 2026-08-17) |
| `mik_reader.py` | Mixed In Key DB | ⬜ | partial | yes | — |
| `musical_landmarks.py` / `extract_musical_landmarks.py` | V3 dropout landmark extraction | ⬜ | partial | yes | — |
| `learn_from_correction.py` / `analyze_correction_diff.py` | Correction learning | ⬜ | partial | **no — closed circuit** (audit 2026-08-17: changes zero output; board card "wire it or retire it") | decide at hardening pass |
| `features.py` / `amplitude_analysis.py` / `cue_candidates.py` | Analysis helpers | ⬜ | partial | cue_candidates dead in production path (audit) | fold or retire |
| `config.py` | Constants | 💤 | n/a | yes | — |

## Pending removal (item #10, in flight — do not harden)

| Module | Status |
|---|---|
| `rekordbox_reader.py` | 🗑 → Archive |
| `rekordbox_waveform.py` | 🗑 → Archive |
| `desktop_analyzer.py` (RB halves) | 🗑 partial delete |
| `phrase_viz.py` (per-beat RB path) | 🗑 partial delete — `segments_from_stem_sections` survives |
| `analysis.py` (`enrich_from_rekordbox`) | 🗑 function delete, shim survives |

## Visualization & review (light checks)

| Module | Status | Note |
|---|---|---|
| `transition_review_viz.py` / `loop_review_viz.py` | 💤 | now MANDATORY in /mix Phase 4 (2026-08-19); known wart: FULL PNGs draw pre-arrangement positions |
| `sections_blind_viz.py` | 💤 | its 3-band code was the Tier A prior art; check for drift vs the cached tiera_* keys |
| `section_placement_viz.py`, `waveform_preview.py`, `report.py` | 💤 | wired-or-orphaned check only |
| Diagnostic/research scripts (`probe_*`, `refit_*`, `reverify_*`, `isolate_*`) | 💤 | wired-or-orphaned check; `probe_render_flam.py` has ZERO callers despite proving the only real-render check (board, 2026-08-17) |

## Standing findings ledger (cross-module)

- **Demucs is nondeterministic run-to-run on the RTX 3050** (proven 2026-08-20 during the drums-cache
  build: two identical cold runs differ in every Demucs-derived envelope; only `hop_t`/`mix` are
  deterministic). Consequences: (a) "byte-identical to a fresh run" is unachievable for anything
  downstream of Demucs — caches are MORE reproducible than re-analysis; (b) small boundary drift on
  re-analysis (e.g. Renegades 2026-08-19) is partly platform noise, not signal — never chase a
  1-bar re-analysis diff without checking against a cached run first.

- The observation gap: nothing checks RENDERED audio (board, 2026-08-17) — biggest structural hole left.
- The learning loop is a closed circuit (board, 2026-08-17).
- Stereo width as a cue signal: Tier A Phase 1 shipped the measure (2026-08-20); Phase 2 wires it.
- Mono-input path yields zero-length tiera arrays (Tier A wart, non-blocking — corpus all stereo).
