# Toolbox — Automated DJ Mixes

Module reference for all pipeline components. Updated as modules are built.

## Modules

### `Source/automated_dj_mixes/orchestrator.py`
Main pipeline controller. Wires analysis → sequencing → warping → automation → ALS generation.
**Status:** Scaffold only

### `Source/automated_dj_mixes/analysis.py`
Reads key/BPM from file tags (mutagen). Transient/downbeat detection (Librosa). LUFS measurement (pyloudnorm). Falls back to Mixed In Key CSV if tags missing.
**Status:** Scaffold only

### `Source/automated_dj_mixes/sequencer.py`
Camelot wheel mapping (musical key → Camelot code). Builds optimal harmonic path through all tracks. Rules: +-1 = smooth, +-2 = power mix, A<->B = key change.
**Status:** Scaffold only

### `Source/automated_dj_mixes/warping.py`
Calculates warp markers from BPM + detected downbeat. Aligns first kick/transient to grid. Assumes constant BPM, 4/4 time (V1 constraint).
**Status:** Scaffold only

### `Source/automated_dj_mixes/automation.py`
Generates filter automation envelopes (bass cut on incoming until energy change, bass cut on outgoing). Crossfade curves (configurable 16 or 32 bars). Gain offsets — finds quietest track, brings all others down to match.
**Status:** Scaffold only

### `Source/automated_dj_mixes/als_generator.py`
Template-based ALS XML patching. Loads known-good Ableton Live 12 template, patches in tracks, clips, warp markers, automation lanes, gain offsets. gzip-compresses and writes .als file. Handles versioning (V1, V2, V3).
**Status:** Scaffold only

### `Source/automated_dj_mixes/config.py`
Loads settings from `Config/settings.json`. Provides defaults for crossfade bars, filter depth, gain strategy, Ableton version, project tempo, versioning prefix.
**Status:** Scaffold only

## Dependencies

| Package | Purpose |
|---------|---------|
| librosa | Transient/downbeat detection, energy analysis |
| pyloudnorm | LUFS measurement |
| mutagen | Reading ID3/Vorbis tags |
| ffmpeg-python | Audio format handling |
