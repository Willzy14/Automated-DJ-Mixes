# Toolbox — Automated DJ Mixes

Module reference for all pipeline components.

## Modules

### `Source/automated_dj_mixes/orchestrator.py`
Main pipeline controller. Wires analysis → Rekordbox enrichment → sequencing → gain offsets → warping (RB beat grid or 2-marker fallback) → phrase-aware strategy selection → skill-based transition automation → automation clamping → ALS generation. CLI via `python -m automated_dj_mixes.orchestrator --input "Tracks/" --output "Output/"`.

Key functions: `run_pipeline()` (full pipeline), `_find_template()`, `_next_version()`, `_find_last_phrase()`, `main()` (CLI).
Strategy priority: breakdown_blend → outro_into_intro → bass_to_bass → tail_into_break → end_to_end.

### `Source/automated_dj_mixes/analysis.py`
Reads key/BPM from file tags (mutagen ID3/Vorbis). Transient/downbeat detection (librosa). LUFS measurement (pyloudnorm). Bass section detection (off-beat energy sampling). Phrase-aware break detection. Rekordbox enrichment maps RB phrases → pipeline fields (bass_start/end, break_start/end, intro_end, last_kick).

Key types: `TrackAnalysis` (dataclass with path, key, camelot, bpm, lufs, first_downbeat_sec, duration_sec, sample_rate, bass_start_sec, bass_end_sec, first_break_start_sec, first_break_end_sec, intro_end_sec, last_kick_sec, rekordbox_phrases, analysis_source, warnings).
Key functions: `analyse_track()`, `analyse_folder()`, `enrich_from_rekordbox()`, `_detect_downbeat()`, `_detect_bass_section()`, `_detect_first_break_phrase_aware()`.

### `Source/automated_dj_mixes/sequencer.py`
Full Camelot wheel mapping (24 keys + common aliases like "Am", "Bbm", "F#"). Compatibility scoring: 4=identical, 3=smooth/relative, 2=power, 1=diagonal, 0=clash. Greedy nearest-neighbour harmonic path. **20 tests.**

Key functions: `key_to_camelot()`, `compatibility_score()`, `is_compatible()`, `build_harmonic_path()`.

### `Source/automated_dj_mixes/rekordbox_reader.py`
Reads Rekordbox 7 ANLZ files (`.DAT`, `.EXT`) for beat grids, phrase analysis, and key data. Manual PSSI binary parser (pyrekordbox doesn't expose phrase data). Matches tracks by filename against Rekordbox library.

Key types: `PhraseSection` (start_beat, label, bars), `RekordboxAnalysis` (title, bpm, key, beat_times_ms, phrases, + helpers).
Key functions: `read_rekordbox_library()`, `enrich_from_rekordbox()` (in analysis.py), `beat_to_sec()`, `phrase_end_beat()`, `first_phrase_of()`.

### `Source/automated_dj_mixes/warping.py`
Warp marker calculation. Two modes: (1) 2-marker linear from BPM + downbeat (fallback), (2) per-beat grid from Rekordbox — one marker per downbeat using exact ms timestamps (165-252 markers per track, eliminates up to 13-beat drift). **5 tests.**

Key types: `WarpMarker` (beat_time, sample_time).
Key functions: `calculate_warp_markers()`, `calculate_warp_markers_from_beat_grid()`, `choose_warp_mode()`.

### `Source/automated_dj_mixes/automation.py`
Transition generation: outgoing LP filter sweeps 20kHz→200Hz, incoming HP filter starts 500Hz then drops to 20Hz at midpoint, volume crossfade. Gain offsets: match to quietest (min LUFS), cap at max_reduction_db. **11 tests.**

Key types: `AutomationPoint`, `TransitionAutomation`.
Key functions: `generate_transition()`, `calculate_gain_offsets()`.

### `Source/automated_dj_mixes/als_generator.py`
Template-based ALS XML patching. Decompresses gzip, patches raw lines (not DOM — Ableton rejects reformatted XML), recompresses. Inserts: AudioClip XML (FileRef, WarpMarkers, Complex Pro mode), track names, utility gain, filter automation envelopes (discovers LP/HP target IDs by frequency value), project BPM. **14 tests.**

Key types: `TrackPatch` (analysis, track_index, warp_markers, gain_offset_db, arrangement_start_beats).
Key functions: `generate_session()`, `decompress_als()`, `compress_als()`, `_build_audio_clip_xml()`, `_find_filter_target_id()`, `_insert_audio_clip()`, `_insert_automation_envelopes()`.

### `Source/automated_dj_mixes/config.py`
Loads settings from `Config/settings.json` with sensible defaults (crossfade_bars=48, max_gain_reduction_db=12, default_project_tempo=128, versioning_prefix="V").

### `Source/automated_dj_mixes/skills/`
Modular transition skill system. `SkillsEngine` in `__init__.py` scores all skills against a `TransitionContext` and picks the highest-scoring one. Each skill generates automation points for its transition style.

| Skill | Bars | Style |
|-------|------|-------|
| `LongFilterBlend` | 16-48 | LP sweep + gradual volume + bass swap at midpoint |
| `QuickEqSwap` | 4-16 | Hard EQ bass kill swap, no filter sweeps |
| `EnergeticPunchSwap` | 12-24 | Hard EQ swap at midpoint, BPM diff >=3 |
| `GentleBlend` | 24-64 | Smooth no-EQ blend, BPM diff <=2 |
| `BreakdownBlend` | 48-96 | Long overlap, breakdown at 1/3 |

### Diagnostic Scripts

- `Source/diagnose_rekordbox.py` — Rekordbox phrase map vs pipeline fields side-by-side
- `Source/analyze_phrase_patterns.py` — Structural patterns across all RB-analyzed tracks (intro/outro zones, drop sequences, archetypes)
- `Source/diagnose_sections.py` — Section markers from librosa across a folder
- `Source/analyze_teaching.py` — Sam's teaching mix clip/transition analysis

## Dependencies

| Package | Purpose |
|---------|---------|
| librosa | Transient/downbeat detection, energy analysis (fallback) |
| pyloudnorm | LUFS measurement |
| mutagen | Reading ID3/Vorbis tags |
| pyrekordbox | Reading Rekordbox ANLZ files (beat grids, phrase data) |
| ffmpeg-python | Audio format handling |
