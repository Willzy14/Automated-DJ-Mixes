# Automated DJ Mixes

## What This Is

Automated DJ mix pipeline for Wired Masters showreels. Takes a folder of pre-tagged dance tracks and produces a sequenced, warped, gain-leveled Ableton Live 12 session (ALS file) from a known-good template. Sam drops tracks in, says "mix these," and gets a ready-to-review session back — harmonic sequencing, beat-matched, transitions automated, levels balanced. He loads it in Ableton, listens through, tweaks, and it's done.

Born from the realisation that the grunt work of DJ mixing (key analysis, Camelot sequencing, warping, gain leveling, crossfades, filter automation) is entirely rule-based and takes hours that could be automated.

## Tech Stack

- **Python 3.x** — main orchestrator and all pipeline modules
- **Librosa** — transient/downbeat detection, energy analysis
- **pyloudnorm** — LUFS measurement for gain staging
- **mutagen** — reading key/BPM from ID3/Vorbis tags (written by Mixed In Key)
- **Ableton Live 12** — target DAW, ALS file format (gzip-compressed XML)
- **Mixed In Key** — key + BPM analysis (run separately, writes to file tags)

Not in V1: Max for Live (future enhancement for real-time automation), pyproject.toml packaging.

## Architecture

```
Source/automated_dj_mixes/
├── __init__.py
├── orchestrator.py      — Main pipeline controller
├── analysis.py          — Tag reading, transient detection, LUFS
├── sequencer.py         — Camelot wheel logic, harmonic path
├── warping.py           — Warp marker calculation
├── automation.py        — Filter curves, crossfades, gain offsets
├── als_generator.py     — Template-based ALS XML patching
└── config.py            — Settings loader
```

Pipeline: analysis → sequencing → warping → automation → ALS generation.

ALS generation is **template-based** — a real Ableton Live 12 session is decompressed, studied, and used as the base. The script patches in tracks, clips, warp markers, automation lanes, and gain offsets. Never builds XML from scratch.

### Template: `DJ Mix Template 2026.als`

- **Track 1**: "Session Time" — HOFA Project Time only, no audio. Always skip.
- **Tracks 2-12**: "2-Audio" through "12-Audio" — each has identical effects chain:
  - `StereoGain` (Utility) — stereo/mono, width, gain, balance, bass mono @ 120Hz
  - `ChannelEq` — 3-band EQ (Low/Mid/High) + output gain
  - `AutoFilter2` — SVF low-pass, 24dB slope, frequency at 20kHz (fully open)
- **Device hierarchy**: `AudioTrack > DeviceChain > Devices > AudioEffectGroupDevice > Branches > AudioEffectBranch > DeviceChain > AudioToAudioDeviceChain > Devices`
- Each parameter has a unique `AutomationTarget Id` — must be found dynamically per track

### Key automation targets (per track)

| Parameter | XML Element | Purpose |
|-----------|------------|---------|
| Mixer Volume | `Mixer > Volume > AutomationTarget` | Crossfade curves |
| Utility Gain | `StereoGain > Gain > AutomationTarget` | LUFS-based gain offsets |
| Auto Filter Freq | `AutoFilter2 > Filter_Frequency > AutomationTarget` | Filter sweeps during transitions |
| Channel EQ Low | `ChannelEq > Low > Gain > AutomationTarget` | Bass kill during transitions |

## How to Run

```powershell
pip install -r requirements.txt
$env:PYTHONPATH="Source"
python -m automated_dj_mixes.orchestrator --input "Tracks/" --output "Output/"
```

Later: `pyproject.toml` + editable install (`pip install -e .`).

## Current State

Scaffolding complete. Template analysed. Awaiting first module implementation.
- Template `DJ Mix Template 2026.als` committed — 12 audio tracks, effects chain studied, XML structure mapped
- Decompressed XML at `Templates/DJ Mix Template 2026.xml` (gitignored, regeneratable)
- All 7 source modules scaffolded with dataclasses, signatures, and `NotImplementedError`
- 4 passing tests for Camelot wheel mapping

## What's Next

1. **Analysis module** — read key/BPM from tags (mutagen), transient detection (Librosa), LUFS measurement (pyloudnorm)
2. **Sequencer module** — Camelot wheel mapping + optimal harmonic path algorithm
3. **Sequencer tests** — unit tests for Camelot logic
4. **Ableton template** — Sam creates minimal Live 12 session, commit + decompress to study XML schema
5. **ALS generator** — template-based XML patching
6. **Warping module** — warp marker calculation from BPM + detected downbeat
7. **Automation module** — filter envelopes, crossfade curves, gain offsets (match to quietest track)
8. **Orchestrator** — wire everything together
9. **CLI** — argparse entry point

## Key Decisions

- **Template-based ALS, not from-scratch XML** — ALS schema is undocumented and fragile. Decompress a real template, learn the structure from fixtures, patch from known-good. (Codex review, 2026-05-14)
- **Mixed In Key tags first, UI automation last** — V1 reads existing tags via mutagen. CSV/export as fallback. Claude UI automation is a last resort, not a core dependency. (Codex review, 2026-05-14)
- **V1 constrained to dance music** — Electronic/dance tracks, constant BPM, 4/4 time, first-kick/downbeat detection. Variable-tempo and non-4/4 are out of scope. (Codex review, 2026-05-14)
- **Gain staging: match to quietest track** — Never boost. Find the quietest track's LUFS, bring all others down to match. Preserves headroom and avoids clipping. (Sam's preference, 2026-05-14)
- **Max for Live deferred** — Filter/crossfade automation lives in ALS automation envelopes. Max for Live is a future enhancement, not V1. (Codex review, 2026-05-14)
- **Manual trigger, not folder monitoring** — Sam drops tracks in a folder, opens Claude Code, says "mix these." No background watcher needed. (Car conversation, 2026-05-14)
- **Versioning: V1, V2, V3** — Every ALS output is versioned. Reordering tracks generates a new version, never overwrites. (Car conversation, 2026-05-14)
- **ALS direct generation, not Max for Live bridge** — Generate the file before opening Ableton, not manipulate clips during a session. Simpler, fewer moving parts. (Car conversation, 2026-05-14)
- **Camelot rules for harmonic sequencing** — +-1 number = smooth transition, +-2 = power mix, A<->B = key change. Script builds optimal path, Sam adjusts by ear after loading. (Car conversation, 2026-05-14)

## Connections

- **Social Media Content Engine** — completed mixes become showreel content for social media
- **samwillsmixing.com** — mixes serve as portfolio demos / musical showreels
- **Wired Masters** — showcases tracks the studio has put out
