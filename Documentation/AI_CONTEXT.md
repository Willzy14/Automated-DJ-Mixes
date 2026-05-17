# Automated DJ Mixes

## What This Is

Automated DJ mix pipeline for Wired Masters showreels. Takes a folder of pre-tagged dance tracks and produces a sequenced, warped, gain-leveled Ableton Live 12 session (ALS file) from a known-good template. Sam drops tracks in, says "mix these," and gets a ready-to-review session back — harmonic sequencing, beat-matched, transitions automated, levels balanced. He loads it in Ableton, listens through, tweaks, and it's done.

Born from the realisation that the grunt work of DJ mixing (key analysis, Camelot sequencing, warping, gain leveling, crossfades, filter automation) is entirely rule-based and takes hours that could be automated.

## Tech Stack

- **Python 3.x** — main orchestrator and all pipeline modules
- **Librosa** — transient/downbeat detection, energy analysis (fallback when Rekordbox unavailable)
- **pyloudnorm** — LUFS measurement for gain staging
- **mutagen** — reading key/BPM from ID3/Vorbis tags (written by Mixed In Key)
- **pyrekordbox** — reading Rekordbox ANLZ files (beat grids, phrase analysis)
- **Ableton Live 12** — target DAW, ALS file format (gzip-compressed XML)
- **Mixed In Key** — key + BPM analysis (run separately, writes to file tags)
- **Rekordbox 7** — phrase analysis (Intro/Up/Down/Chorus/Outro), beat grids, key data

Not in V1: Max for Live (future enhancement for real-time automation), pyproject.toml packaging.

## Architecture

```
Source/automated_dj_mixes/
├── __init__.py
├── orchestrator.py      — Main pipeline controller + strategy selector
├── analysis.py          — Tag reading, transient detection, LUFS, Rekordbox enrichment
├── sequencer.py         — Camelot wheel logic, harmonic path
├── warping.py           — Warp marker calculation (2-marker + Rekordbox beat grid)
├── automation.py        — Filter curves, crossfades, gain offsets
├── als_generator.py     — Template-based ALS XML patching
├── rekordbox_reader.py  — Rekordbox ANLZ parser (phrases, beat grid, key)
├── config.py            — Settings loader
└── skills/
    ├── __init__.py      — SkillsEngine decision layer
    ├── base.py          — TransitionContext, TransitionPlan, TransitionSkill
    ├── long_filter_blend.py
    ├── quick_eq_swap.py
    ├── energetic_punch_swap.py
    ├── gentle_blend.py
    └── breakdown_blend.py
```

Pipeline: analysis → Rekordbox enrichment → sequencing → warping → strategy selection → skill-based automation → ALS generation.

ALS generation is **template-based** — a real Ableton Live 12 session is decompressed, studied, and used as the base. The script patches in tracks, clips, warp markers, automation lanes, and gain offsets. Never builds XML from scratch.

### Template: `DJ Mix Template 2026.als`

- **Track 1**: "Session Time" — HOFA Project Time only, no audio. Always skip.
- **Tracks 2-12**: "2-Audio" through "12-Audio" — each has identical effects chain:
  - `StereoGain` (Utility) — stereo/mono, width, gain, balance, bass mono @ 120Hz
  - `ChannelEq` — 3-band EQ (Low/Mid/High) + output gain. **Use Low band for bass kills** (cleaner than filter)
  - `AutoFilter2` Id="1" — **Low-pass**, SVF, 24dB slope, freq at 20kHz (fully open). Sweep DOWN to cut highs.
  - `AutoFilter2` Id="3" — **High-pass**, SVF, 24dB slope, freq at 20Hz (fully open). Sweep UP to cut lows.
- **Device hierarchy**: `AudioTrack > DeviceChain > Devices > AudioEffectGroupDevice > Branches > AudioEffectBranch > DeviceChain > AudioToAudioDeviceChain > Devices`
- Each parameter has a unique `AutomationTarget Id` — must be found dynamically per track

### Key automation targets (per track)

| Parameter | XML Element | Purpose |
|-----------|------------|---------|
| Mixer Volume | `Mixer > Volume > AutomationTarget` | Crossfade curves |
| Utility Gain | `StereoGain > Gain > AutomationTarget` | LUFS-based gain offsets |
| LP Filter Freq | `AutoFilter2[0] > Filter_Frequency` (Type=0) | Low-pass sweep — cut highs |
| HP Filter Freq | `AutoFilter2[1] > Filter_Frequency` (Type=1) | High-pass sweep — cut lows |
| Channel EQ Low | `ChannelEq > Low > Gain > AutomationTarget` | Bass kill (cleaner than filter) |

## How to Run

```powershell
pip install -r requirements.txt
$env:PYTHONPATH="Source"
python -m automated_dj_mixes.orchestrator --input "Tracks/" --output "Output/"
```

Later: `pyproject.toml` + editable install (`pip install -e .`).

## Current State

**Pipeline fully working end-to-end with Rekordbox integration, iterating on transition quality with Sam.** Has generated 20+ mixes in real Ableton sessions (V1-V12 with librosa, V7-V10 with Rekordbox), Sam reviewing track-by-track.

**Implemented (Phase 1-8):**
- All 7 core modules + rekordbox_reader + skills system functional
- **Rekordbox phrase analysis** (rekordbox_reader.py) — manual PSSI binary parser reads Intro/Up/Down/Chorus/Outro with beat-accurate boundaries from Rekordbox 7 ANLZ files
- **Rekordbox beat grid warp markers** (warping.py) — one marker per downbeat from Rekordbox's per-beat grid (165-252 markers per track vs old 2-marker linear interpolation). Eliminates up to 13-beat drift on tracks with micro-tempo variation
- **Phrase-aware strategy selector** (orchestrator.py) — uses full Rekordbox phrase map: `breakdown_blend` (outgoing's breakdown overlaps incoming's build), `outro_into_intro` (natural zone blend), `bass_to_bass`, `tail_into_break`, `end_to_end` fallback
- **Phrase boundary snap** — swap points snap to actual Rekordbox phrase starts instead of arbitrary 32-bar grid
- **Automation clamping** — unity (1.0) anchor points at clip boundaries ensure NO automation outside overlap zones. Ableton shows 1.0 everywhere except actual transitions
- **Drop-confirmation kick detection** (analysis.py) — finds first kick via rhythmic confirmation + bass power in next 8 beats
- **Bass section detection** (analysis.py) — off-beat energy sampling distinguishes sustained bass synth from kicks-only intros
- **Modular skills system** (`skills/`) — `LongFilterBlend`, `QuickEqSwap`, `EnergeticPunchSwap`, `GentleBlend`, `BreakdownBlend`, base classes + `SkillsEngine` decision layer
- **Master at -6dB** — prevents clipping when summing mastered tracks
- **Tempo automation** across the mix (each track plays near its native BPM with smooth ramps)
- **Multi-envelope merge** — middle tracks correctly merge incoming + outgoing automation onto single envelopes per parameter
- **Max overlap capped at 48 bars** (was 96) — matches Sam's mixing style (teaching mix median: 25 bars)
- **XML escaping** for `&` and unicode characters in track names
- **35-track template** in use (`Templates/DJ Mix Template 2026-1 Project/`) — fits 12+ track mixes

**Key technique discoveries:**
- Volume + bass cut is the dominant transition technique (filter sweeps cause conflicts with bass kills)
- Volume on Utility plugin Gain (not Mixer fader — keeps fader free for manual tweaking)
- Mixer fader gets static LUFS-correction value at load (not automated)
- EQ bass kill uses Ableton's ChannelEQ LowShelfGain (range 0.18 = -15dB to 1.0 = unity)
- Rekordbox phrase start_beat values are 1-based; must subtract 1 for 0-based warp marker coordinates
- Ableton extends first/last automation breakpoint values to entire timeline — must clamp with unity anchors

## Recent Session History

### 2026-05-16 (Latest Session)
**Focus**: Rekordbox integration — phrase analysis + beat grids replace librosa section detection

**Completed**:
- Rekordbox reader (rekordbox_reader.py) — PSSI binary parser, beat grid, phrase mapping. All 17 tracks matched
- Per-beat warp markers via `calculate_warp_markers_from_beat_grid()` — 165-252 markers per track vs old 2-marker
- Phrase-aware strategy selector using Rekordbox phrase map (breakdown_blend → outro_into_intro → bass_to_bass → tail_into_break → end_to_end)
- Automation clamping with unity anchors at clip boundaries — no automation outside overlap zones
- Max overlap reduced to 48 bars (from 96), breakdown_blend guarded to >50% track position
- Phrase boundary snap to actual Rekordbox phrase starts
- Diagnostic scripts: diagnose_rekordbox.py, analyze_phrase_patterns.py
- Mix V7-V10 generated with iterative Sam feedback on transition quality

**Key Learnings**:
- Rekordbox phrase analysis far more reliable than librosa for structural detection
- Ableton extends first/last automation breakpoint values across entire timeline — must clamp with unity anchors
- When outro_into_intro was prioritized first, ALL transitions used it (every track has both). Must guard generic strategies
- Coast to Coast tail naturally looped — Sam loves this, wants intentional loop extension as a feature

### 2026-05-15 (Previous Session)
**Focus**: Base-to-base mixing — phrase-grid alignment, smarter strategies, real-time Sam review

**Completed**: Bass detection, phrase-grid snap, strategy selector (bass_to_bass / tail_into_break / end_to_end), multi-envelope merge, master at -6dB, volume on Utility Gain, 12 mix versions (V1-V12)

### 2026-05-14 (First Session)
**Focus**: Bootstrap → end-to-end pipeline → skills system → tempo automation

**Completed**: Full pipeline implementation, drop-confirmation kick detection, 5-skill engine, tempo automation, base-to-base alignment first attempt (V1-V8)

## What's Next

1. **Sam testing V10** — review all 11 transitions in Ableton, feedback on transition quality with Rekordbox data
2. **Intentional loop extension** — Sam loved Coast to Coast tail looping. Build a feature to intentionally enable LoopOn when alignment math leaves a gap (Sam's manual technique)
3. **Clip fragmentation** (V2 signature, deferred) — chop outgoing's last drum section into 2-4 beat fragments for percussion-loop outros (76% of clips in Sam's teaching mixes are <16 beats)
4. **Smoother tempo automation** — "1 BPM rise over 2 tracks" instead of jumping at every transition
5. **Expand template** — current template fits 11 tracks, need 12+ support

## Key Decisions

- **Template-based ALS, not from-scratch XML** — ALS schema is undocumented and fragile. Decompress a real template, learn the structure from fixtures, patch from known-good. (Codex review, 2026-05-14)
- **Mixed In Key tags first, UI automation last** — V1 reads existing tags via mutagen. CSV/export as fallback. Claude UI automation is a last resort, not a core dependency. (Codex review, 2026-05-14)
- **V1 constrained to dance music** — Electronic/dance tracks, constant BPM, 4/4 time, first-kick/downbeat detection. Variable-tempo and non-4/4 are out of scope. (Codex review, 2026-05-14)
- **Gain staging: match to quietest track** — Never boost. Find the quietest track's LUFS, bring all others down to match. Preserves headroom and avoids clipping. (Sam's preference, 2026-05-14)
- **Max for Live deferred** — Filter/crossfade automation lives in ALS automation envelopes. Max for Live is a future enhancement, not V1. (Codex review, 2026-05-14)
- **Manual trigger, not folder monitoring** — Sam drops tracks in a folder, opens Claude Code, says "mix these." No background watcher needed. (Car conversation, 2026-05-14)
- **Versioning: V1, V2, V3** — Every ALS output is versioned. Reordering tracks generates a new version, never overwrites. (Car conversation, 2026-05-14)
- **ALS direct generation, not Max for Live bridge** — Generate the file before opening Ableton, not manipulate clips during a session. Simpler, fewer moving parts. (Car conversation, 2026-05-14)
- **ALS XML patching proven** — Decompress gzip, modify XML values (line-level text replacement, not XML rewriter), recompress. Ableton loads it clean. XmlWriter reformats the document and corrupts it — must use raw text ops. (Validated 2026-05-14)
- **Camelot rules for harmonic sequencing** — +-1 number = smooth transition, +-2 = power mix, A<->B = key change. Script builds optimal path, Sam adjusts by ear after loading. (Car conversation, 2026-05-14)
- **Phrase grid (16/32 bars) is the master timing rule** — Every major change (bass swap, volume fade endpoint, transition boundary) MUST land on a 16 or 32 bar phrase boundary. Music is built on phrases; off-grid transitions sound wrong regardless of beat counts. Snap to nearest 32-bar mark, clamp to within outgoing's clip. (Sam, 2026-05-15)
- **Two valid transition types** — (1) bass-to-bass: outgoing's bass_end aligns with incoming's bass_start, kicks overlap, EQ swap manages lows. (2) tail-into-break: outgoing's outro plays into incoming's break, then incoming's bass drops in at break_end. Pure end-to-end (beats-into-beats) is BORING and only a last resort. (Sam, 2026-05-15)
- **Volume + bass cut > filter sweeps** — Filter sweeps (HP on incoming, LP on outgoing) conflict with EQ bass kill on the same low frequencies. Default to volume + bass cut only; filter sweeps stay as opt-in skill. (Sam, 2026-05-15)
- **Volume on Utility plugin, not mixer fader** — Volume automation lives on the Utility Gain parameter at the top of each track's device chain. The mixer fader on the right gets the static LUFS gain offset, not automation — keeps it free for manual tweaking during playback. (Sam, 2026-05-15)
- **Bass swap = single hard step at one beat; volume = smooth curve over full window** — They're independent automation layers in the same transition. Bass cuts surgically; volume blends gradually. (Sam, 2026-05-15)
- **Mode-based project tempo, not average** — Project BPM = most common rounded BPM across tracks (if 8 tracks at 130 and 4 at 124, use 130). Tempo automation across the mix makes each track play at its native BPM via gradual ramps. (Sam, 2026-05-15)
- **Master at -6dB by default** — All tracks are mastered, so summing risks clipping. Pre-attenuate master by 6dB. Mastering integration is a future enhancement. (Sam, 2026-05-15)
- **Rekordbox as primary structural data source** — Rekordbox's phrase analysis (Intro/Up/Down/Chorus/Outro) and beat grids replace fragile librosa-based section detection. Librosa kept as fallback. Rekordbox PSSI start_beat values are 1-based. (2026-05-16)
- **Automation ONLY in overlap zones** — No automation curves where tracks aren't overlapping. Unity (1.0) anchor points at clip boundaries ensure Ableton shows no processing outside transitions. Root cause of stray points: Ableton extends first/last breakpoint to entire timeline. (Sam, 2026-05-16)
- **Max overlap 48 bars, not 96** — Sam's teaching mixes median at 25 bars. 96 was too long and created unwieldy transitions like Sentin remix at 93 bars. 48 is the upper bound. (Sam, 2026-05-16)
- **Per-beat warp markers from Rekordbox grid** — One marker per downbeat (every 4th beat) from Rekordbox's exact ms timestamps. Eliminates up to 13-beat drift vs 2-marker linear interpolation. (2026-05-16)

## Connections

- **Social Media Content Engine** — completed mixes become showreel content for social media
- **samwillsmixing.com** — mixes serve as portfolio demos / musical showreels
- **Wired Masters** — showcases tracks the studio has put out
