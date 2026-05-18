# Automated DJ Mixes — Copilot Instructions

## Project Overview
Automated DJ mix pipeline for Wired Masters showreels. Takes pre-tagged dance tracks and produces a ready-to-review Ableton Live 12 session via template-based ALS XML patching. Multi-signal cue candidate detection feeds bass-to-bass transition planning.

## Current Project Status (2026-05-18)

| Component | Status |
|---|---|
| Camelot sequencing | ✅ |
| Per-beat warp markers from Rekordbox grid | ✅ |
| LUFS-based gain offsets | ✅ |
| Rekordbox PSSI phrase parsing (RB7 manual binary) | ✅ |
| Rekordbox PWV5 waveform parsing | ✅ |
| Mixed In Key 11 reader (GEOB + SQLite) | ✅ (2026-05-18) |
| Amplitude-envelope analysis (1s RMS) | ✅ (2026-05-18) |
| Visual hints workflow (`Hints/track_hints.json` → CueCandidate conf 0.95) | ✅ (2026-05-18) |
| Per-track PhraseGrid snap (16/8/4 tiered) | ✅ (2026-05-18) |
| Per-track + per-transition viz PNGs with tiered phrase grid | ✅ (2026-05-18) |
| Blank-canvas preview PNGs (for hint authoring) | ✅ (2026-05-18) |
| Visual review gate (VISUAL REVIEW REQUIRED + REVIEW_VNN.md) | ✅ (2026-05-18) |
| Per-track bar alignment HARD validation | ✅ (2026-05-18) — 11/11 in V46 |
| Clamp sync (chop_arrangement follows bass_swap) | ✅ (2026-05-18) |
| first_downbeat_offset positioning fix | ✅ (2026-05-18) |
| Dead-air loop refinement | ✅ (2026-05-18) |
| Chop-leave-outro-room (16-bar reserve) | ✅ (2026-05-18) |
| ALS generation (template-based, multi-clip) | ✅ |
| Sapian (T5) bass placement | ⚠️ may need hint adjustment |
| Off-by-one beat verification in Ableton | ⚠️ pending Sam's listen |

**Latest outputs**: Mix V46 (ALL PASS, awaiting Sam's Ableton listen)
**Analysis model version**: `cue-candidates-v1`

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

## Next Priority
1. Sam to listen to V46 in Ableton — verify off-by-one fix + Sapian alignment.
2. Refine hints from listening pass (edit `Test Project/.../Hints/track_hints.json`, re-run).
3. Expand template to 12+ tracks (still pending from 2026-05-17).

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
