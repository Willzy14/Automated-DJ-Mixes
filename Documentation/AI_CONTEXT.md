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

**Pipeline fully working end-to-end, iterating on transition quality with Sam.** Has generated 12+ mixes in real Ableton sessions, Sam has reviewed and given track-by-track feedback.

**Implemented (Phase 1-6):**
- All 7 core modules functional
- **Drop-confirmation kick detection** (analysis.py) — finds first kick via rhythmic confirmation + bass power in next 8 beats
- **Bass section detection** (analysis.py) — off-beat energy sampling distinguishes sustained bass synth from kicks-only intros
- **Phrase-aware break detection** (analysis.py) — scans at 16-bar grid from bass_start, finds first energy drop (break_start) and recovery (break_end)
- **Modular skills system** (`skills/`) — `LongFilterBlend`, `QuickEqSwap`, `EnergeticPunchSwap`, `GentleBlend`, `BreakdownBlend`, base classes + `SkillsEngine` decision layer
- **Three alignment strategies** (orchestrator) — `bass_to_bass`, `tail_into_break`, `end_to_end` fallback
- **Phrase-grid snap** — all swap points land on 32-bar boundaries (Sam's master rule: music = 16/32 bar phrases)
- **Master at -6dB** — prevents clipping when summing mastered tracks
- **Tempo automation** across the mix (each track plays near its native BPM with smooth ramps)
- **Multi-envelope merge** — middle tracks correctly merge incoming + outgoing automation onto single envelopes per parameter
- **XML escaping** for `&` and unicode characters in track names
- **35-track template** in use (`Templates/DJ Mix Template 2026-1 Project/`) — fits 12+ track mixes

**Key technique discoveries:**
- Volume + bass cut is the dominant transition technique (filter sweeps cause conflicts with bass kills)
- Volume on Utility plugin Gain (not Mixer fader — keeps fader free for manual tweaking)
- Mixer fader gets static LUFS-correction value at load (not automated)
- EQ bass kill uses Ableton's ChannelEQ LowShelfGain (range 0.18 = -15dB to 1.0 = unity)

## Recent Session History

### 2026-05-15 (Latest Session)
**Focus**: Base-to-base mixing — phrase-grid alignment, smarter strategies, real-time Sam review

**Completed**:
- Bass detection via off-beat energy sampling (`_detect_bass_section` in analysis.py)
- Phrase-grid snap (32-bar boundaries) for all swap points (orchestrator)
- Strategy selector: `bass_to_bass` / `tail_into_break` / `end_to_end` fallback
- Phrase-aware break detection (`_detect_first_break_phrase_aware`) returning break_start AND break_end
- Multi-envelope merge per (track, param) — fixed Ableton ignoring 2nd envelope on same target
- Master volume at -6dB (`_set_master_volume_level` in als_generator.py)
- Dropped LP/HP filter sweeps from default automation (they conflict with bass cut on lows)
- Volume automation moved from Mixer Volume → Utility plugin Gain
- Mixer fader carries static LUFS gain offset (not automated, free to manually adjust)
- Snap clamp: phrase snap never rounds past outgoing's clip end
- 35-track template now used automatically (most-recently-modified .als in Templates/)
- 12 mix versions generated (V1-V12), Sam reviewing transition-by-transition

**Key Learnings**:
- **Music = 16/32 bar phrases** — every swap MUST land on a phrase boundary or it sounds off-grid
- **Beat-to-beat alignment is BORING** — listener loses interest after 32+ bars of just beats. Either bass-to-bass swap OR tail-into-break (outro plays into incoming's break, then incoming's bass drops back in)
- **Bass cut + volume fade are independent** — bass swap is often a hard step at one beat; volume is a smooth curve over the full transition. Both run together
- **The mix point IS the bass swap** — outgoing's bass cuts when incoming's bass enters (earlier of the two, not when outgoing's bass naturally ends)
- **Filter sweeps fight bass cuts** — both try to manage lows. Drop filters from default. Filter blend stays as opt-in skill
- **Multi-envelope per target = broken automation** — Ableton uses first envelope, ignores others. Must merge into single envelope per (track, param)
- **Auto-filter HP on incoming was redundant** with EQ bass kill — same job, conflict

**Known Issues (entering next session)**:
- Sapian transition in V12 is "fucked up" (Sam's words) — Sapian has no bass detection, the tail_into_break strategy picked up but result is wrong
- 0.5-1 beat drift when incoming's bass_start is at non-bar-aligned beat (e.g. 64.48 beats); needs warp anchor snap
- Bass detection threshold needs tuning — fails on tracks with very even bass energy (Sapian, Detlef, Harry Romero)
- Phrase-aware break detection is new in V12 — not yet validated against ground truth

### 2026-05-14 (Previous Session)
**Focus**: Bootstrap → end-to-end pipeline → skills system → tempo automation

**Completed**: Full pipeline implementation, drop-confirmation kick detection, 5-skill engine, tempo automation, base-to-base alignment first attempt (V1-V8)

## What's Next (tomorrow)

1. **Fix Sapian-like cases** — when bass detection fails AND incoming has no clear break, find a better strategy than naive end_to_end. Maybe detect outgoing's natural outro point and align there
2. **Warp anchor snap** — round bass_start_sec / first_break_start_sec to nearest 4-beat multiple in clip time via warp markers; eliminates 0.5-beat drift
3. **Validate break detection on real tracks** — run the phrase-aware break detector against Sam's actual mix points from teaching mixes
4. **Loop intro/outro** when alignment math leaves a gap (Sam's manual technique — extend either side to hit a phrase boundary)
5. **Clip fragmentation** (V2 signature, deferred) — chop outgoing's last drum section into 2-4 beat fragments for percussion-loop outros (76% of clips in Sam's teaching mixes are <16 beats)
6. **Smoother tempo automation** — Sam wants "1 BPM rise over 2 tracks" instead of jumping at every transition

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

## Connections

- **Social Media Content Engine** — completed mixes become showreel content for social media
- **samwillsmixing.com** — mixes serve as portfolio demos / musical showreels
- **Wired Masters** — showcases tracks the studio has put out
