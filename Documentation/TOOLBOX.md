# Toolbox — Automated DJ Mixes

Module reference for all pipeline components.

## Modules

### `Source/automated_dj_mixes/orchestrator.py`
Main pipeline controller. Wires analysis → sequencing → gain offsets → warping → arrangement positions → transition automation → ALS generation. CLI via `python -m automated_dj_mixes.orchestrator --input "Tracks/" --output "Output/"`.

Key functions: `run_pipeline()` (full pipeline), `_find_template()`, `_next_version()`, `main()` (CLI).

### `Source/automated_dj_mixes/analysis.py`
Reads key/BPM from file tags (mutagen ID3/Vorbis). Transient/downbeat detection (librosa). LUFS measurement (pyloudnorm). Falls back to librosa beat detection if BPM tag missing.

Key types: `TrackAnalysis` (dataclass with path, key, camelot, bpm, lufs, first_downbeat_sec, duration_sec, sample_rate, warnings).
Key functions: `analyse_track()`, `analyse_folder()`, `_read_tags()`, `_detect_downbeat()`, `_measure_lufs()`.

### `Source/automated_dj_mixes/sequencer.py`
Full Camelot wheel mapping (24 keys + common aliases like "Am", "Bbm", "F#"). Compatibility scoring: 4=identical, 3=smooth/relative, 2=power, 1=diagonal, 0=clash. Greedy nearest-neighbour harmonic path. **20 tests.**

Key functions: `key_to_camelot()`, `compatibility_score()`, `is_compatible()`, `build_harmonic_path()`.

### `Source/automated_dj_mixes/warping.py`
Two-marker warp calculation: first downbeat → beat 0, end of track → total beats. Ableton interpolates linearly. Works for constant-BPM tracks. **5 tests.**

Key types: `WarpMarker` (beat_time, sample_time).
Key function: `calculate_warp_markers()`.

### `Source/automated_dj_mixes/automation.py`
Transition generation: outgoing LP filter sweeps 20kHz→200Hz, incoming HP filter starts 500Hz then drops to 20Hz at midpoint, volume crossfade. Gain offsets: match to quietest (min LUFS), cap at max_reduction_db. **11 tests.**

Key types: `AutomationPoint`, `TransitionAutomation`.
Key functions: `generate_transition()`, `calculate_gain_offsets()`.

### `Source/automated_dj_mixes/als_generator.py`
Template-based ALS XML patching. Decompresses gzip, patches raw lines (not DOM — Ableton rejects reformatted XML), recompresses. Inserts: AudioClip XML (FileRef, WarpMarkers, Complex Pro mode), track names, utility gain, filter automation envelopes (discovers LP/HP target IDs by frequency value), project BPM. **14 tests.**

Key types: `TrackPatch` (analysis, track_index, warp_markers, gain_offset_db, arrangement_start_beats).
Key functions: `generate_session()`, `decompress_als()`, `compress_als()`, `_build_audio_clip_xml()`, `_find_filter_target_id()`, `_insert_audio_clip()`, `_insert_automation_envelopes()`.

### `Source/automated_dj_mixes/config.py`
Loads settings from `Config/settings.json` with sensible defaults (crossfade_bars=32, max_gain_reduction_db=12, default_project_tempo=128, versioning_prefix="V").

## Dependencies

| Package | Purpose |
|---------|---------|
| librosa | Transient/downbeat detection, energy analysis |
| pyloudnorm | LUFS measurement |
| mutagen | Reading ID3/Vorbis tags |
| ffmpeg-python | Audio format handling |
