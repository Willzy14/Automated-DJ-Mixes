# Automated DJ Mixes — Copilot Instructions

## Project Overview
Automated DJ mix pipeline for Wired Masters showreels. Takes pre-tagged dance tracks and produces a ready-to-review Ableton Live 12 session via template-based ALS XML patching. Multi-signal cue candidate detection feeds bass-to-bass transition planning.

> **⚠ This file is stale (May 2026 priorities below). For the live state read `Documentation/AI_CONTEXT.md`.** As of 2026-06-11 evening: the warp/cut regression is **FIXED in code, pending Sam's ear on the re-render**. Two causes — (1) two-clock bug (stem sections cut on librosa's quantized BPM while audio warped to the RB grid) → fixed by `warping.sec_to_clip_beats` + grid-derived detector params; (2) Todd's grid PHASE-shifted +70ms → fixed via `Hints/grid_overrides.json`. New `Source/validate_beatgrid.py` gate hard-stops bad grids in sections-layout. Suite 75/75. See memory `project-warp-beatgrid-bug`.

## Current Project Status (2026-05-20)

| Component | Status |
|---|---|
| Camelot sequencing | ✅ |
| Per-beat warp markers from Rekordbox grid | ✅ |
| LUFS-based gain offsets | ✅ |
| Rekordbox PSSI phrase parsing (RB7 manual binary) | ✅ |
| Rekordbox PWV5 waveform parsing | ✅ |
| Mixed In Key 11 reader (GEOB + SQLite) | ✅ |
| Amplitude-envelope analysis (1s RMS) | ✅ |
| Visual hints workflow (4-field schema, hint gate enforced) | ✅ (2026-05-19) |
| `last_bass_drop_sec` natural-fill alignment | ✅ (2026-05-19) |
| 16-beat HARD phrase snap (no tiered fallback) | ✅ (2026-05-19) |
| Per-track + per-transition viz PNGs with tiered phrase grid | ✅ |
| Blank-canvas preview PNGs (for hint authoring) | ✅ |
| Visual review gate (VISUAL REVIEW REQUIRED + REVIEW_VNN.md) | ✅ |
| Per-track bar alignment HARD validation | ✅ |
| Phrase boundary HARD validation (16-beat) | ✅ (2026-05-19) |
| Overlap range 16-80 bars | ✅ (2026-05-19, was 16-48) |
| ALS generation (template-based, multi-clip) | ✅ |
| `/mix` skill (canonical production path) | ✅ (2026-05-19) |
| Hint gate in orchestrator (refuses run without complete hints) | ✅ (2026-05-19) |
| `desktop_analyzer.py` (drives MIK + RB UIs) | ✅ (2026-05-19) |
| `ABLETON_INTERACTION.md` portable reference doc | ✅ (2026-05-19) |
| Sapian (T5) bass placement | ⚠️ may need hint adjustment after listen |
| Off-by-one beat verification in Ableton | ⚠️ pending Sam's listen on V13 |
| `/section-detection` skill — algorithm + Claude corrections = finished sections .als | ✅ LOCKED IN (2026-05-20) |
| `apply_section_corrections.py` — patch chop boundaries directly via XML | ✅ (2026-05-20) |
| `arrange_sections.py` — reposition tracks via natural-fill alignment | ✅ (2026-05-20) |
| 8-quarter blind PNG renderer (`sections_blind_viz.py`) | ✅ (2026-05-20) |
| `/arrange-mix` skill + Mix Patterns Library | ⏳ planned next session (see `Documentation/TODO_ARRANGE_MIX.md`) |

**Latest outputs**: Sections V19 (section-detection LOCKED IN + arrangement); Sam-built V20 (basic mixes with loops, no automation) is the next-phase teaching example for /arrange-mix.
**Analysis model version**: `cue-candidates-v1`
**Production entry point**: `/mix <project-path>` — never `python -m automated_dj_mixes.orchestrator` for new mixes

## What NOT to rebuild
- **rekordbox_waveform.py PWV5 parser** — bit layout figured out (LSB-first: R bits0-2, G bits3-5, B bits6-8, height bits9-13). Don't re-derive.
- **features.py disk cache** — keyed on path/mtime/size/version. Don't bypass without good reason.
- **The Interval / CueCandidate / TransitionSpec split** — Codex review settled this: facts → interpretation → planner. Don't put cue flags back on Interval.
- **Manual PSSI binary parsing in rekordbox_reader.py** — pyrekordbox 0.4.x can't parse RB7 EXT files (construct.ConstError). Don't try `AnlzFile.parse_file()` again.
- **Volume + EQ bass swap as the transition technique** — Sam settled: filter sweeps cause conflicts. Don't reintroduce LP/HP automation.
- **PWV5 colour interpretation** — neutral colour/height fields; frequency-band correlation is the UI convention, not validated.
- **Mixed In Key GEOB tag format** — base64-encoded JSON; resilient tag-only fallback when DB read fails. Don't try mutagen text decode.
- **Visual hint precedence** — `_is_visual_hint` check in `first_credible` and `first_drop_candidate` ensures hints win over MIK/RB/amplitude. Don't add other ranking that could override.
- **PhraseGrid two-grid pattern (per-track)** — outgoing_grid snaps incoming_start; incoming_grid snaps bass_swap. The cascade preserves alignment across the whole mix. Don't snap to global beat 0 — Sam explicitly clarified per-track.
- **Visual review gate** — pipeline MUST print VISUAL REVIEW REQUIRED block + write REVIEW_VNN.md template at end. AI_CONTEXT.md REQUIRED section enforces this; don't remove without Sam's call.
- **`/mix` is the canonical production path** (2026-05-19) — never invoke `orchestrator.py` directly for new mixes. Use `/mix <project>` which forces the visual-pass-first workflow. Orchestrator now gates on a complete `Hints/track_hints.json` (every track needs `first_drop_sec`, `first_break_sec`, `outro_start_sec` with exact filename keys); only `--previews-only` (bypasses for preview generation) and `--no-hints-required` (debug only) skip the gate. Mirrors exist in Codex Brain / Antigravity Brain command folders.
- **`/section-detection` pipeline LOCKED IN — algorithm + Claude corrections = finished sections .als** (2026-05-20) — auto-fires when user mentions section detection, Sections V<N>, `phrase_viz.py`, `refine_segments`, paths under `Sections Review/`, etc. Canonical chopping pipeline validated on Black Book x Defected V2 (V13 → V19). Order: (1) `orchestrator.py --sections-layout` for algorithm pass, (2) `extract_sections_als.py` → JSON, (3) `sections_blind_viz.py` renders **8 quarter PNGs per track** (NOT 4 — 4 missed 1-2 bar fills), (4) Claude reads every PNG and fills `BLIND_VALIDATION_V<N>.md` table with PNG inspected / energy-step-seen-at-bar / match verdict / specific observation (HARD self-check: chop count must equal row count), (5) for `⚠ off N` errors, edit `apply_section_corrections.py` CORRECTIONS list and patch the .als directly. Algorithm tuning capped at ONE round per project; beyond that, manual correction only. `sections_compare_viz.py` FORBIDDEN (V7-diff trap). Stop condition: zero `⚠ off N` entries remain. Arrangement step (`arrange_sections.py`) is SEPARATE and runs AFTER chops are locked, using natural-fill alignment. Skill lives at `~/.claude/commands/section-detection.md` mirrored to Codex Brain / Antigravity Brain. Brain-level auto-fire instruction in each brain's CLAUDE.md / AGENTS.md / GEMINI.md.

## Next Priority
1. **`/arrange-mix` skill + Mix Patterns Library (PRIORITY, start next session)** — full plan in `Documentation/TODO_ARRANGE_MIX.md`. Library lives at `Documentation/Mix Patterns Library/` (in this repo, cross-project). Similarity matching by BPM + section structure shape. Learns from rejections. Auto-detects Sam edits. V20's 9 transitions are the initial training data. Don't touch the locked-in section-detection pipeline.
2. Sam to listen to **Mix V13** (Black Book x Defected V2) in Ableton — verify `last_bass_drop` anchoring gives clean musical swaps. T1 (Adam Ten → Chris Lake Savana) is the canary; Sam's manual mix at the natural fill was much cleaner than earlier algorithm picks.
2. Refine `last_bass_drop_sec` and `first_drop_sec` per track from listening — current values are coarse visual estimates from preview PNGs. Edit `Test Project/Black Book x Defected V2/Hints/track_hints.json` and re-run via `/mix`.
3. End-to-end test of `desktop_analyzer.py` on a fresh project with NO prior MIK/RB analysis (current V2 was already analyzed; full UI driving path hasn't been validated end-to-end).
4. Mirror `/mix` skill to Wren protocols (`~/.hermes/reference/ai_brain_protocols.md`) — skipped this session because the path doesn't exist on this machine.
5. Bargrooves T2 "edited incoming intro" technique (deferred): some tracks need their buildup compressed to fit shorter overlaps. Not implemented yet.
6. Expand template to 12+ tracks (still pending from 2026-05-17).

## Architecture
- Source code: `Source/automated_dj_mixes/` Python package (15 modules; see TOOLBOX.md)
- Pipeline: analysis → Rekordbox enrichment → sequencing → warping → per-beat features (cached) → factual intervals → ranked cue candidates → candidate-driven transition planning → ALS generation → objective validation + transition report
- ALS generation is template-based — never build XML from scratch (ElementTree breaks Ableton's parser)
- Reports: `Test Project/May 2026 Mix/Reports/` (CSV + Markdown + PWV5 PNGs)
- Cache: `Test Project/May 2026 Mix/Analysis Cache/` (versioned pickles)

## V1 Constraints
- Electronic/dance tracks only
- Constant BPM, 4/4 time
- Rekordbox-analyzed library (PSSI + PQTZ + PWV5 in .EXT file)
- Gain staging: always match to quietest track (never boost)

## Key Libraries
- `librosa` — per-beat RMS + bass-band RMS
- `numpy` — percentile stats + smoothing
- `pyloudnorm` — LUFS measurement
- `mutagen` — ID3/Vorbis tag reading
- `pyrekordbox` — Rekordbox6Database access (manual binary parse for RB7 tags)
- `matplotlib` — PWV5 validation renders

## Conventions
- Folder names: capitalised full words (Documentation/, Config/, Data/, Tests/)
- Exception: `Source/automated_dj_mixes/` — lowercase Python package
- Versioning: V1, V2, V3 for ALS outputs; `ANALYSIS_MODEL_VERSION` for analysis-side caches/reports
- Conventional commits: feat, fix, refactor, docs, chore, test

## Next Priority
1. Fix bass-swap grid alignment (validation FAIL on V16)
2. Sam reviews V16 + Phrase Viz V8, populates `Data/Ground Truth/Sam Cue Points.yaml`
3. Tune cue-detection thresholds against ground truth

## Session Protocol
- Read `Documentation/AI_CONTEXT.md` and `.github/ai-activity-log.md` at session start
- Append STARTED/DONE entries to the activity log
- Update `AI_CONTEXT.md` Current State and What's Next at session end
