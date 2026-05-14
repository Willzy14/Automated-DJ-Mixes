# Automated DJ Mixes — Copilot Instructions

## Project Overview
Automated DJ mix pipeline for Wired Masters showreels. Takes pre-tagged dance tracks and produces a ready-to-review Ableton Live 12 session (ALS file) using template-based XML generation.

## Architecture
- All source code lives in `Source/automated_dj_mixes/` as a Python package
- Pipeline: analysis → sequencing → warping → automation → ALS generation
- ALS generation is template-based — never build XML from scratch
- Config lives in `Config/settings.json`
- Tests in `Tests/`, fixtures in `Tests/Fixtures/`

## V1 Constraints
- Electronic/dance tracks only
- Constant BPM, 4/4 time
- First-kick/downbeat detection for grid alignment
- Gain staging: always match to quietest track (never boost)

## Key Libraries
- `librosa` — transient/downbeat detection
- `pyloudnorm` — LUFS measurement
- `mutagen` — ID3/Vorbis tag reading
- `ffmpeg-python` — audio format handling

## Conventions
- Folder names: capitalised full words (Documentation/, Config/, Tests/)
- Exception: `Source/automated_dj_mixes/` — lowercase Python package
- Versioning: V1, V2, V3 for ALS outputs
- Conventional commits: feat, fix, refactor, docs, chore, test

## Session Protocol
- Read `Documentation/AI_CONTEXT.md` and `.github/ai-activity-log.md` at session start
- Append STARTED/DONE entries to the activity log
- Update `AI_CONTEXT.md` Current State and What's Next at session end
