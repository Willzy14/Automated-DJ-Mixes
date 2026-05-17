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
├── orchestrator.py        — Main pipeline controller, --visualize mode
├── analysis.py            — Tag reading, transient detection, LUFS
├── sequencer.py           — Camelot wheel logic, harmonic path
├── warping.py             — Per-beat warp markers from Rekordbox grid
├── automation.py          — AutomationPoint + gain offset calc
├── als_generator.py       — Template-based ALS XML patching (multi-clip per track)
├── rekordbox_reader.py    — Rekordbox ANLZ parser (PSSI phrases, PQTZ beat grid)
├── rekordbox_waveform.py  — PWV5/PWV4 colour waveform parser (4th analysis signal)
├── features.py            — Per-beat features (RMS + bass + PWV5) with disk cache
├── phrase_viz.py          — Factual Interval records + viz colour collapse
├── cue_candidates.py      — Ranked CueCandidate API (5 cue types + confidence)
├── transition.py          — Bass-to-bass transition planning, candidate-driven
├── report.py              — Per-track CSV + per-mix Markdown reports
├── validation.py          — Objective pass/fail checks on planned mix
└── config.py              — Settings loader
```

Pipeline: analysis → Rekordbox enrichment → sequencing → warping → per-beat features (cached) → factual intervals → ranked cue candidates → candidate-driven transition planning → ALS generation → objective validation.

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

**Multi-signal cue candidate architecture landed (V8 viz / V16 mix). Pipeline now emits ranked CueCandidate objects with confidence + sources + reasons, consumed by transition planning. Per-track CSVs and per-mix Markdown reports auto-generated. Disk cache for per-beat features. PWV5 parsing added as 4th analysis signal.** Sam reviewing V16 + Viz V8.

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

### 2026-05-17 (Latest Session)
**Focus**: Multi-signal cue candidate architecture (Codex-reviewed plan, executed end-to-end)

**Completed**:
- Preserved V7 work as `analysis-v7-preserve` branch on origin (safety net)
- Merged V7 worktree → main (commit `efadeb0`), pushed
- `rekordbox_waveform.py`: PWV5/PWV4 parser — 3-bit RGB + 5-bit height packed in 16-bit LSB-first words. Generates neutral colour/height per pixel; bit-layout confirmed by inspecting real .EXT data on Coast 2 Coast / VLAD / Ease My Mind. 3 PNG validation renders in `Test Project/May 2026 Mix/Reports/`
- `features.py`: per-beat librosa RMS + bass-band RMS + PWV5 height/RGB, with disk cache keyed on path/mtime/size/analysis_version. Track-local p30/p50/p70 percentiles for relative banding
- `phrase_viz.py` REFACTORED: `Interval` is now factual-only (no cue flags, no labels). `segments_from_intervals()` is the viz-only colour collapse.
- `cue_candidates.py`: ranked `CueCandidate` API. 5 cue types (bass_entry, break_start, break_end, chop_point, outro_start) with confidence (multi-signal agreement) + sources list + human-readable reasons. Pre-chorus candidates penalized 15% but never hidden (Harry Romero fix)
- `report.py`: per-track CSV (`Analysis - {track}.csv`) + per-mix Markdown (`Transition - Mix V{N}.md`)
- `validation.py`: 5 objective pass/fail checks on the planned mix (NOT by reparsing ALS — uses internal MixPlan state)
- `transition.py`: now accepts ranked `CueCandidate` lists, prefers them over RB-phrase fallbacks. Sources logged in decision_log
- `orchestrator.py`: full wire-up — features extracted before transition planning, candidates threaded into `plan_transition()`, validation + transition report at the end
- `Data/Ground Truth/Sam Cue Points.yaml`: stub for 5 problem tracks × 4 cues (Sam to populate)
- Generated **Phrase Viz V8** (using new candidate detection in viz mode) and **Mix V16** (candidate-driven transitions). 4/5 validation checks pass; bass-swap grid alignment fails on some transitions (off-bar)
- `ANALYSIS_MODEL_VERSION = "cue-candidates-v1"` propagated through cache keys + all reports
- Merged `analysis-v8-build` → main, pushed to origin

**Key Learnings**:
- PWV5 entry layout is 16-bit big-endian word, LSB-first packing: R=bits0-2, G=bits3-5, B=bits6-8, height=bits9-13, padding=bits14-15. Confirmed by decoding real data and seeing musically-sensible patterns (low-energy intros, peaks at drops)
- pyrekordbox can't parse Rekordbox 7 .EXT files (construct.ConstError), but the PWV5/PSSI tag structures are simple enough to scan/parse manually using the same binary-scan pattern as PSSI
- Multi-signal agreement = confidence: Coast 2 Coast + Sapian both hit 0.85 on bass_entry when RB chorus phrase + librosa bass rise + PWV5 height rise all align in one 8-bar window
- Codex's "interpretation lives separately from observations" pattern made the codebase dramatically more honest — Interval stores facts, CueCandidate stores interpretations, transition planner consumes ranked candidates
- Disk cache for features.py is essential — without it every iteration was waiting on librosa
- The 48-bar max-overlap clamp can push bass_swap off-grid in arrangement; validation flagged this, needs `_snap_to_phrase()` on the final swap position in transition.py

### 2026-05-16 (Previous Session)
**Focus**: Rekordbox integration — phrase analysis + beat grids replace librosa section detection

**Completed**: PSSI binary parser, per-beat warp markers, phrase-aware strategy selector, automation clamping with unity anchors, max overlap to 48 bars, Mix V7-V10

**Key Learnings**:
- Rekordbox phrase analysis far more reliable than librosa for structural detection
- Ableton extends first/last automation breakpoint values across entire timeline — must clamp with unity anchors
- Coast to Coast tail naturally looped — Sam loves this, wants intentional loop extension

### 2026-05-15 (Previous Session)
**Focus**: Base-to-base mixing — phrase-grid alignment, smarter strategies, real-time Sam review

**Completed**: Bass detection, phrase-grid snap, strategy selector (bass_to_bass / tail_into_break / end_to_end), multi-envelope merge, master at -6dB, volume on Utility Gain, 12 mix versions (V1-V12)

### 2026-05-14 (First Session)
**Focus**: Bootstrap → end-to-end pipeline → skills system → tempo automation

**Completed**: Full pipeline implementation, drop-confirmation kick detection, 5-skill engine, tempo automation, base-to-base alignment first attempt (V1-V8)

## What's Next

1. **Sam reviews V16 + Phrase Viz V8** — listen, mark hits/misses, populate `Data/Ground Truth/Sam Cue Points.yaml`
2. **Fix bass-swap grid alignment** — validation FAILed on this. Snap `bass_swap` to nearest 8-bar boundary in `transition.py` while preserving chop/incoming alignment
3. **PWV5 visual confirmation** — Sam compares the 3 PNGs in `Reports/` to Rekordbox UI to confirm colour→frequency mapping before relying on it for chop detection
4. **Tune candidate thresholds** with the ground-truth file — if our top bass_entry candidate matches Sam's marked beat for 5/5 tracks, ship; if not, adjust BASS_DELTA_RISE / BASS_DELTA_DROP
5. **Expand template** — current template fits 11 tracks, need 12+ support
6. **Long intros**: Capriati 40 bars, Fanciulli 40 bars — internal structure (build/teaser-drop) currently hidden inside the "intro" region; might need sub-classification

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
- **Multi-signal cue candidates, not single labels** — Each 8-bar interval stores facts (RB phrase + RMS + bass + PWV5 height). Interpretation is a separate ranked `CueCandidate` layer with confidence + sources + reasons. Transition planning consumes ranked candidates with RB-phrase fallback. (Codex review + Sam, 2026-05-17)
- **PWV5 = Rekordbox's visual waveform bytes, not AI vision** — Pioneer's purpose-built waveform display data is already in the .EXT file as colour+height per pixel. Better first signal than rendering+OCR. Channel mapping (R=highs/G=mids/B=lows) is the Rekordbox UI convention but treated as visual data first, frequency-correlated second. (Sam + Codex, 2026-05-17)
- **Interval is facts only; CueCandidate is interpretation** — Removing cue flags from Interval and putting interpretation in `cue_candidates.py` made the codebase honest. (Codex, 2026-05-17)
- **Pre-chorus candidates penalized but never hidden** — Bass entries inside RB's "intro" region get a 15% confidence penalty + `region` tag, but stay visible. (Harry Romero fix; Codex, 2026-05-17)
- **Percentile-based thresholds, not absolute** — p30/p70 per track for low/high banding; mastered tracks vary wildly so absolutes fail. (Codex, 2026-05-17)
- **Disk cache for per-beat features** — Path/mtime/size + analysis_version cache key. Without it every viz iteration was librosa-bound. (2026-05-17)
- **Validate from the MixPlan, not from the generated ALS** — Reparsing the ALS XML is fragile and unnecessary; the internal plan state is the source of truth for what we INTENDED. (Codex, 2026-05-17)
- **ANALYSIS_MODEL_VERSION constant** — Propagated through cache keys, CSVs, MD reports, and YAML headers. Bumping invalidates old caches and lets old reports stay identifiable when thresholds change. (Codex, 2026-05-17)

## Connections

- **Social Media Content Engine** — completed mixes become showreel content for social media
- **samwillsmixing.com** — mixes serve as portfolio demos / musical showreels
- **Wired Masters** — showcases tracks the studio has put out
