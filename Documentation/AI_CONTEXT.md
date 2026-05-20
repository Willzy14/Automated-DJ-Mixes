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
├── cue_candidates.py      — Ranked CueCandidate API (5 cue types + confidence + visual_hint + amplitude + MIK paths)
├── mik_reader.py          — Mixed In Key 11 GEOB tag + SQLite reader (cues, beat grid, energy)
├── amplitude_analysis.py  — 1s RMS envelope analysis (first_drop, first_break, outro_start, clean-loop-window detector)
├── transition.py          — Transition planning with PhraseGrid (per-track 16/8/4 snap)
├── report.py              — Per-track CSV + per-mix Markdown reports
├── validation.py          — Objective checks: overlap, per-track bar/phrase alignment, fade endpoints
├── transition_viz.py      — Per-transition PNG (both tracks aligned, automation overlay, tiered phrase grid)
├── track_viz.py           — Per-track PNG (full timeline, candidates, loop region, tiered phrase grid)
├── waveform_preview.py    — Blank-canvas preview PNG (for writing visual hints before pipeline run)
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

**Sections V19 — chopping pipeline LOCKED IN.** End-to-end validated on Black Book x Defected V2: algorithm pass (V13) → Fix G iteration (V16) → 3 manual chop corrections via `apply_section_corrections.py` (V17) → Sam's manual edits (V18) → arrangement repositioning via `arrange_sections.py` (V19). The `/section-detection` skill encodes the workflow with un-skippable blind validation (8 PNGs per track, per-chop verdict table, hard chop-count = row-count check). Arrangement uses natural-fill alignment (incoming.drop_1 aligned to outgoing's last fill/break before outro). **Next phase: arrangement refinement** — Sam to evaluate V19 overlap lengths (longest 100 + 104 bars on Savana→Capriati and Route 94→EMM) and propose per-pair alignment rules.

**Mix V46 (previous milestone) — per-track phrase-grid alignment enforced. 100% bar alignment, ~85% 4-bar phrase alignment per-track.** Pipeline has a full visual-hint workflow: each track gets a blank-canvas preview PNG; Sam (or Claude) reads the picture, writes timestamps to `Test Project/.../Hints/track_hints.json`; hints emit highest-confidence CueCandidates (0.95) that win over algorithmic picks. Visual review gate at end of every pipeline run prints `VISUAL REVIEW REQUIRED` block + auto-generates `REVIEW_VNN.md` template that must be filled before the mix is "complete."

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

### 2026-05-20 (Latest Session) — Section-detection pipeline LOCKED IN + arrangement principle learned (V13→V20)

**Focus**: Lock in the section-detection pipeline. Then learn arrangement principles from Sam's V20 example. Plan `/arrange-mix` skill + Mix Patterns Library for tomorrow.

**Completed: `/section-detection` skill + corrective workflow.**
- `~/.claude/commands/section-detection.md` (+ Codex Brain / Antigravity Brain mirrors) — full workflow with un-skippable blind validation. Auto-fires on triggers (section detection, Sections V<N>, phrase_viz.py, etc.). Brain-level auto-fire instructions added to CLAUDE.md / AGENTS.md / GEMINI.md.
- Workflow: orchestrator `--sections-layout` → `extract_sections_als.py` → `sections_blind_viz.py` (**8 quarter PNGs** per track, not 4 — 4 missed 1-2 bar fills) → Claude reads every PNG and fills `BLIND_VALIDATION_V<N>.md` table (HARD self-check: chop count must equal row count) → for `⚠ off N` errors, edit `apply_section_corrections.py` CORRECTIONS list and patch .als directly.
- Anti-patterns documented and rejected: "X/Y near perfect" without evidence, "matches V7 within N bars" (V7 is not truth, waveform is), reading some PNGs and extrapolating, running `sections_compare_viz.py` (V7-diff trap — FORBIDDEN by skill).

**Completed: corrective workflow proven end-to-end on Black Book x Defected V2 (V13 → V19).**
- V13: algorithm pass, BLIND_VALIDATION found 4 real `⚠ off N` errors.
- V14: tried tuning `OUTRO_REFINE_BASS_RATIO` 0.7→0.85 → no change (Fix C aborted on Marco's 1-bar drop_4; threshold not enough for EMM).
- V15: tried `mean()` instead of `all()` + walk-back logic → REGRESSION (pulled back Savana + Sapian which were correct). Reverted.
- V16: added **Fix G — `_absorb_short_segments_before_outro`** (catches Marco's spurious fill+1-bar-drop+outro pattern, consolidates into outro starting at the amplitude collapse). Marco outro fixed (112 → 107). No regressions.
- V17: applied 3 manual `apply_section_corrections.py` patches — Adam Ten bar 72 → 74 (drop_3/break_1), Adam Ten bar 112 → 108 (break_1/drop_4), EMM bar 240 → 236 (drop_4/outro). All 24 attribute changes (8 per correction × 3) successful.
- V18: Sam-edited truth file (Sam added intro→Break/Build splits on 4 tracks, moved Marco drop_1 from 40 → 36, kept Savana / Renegades / Sapian identical to V17).
- V19: arrangement via new `arrange_sections.py` — recomputed natural-fill positions using V18 chops, shifted Marco/Crusy/Sapian +16 beats to track Marco's drop_1 move. Tracks 1-7 unchanged.

**Completed: pipeline LOCK-IN across 5 surfaces.**
1. `~/.claude/commands/section-detection.md` — added "Status — LOCKED IN (2026-05-20)" header, 8-PNG default explicit, `arrange_sections.py` added to tools table.
2. Codex Brain mirror.
3. Antigravity Brain mirror.
4. `Documentation/AI_CONTEXT.md` — Current State leads with "Sections V19 — chopping pipeline LOCKED IN", new Key Decision documenting 5 canonical script steps.
5. `.github/copilot-instructions.md` — replaced V13-era blurb with full LOCKED IN workflow.

**Completed: Sam's V20 reveals arrangement principle.**
V20 (Sam-built) introduces basic mixes with loops but no automation. Reduced overlaps from 44-104 bars (V19) to 15-47 bars. Added looping clips (Adam Ten 16→29 clips, Capriati 12→13, Renegades 11→13, Route 94 6→10, EMM 10→13). Sam's correction of my framing: "the chops are the lineup points." Each transition has 2-3 alignment moments: **entry** (incoming intro START at outgoing chop), **bass swap** (chop coincidence on both tracks — natural swap without automation), **exit** (outgoing end at incoming chop). **Loops are mechanical glue** to fill gaps when a section's native length is shorter than the moment-to-moment span.

V20 transitions analysed: Adam Ten → Savana (2-chop, looped Adam Ten kick stinger), Crusy → Sapian (3-chop including natural bass swap, no loops), Capriati intro restarted to extend 24→36 bars, Renegades intro looped 4-bar × 3, Route 94 skips source bar 0 starts at bar 4 then loops 4-bar × 4, EMM heavy multi-loop 16→40 bars, Sapian dropped outro.

**Planned: `/arrange-mix` skill + Mix Patterns Library — full plan in `Documentation/TODO_ARRANGE_MIX.md`.**
Cross-project learning library at `Documentation/Mix Patterns Library/` (in this repo). Similarity matching by BPM + section structure shape. Learns from rejections (records both Claude's pick AND Sam's correction). Auto-detects Sam edits on every invocation. V20's 9 transitions to be extracted as initial training data tomorrow.

**Key Learnings**:
- The algorithm has a ceiling. Visual validation by Claude IS the deliverable, not algorithm refinement. After 4 iterations (V13→V16) only 1 of 4 errors was fixed by algorithm tuning. The other 3 fixed by direct `apply_section_corrections.py` patching in seconds.
- V14/V15 failures proved that "raise the threshold" approach is non-convex — fixing one track breaks another. Targeted new fixes (Fix G) beat generic threshold tuning.
- **The chops are the lineup points** — Sam's framing. Bars/beats are the wrong unit; chop-to-chop alignment is the right unit. Loops aren't a creative choice, they're consequences of which chops you pick to align.
- The 8-PNG zoom (vs 4-PNG default) reliably catches 1-2 bar fills the 4-PNG zoom missed. Don't reduce zoom back to 4 without revalidating.
- "Matches V7" is V7-diffing dressed as validation — `sections_compare_viz.py` is now explicitly forbidden by the skill.

**Files changed**:
- Source/ (new): `apply_section_corrections.py`, `arrange_sections.py`, `extract_sections_als.py`, `diff_sections.py`, `sections_blind_viz.py`, `sections_compare_viz.py`
- Source/automated_dj_mixes/ (modified): `orchestrator.py` (version counter fix, --sections-layout already existed), `phrase_viz.py` (added Fix G `_absorb_short_segments_before_outro`)
- Documentation/ (modified): `AI_CONTEXT.md` (locked-in note, current state, what's next), (new): `TODO_ARRANGE_MIX.md` (tomorrow's plan)
- ~/.claude/commands/ (new): `section-detection.md`. (Modified): `mix.md` (un-skippable validation note added earlier in session)
- Codex Brain / Antigravity Brain: `commands/section-detection.md` (new mirrors), `AGENTS.md` / `GEMINI.md` (auto-fire trigger sections + Available Skills row added)
- Claude Code Brain `CLAUDE.md` (auto-fire section added)
- `.github/copilot-instructions.md` (locked-in `/section-detection` + skill trigger)

### 2026-05-19 — `/mix` skill + `last_bass_drop` + desktop automation

**Focus**: Three major architectural changes, plus an attempted refactor that was reverted.

**Attempted then reverted: programmatic auto-analysis refactor.**
Built `auto_analyze.py` (Krumhansl-Kessler key detection + constant-tempo beat grid + phrase labeling) to replace MIK and Rekordbox desktop apps. Generated Mix V1 — warping was unlistenable because constant-tempo grid can't match per-beat reality without precise BPM. Sam's catch: "you take control of the PC for Blender — why not for MIK and Rekordbox?" — desktop automation gives back the per-beat RB grid without losing the zero-touch goal. Whole refactor reverted via `git checkout HEAD` (no commits had been made). Memory saved: `feedback_consider_desktop_automation_first.md`.

**Completed: desktop automation for MIK + Rekordbox.**
- `Source/automated_dj_mixes/desktop_analyzer.py` (~440 lines) — drives both apps via `pywinauto` + `pyautogui` with cursor save/restore so Sam can keep working in Ableton alongside.
- MIK driver: launches MIK, dismisses startup dialogs, clicks "My Collection" tab via UIA invoke, clicks "Add tracks" sidebar button via PNG template match (`templates/mik_add_tracks_button.png` — the button is a WPF custom control that UIA doesn't expose), clicks "Add folder" in modal, drives the "Browse For Folder" #32770 dialog via SendMessage. Polls `MIKStore.db` `Song` table for `IsAnalyzed=1` to detect completion.
- RB driver: brings rekordbox to foreground via `AttachThreadInput` (not the Alt-key trick which opens menu mode), clicks File → Import → Import Folder with cursor restored after each click. Polls via `pyrekordbox`.
- Wired into orchestrator: runs before `analyse_folder` so tracks are MIK+RB analyzed before the rest of the pipeline. Requires Library Protection OFF in Rekordbox.
- All 10 V2 project tracks now analyzed via the driver. Memory saved: `feedback_scope_ui_searches_to_target_window.md` (don't search globally — Ableton has a "File" menu too).
- pywinauto + pyautogui + pyperclip added to `requirements.txt`.

**Completed: `Documentation/ABLETON_INTERACTION.md` reference doc.**
17-section portable reference for any agent that needs to read/write `.als` files. Covers gzip format, the cardinal rule (line-level text patching, never `ElementTree`), AudioClip structure, warp markers, automation envelopes with the `Time="-63072000"` and unity-anchor gotchas, dB↔linear conversion, version notes. Written so it's NOT coupled to DJ-mix logic — Sam's planning to use it for a new Ableton-based project. Lives in this repo for now.

**Completed: `/mix` skill — canonical production path with hint enforcement.**
- `~/.claude/commands/mix.md` (symlinked to `Claude Code Brain/commands/mix.md`), mirrored to `Codex Brain/commands/mix.md` and `Antigravity Brain/commands/mix.md`.
- 7-step workflow: validate inputs → desktop analysis → previews-only render → **visual pass (read every PNG, identify 4 hint fields)** → write `Hints/track_hints.json` → full pipeline → visual review.
- `orchestrator.py`: added `--previews-only` flag (renders previews and exits before transition planning; bypasses hint gate so previews remain authorable), `--no-hints-required` (debug-only override), `_validate_hints()` helper, `_render_previews()` extracted to run early.
- **Production gate**: orchestrator refuses to plan transitions if any track is missing a complete hint. Exact filename keys including extension. All required fields must be present and positive numeric. Clear error message lists each missing field per track.

**Completed: `last_bass_drop_sec` — Sam's natural-fill alignment principle.**
- Added 4th required hint field to `HINT_REQUIRED_FIELDS` (`orchestrator.py`) and `HINT_TO_CUE_TYPE` (`cue_candidates.py`).
- New transition strategy in `plan_transition()` (`transition.py`): when outgoing has a `last_bass_drop` candidate, that's the bass_swap anchor — the natural fill near the end where bass drops out before final kicks return. Incoming positions so its `first_drop_sec` lands on the same arrangement beat. The EQ bass-cut still fires at that beat (hard step, two-phase volume envelope unchanged) — it reinforces what the music is already doing.
- Outgoing plays through to natural end (no early chop). Loop region only extends what's needed past natural end.
- Clamp skipped when `last_bass_drop` is the anchor — the music's natural overlap wins over the 48-bar cap.
- Validator overlap range bumped 16-48 → 16-80 bars (Sam's real Bargrooves mixes are 28-56 bars).

**Completed: 16-beat HARD phrase snap.**
- `PhraseGrid.snap()` (`transition.py`) replaced the tiered 16→8→4 fallback with HARD 16-beat-only snapping. Every transition breakpoint MUST land on a multiple of 16 beats from per-track origin.
- Validator: phrase-boundary check is now HARD (was WARN). Fails the mix if any breakpoint is off-phrase.

**Completed: Bargrooves Summer 2015 Mix 1 analysis** (`Source/analyze_real_mix.py`, `inspect_transition.py`).
Opened Sam's real DJ mix from `G:/Mix CD' Projects/2015 -/`, extracted clip positions per track. Found 4 distinct transition styles in 4 consecutive transitions: T1 = 1-bar Amen-style hammer (40 reps) + simplicity-bridge 16-bar chop, T2 = 1-bar hammer + edited incoming intro (skips 30+ source bars), T3 = outgoing surgery (3 chops with source-skips, no hammer), T4 = both natural (simple long crossfade). Sam's clarification: the core principle is "lock outgoing's last_bass_drop to incoming's first_drop" — the four styles are emergent from how that constraint resolves given track structures. Hence `last_bass_drop_sec` as the new central hint.

**Completed: V2 project test mix end-to-end via `/mix` workflow.**
Wrote `Test Project/Black Book x Defected V2/Hints/track_hints.json` with all 4 fields for all 10 tracks. Generated Mix V8 (first `/mix`-driven mix). Iterated to Mix V13 after `last_bass_drop` anchoring rule. Sam reviewed T1 in Ableton, identified that algorithmic chop was wrong, manual mix using natural fill alignment was much cleaner — confirmed the design direction.

**Key Learnings**:
- **Desktop automation > programmatic reimplementation when the desktop apps work well.** Sam's Blender remark cracked open the right pattern: don't reimplement MIK's auto-cue model (10+ years of refinement) when you can drive it with 200 lines of Python. Same for Rekordbox per-beat grids.
- **Mouse-stealing is real.** First desktop automation pass used `pyautogui.click` everywhere — kept hijacking Sam's cursor while he was working in Ableton. Refactored to use `pywinauto.click()` (BM_CLICK messages) and `set_edit_text()` (WM_SETTEXT) wherever possible; only the MIK Add tracks WPF button needs the actual cursor.
- **JUCE apps require AttachThreadInput for focus.** `SetForegroundWindow` is blocked by Windows focus-stealing prevention; Alt-key trick triggers menu activation as a side effect. AttachThreadInput is the clean answer.
- **Library Protection in Rekordbox silently no-ops the Import menu.** Spent 30 minutes debugging "Import Folder did nothing" before Sam toggled the padlock off. Document this in the `/mix` skill.
- **MIK 11 writes analysis to MIKStore.db SQLite (Song table) for WAV files — not to ID3 GEOB tags.** Old `mik_reader.py` only checked GEOB. Updated `is_mik_analyzed()` to check the DB first, fall back to GEOB for MP3s.
- **Aggregate stats hide DJ technique.** Earlier `MIXING_PATTERNS.md` extracted "median transition is 25 bars" from 184 transitions — useless. Looking at 4 transitions BY EYE revealed 4 distinct techniques. Visual analysis of real mixes is the right onboarding pattern.
- **`/mix` skill as forcing function works.** Before this session Claude kept "forgetting" the visual-pass-first rule even though it was documented. Codifying it as a skill + an orchestrator gate that physically refuses to run without complete hints makes the rule structural, not memory-dependent.
- **Constant-tempo grid drift > BPM-detection error.** With librosa's BPM detection (often off by 0.1-0.5 BPM), a constant-tempo grid drifts ~1 second per minute of audio — by the end of a 5-minute track, beat markers are 5+ seconds off the actual kicks. Per-beat detected timestamps (what Rekordbox produces) eliminate this. Reason to keep MIK+RB in the loop rather than reimplementing.

### 2026-05-18 (Previous Session)
**Focus**: Long iteration session — V17→V46 — wiring MIK, building visual-hint workflow, phrase-grid enforcement (per-track), and forcing Claude to actually use the visual review

**Completed (new modules)**:
- `mik_reader.py` — Mixed In Key 11 GEOB ID3 tag reader + SQLite (MIKStore.db) reader for cues, beat grid, energy segments, key. Resilient to DB failures (tags-only fallback).
- `amplitude_analysis.py` — librosa 1-second RMS envelope. `find_first_drop` (largest rise in 8-90s), `find_first_break` (first drop after first_drop), `find_outro_start` (first drop in last 90s, excluding final 20s fadeout), `find_clean_loop_window` (dead-air-free 8-bar window). `snap_to_mik_or_beat` helper.
- `transition_viz.py` — per-transition PNG (last 32 bars of outgoing + first 32 bars of incoming, aligned; volume + EQ overlays; bass_swap dashed line; loop region hatched; tiered phrase grid with bar labels).
- `track_viz.py` — per-track PNG (full timeline + MIK cues + RB phrases + energy strip + picked candidates + automation lanes + tiered phrase grid).
- `waveform_preview.py` — blank-canvas PNG (waveform + MIK cues + energy strip + RB phrases ONLY — no picks). For visual-hint authoring before pipeline runs.

**Completed (cue_candidates.py additions)**:
- `mik_to_candidates` — synthesises bass_entry + outro_start + chop_point from MIK cues when Rekordbox phrase data absent (10/12 tracks in test mix). chop_point = end of last MIK energy segment ≥ 4, or outro_start + 16 bars.
- `amplitude_to_candidates` — emits cues from amplitude envelope (used when MIK is sparse).
- `hint_to_candidates` + `load_hints_file` — reads `Hints/track_hints.json`, emits bass_entry/break_start/outro_start at confidence 0.95.
- `_is_visual_hint` + hint precedence in `first_credible` and `first_drop_candidate` — visual hints override algorithmic picks.
- `first_drop_candidate` — picks EARLIEST credible bass_entry (dance-music structural prior: first drop = the one DJs care about).

**Completed (transition.py refactors)**:
- `PhraseGrid` dataclass with tiered snap (16/8/4 beat fallback per Sam's chosen tolerance).
- **Per-track phrase grids**: each transition uses `outgoing_grid` (origin=outgoing_arrangement_start) to snap incoming start, then `incoming_grid` (origin=incoming_arrangement_start) to snap bass_swap. Cascade preserves alignment across the whole mix.
- Clamp branches also use per-track grid snap (V42 bug: clamps were re-snapping with plain `snap()` and undoing phrase alignment).
- `first_downbeat_offset` correction in incoming_arrangement_start — fixes off-by-one beat caused by clip-start vs first-downbeat misalignment.
- Loop dead-air refinement (`refine_for_clean_audio` calls `find_clean_loop_window`).
- Chop-leave-outro-room: chop pulled back if natural chop would leave < 24 beats of outro audio for the loop.
- Clamp sync: when overlap clamps shift incoming_start, chop_arrangement follows bass_swap (V42 had 24-beat gap between loop start and bass switch).

**Completed (visual review enforcement — the meta-fix)**:
- `Documentation/AI_CONTEXT.md` REQUIRED section at the top: visual review must be done after every pipeline run.
- Orchestrator prints `VISUAL REVIEW REQUIRED` block + auto-generates `Output/Visualisations/REVIEW_VNN.md` template with per-image checkboxes.
- Tiered phrase grid lines in all viz: bar (4-beat) faint → 2-bar (8-beat) medium → 4-bar phrase (16-beat) dark+labelled → 16-bar section (64-beat) bold+labelled. Makes off-phrase automation visible at a glance.

**Completed (validation.py)**:
- Per-track alignment check: `(bass_swap - incoming_arrangement_start) mod 4` (HARD), `mod 16` (warn). Same for transition_start (vs outgoing) and transition_end (vs incoming).
- Overlap tolerance widened to 1.5 bars to absorb phrase-snap drift.

**Completed (orchestrator wiring)**:
- MIK enrichment for all tracks (12/12 in test mix have auto-cues).
- pyrekordbox + sqlcipher3-wheels installed (Rekordbox 7 master.db decryptable; only 2/12 tracks matched in test mix — RB filename matcher is fuzzy).
- Hints loaded from `Test Project/.../Hints/track_hints.json` (currently 12 tracks hinted, all with first_drop/break/outro).

**Completed (Codex review doc)**:
- `Documentation/CODEX_REVIEW.md` — comprehensive architecture + rules-matrix + open questions + visualisation strategy. Sent to Codex; their P1/P2/P3 findings implemented.

**Key Learnings**:
- **Visual-pass-first beats numerical guess**: Sam's "look at the picture first, then dial in with data" framing fundamentally changed how the pipeline works. Hints from a human eye on the rendered waveform produce dramatically better picks than any algorithmic combination.
- **Numerical validation is not enough**: V42 passed all `validate_mix` checks but 0/11 bass swaps were on phrase boundaries — proves "ALL PASS" is necessary but not sufficient. Visual review gate now blocks declaring a mix complete.
- **Claude's visual capability needs to be FORCED into the workflow**: I built the per-track PNGs early but didn't open them until Sam pointed out I was bypassing my own tool. The `VISUAL REVIEW REQUIRED` block + `REVIEW_VNN.md` template + AI_CONTEXT.md rule makes it structural, not optional.
- **Per-track phrase grid ≠ global phrase grid**: snapping to multiples of 16 from arrangement beat 0 doesn't equal snapping to multiples of 16 from each track's beat 1. When tier-fallback kicks in for incoming_start, the two interpretations diverge. Per-track is the right semantic (matches what the listener perceives).
- **Dance music structural priors save the pipeline**: "first drop is at ~60s", "outro begins ~60s before track end", "MIK doesn't always cue the drop" — these are domain truths the algorithm should bake in, not discover.
- **Hints win, always**: even when MIK + amplitude + librosa all agree on beat 35, if the visual hint says beat 60, beat 60 wins. Human eye on the rendered waveform > algorithm.
- **Loop content should source from AFTER the chop**: my "outro_start = post_break_body" was wrong terminology. The real outro (Sam's term) is at chop_point onwards. Loops should come from past the chop, not before it.

### 2026-05-17 (Previous Session)
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

### 2026-05-15
**Focus**: Base-to-base mixing — phrase-grid alignment, smarter strategies, real-time Sam review. V1-V12.

### 2026-05-14
**Focus**: Bootstrap → end-to-end pipeline → skills system → tempo automation. V1-V8.

## What's Next

1. **`/arrange-mix` skill + Mix Patterns Library (PRIORITY, start tomorrow 2026-05-21)** — full plan in `Documentation/TODO_ARRANGE_MIX.md`. Sam taught the principle via V20: chops are lineup points; each transition has 2-3 alignment moments (entry, optional bass-swap, exit); loops fill gaps. Library lives at `Documentation/Mix Patterns Library/` (in this repo, cross-project). Similarity matching by BPM + section structure shape. Learns from rejections (record Claude's pick AND Sam's correction). Auto-detects edits on every invocation. Initial training data: V20's 9 transitions extracted as `source: sam_v20_initial` baseline.
2. **Sam to listen to V46 in Ableton** — verify (a) off-by-one beat issue resolved by `first_downbeat_offset` fix, (b) Sapian (T5) bass placement at 45s OK or needs hint adjustment, (c) per-track phrase alignment feels right musically.
3. **Refine hints from listening pass** — any track where Sam disagrees with the picked bass_entry/break/outro, edit `Test Project/.../Hints/track_hints.json` and re-run. Hints persist across mixes.
3. **Expand template** — current template fits 11 tracks, need 12+ support (still pending from 2026-05-17).
4. **Consider per-genre `prefer_grid`** in `PhraseGrid` — house/techno @ 16-beat preferred (current default works), DnB @ 8, trance @ 32. Could derive from BPM heuristic or RB metadata.
5. **Hint cache by audio hash** — once a track is hinted, the hint should persist across mixes regardless of project. Currently keyed by filename in track_hints.json — works but fragile if filenames change.
6. **Codex `CODEX_REVIEW.md` follow-up** — Codex's response landed; most P1/P2/P3 items implemented this session. Remaining: tempo ramp ending location (Sam said skip), per-genre phrase length parameterisation.
7. **Long intros**: Capriati 40 bars, Fanciulli 40 bars — internal structure (build/teaser-drop) currently hidden inside the "intro" region; might need sub-classification (still pending).

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
- **Mixed In Key auto-cues are the most trusted ALGORITHMIC signal** — MIK has refined its auto-cue model for years on dance music. MIK cue alignment within an interval adds +0.25 confidence (largest single boost). (Sam, 2026-05-18)
- **Visual hints override everything** — When Sam (or Claude) writes a `Hints/track_hints.json` entry for a track, that beat wins over MIK, Rekordbox, librosa, amplitude — regardless of position. Human eye on the rendered waveform > algorithm. Confidence 0.95. (Sam, 2026-05-18)
- **Visual pass before pipeline + visual review after** — Pre-pipeline: render blank-canvas preview, eyeball broad strokes, write hints. Post-pipeline: render per-track + per-transition viz with picks overlaid, verify alignment matches hints. `VISUAL REVIEW REQUIRED` block + `REVIEW_VNN.md` template enforce this. (Sam, 2026-05-18)
- **Phrase grid is PER TRACK, not global** — Each track has its own phrase grid starting at THAT track's beat 1, not at arrangement beat 0. bass_swap snaps to incoming's grid; chop_arrangement (= bass_swap) lands on outgoing's grid because incoming_arrangement_start was snapped to outgoing's grid in the first step. Cascade preserves alignment. (Sam, 2026-05-18)
- **Tiered snap fallback: 16 → 8 → 4** — Try 4-bar phrase first; fall back to 2-bar if natural drift > 4 beats; fall back to 1-bar only if drift > 8 beats. Hard floor: bar boundary (validator hard-fails off-bar). (Sam choice via AskUserQuestion, 2026-05-18)
- **First drop = earliest credible bass_entry, not highest confidence** — Dance music structural prior: the FIRST drop is what DJs care about for the bass swap. A later cue with bigger energy rise is usually a second drop after a break. `first_drop_candidate` returns the earliest credible, not the highest confidence. (Sam, 2026-05-18)
- **Outro = at/past the chop, not before it** — Sam's terminology: "outro" is the stripped percussion region. The earlier `outro_start` was actually the post-break body. The real outro starts at `chop_point` and continues. Loops source from AT chop (first 8 beats of real outro), not from before chop. (Sam, 2026-05-18)
- **Chop must leave outro room** — If natural chop is within 24 beats of track end, the outro loop has nowhere to live and falls back to intro. Solution: pull chop back to leave 16-bar reserve. (Sam, 2026-05-18)
- **Looping rule: outgoing → outro, incoming → intro** — Where possible, loop the OUTGOING's outro and the INCOMING's intro. Use whichever has cleaner content if only one end is stripped. `find_loop_region` has a `role` parameter for this. (Sam, 2026-05-18)
- **Tiered phrase grid in viz with bar labels** — Bar lines weighted by phrase importance: bar (4-beat) faint, 2-bar medium, 4-bar phrase dark+labelled, 16-bar section bold+labelled. Off-phrase automation should be visually obvious. (Sam-prompted, 2026-05-18: "how did you not spot these in the visual?")
- **Numerical validation is necessary but NOT sufficient** — `validate_mix` ALL-PASS doesn't mean the mix is right. The visual review gate is the only thing that verifies picks land on the right musical moments. AI_CONTEXT.md REQUIRED section + orchestrator's `VISUAL REVIEW REQUIRED` block + per-mix `REVIEW_VNN.md` template enforce this. (Sam, 2026-05-18)
- **`/mix` skill is the canonical production path** — never invoke the orchestrator directly for new mixes. The skill (in `~/.claude/commands/mix.md` and the Codex/Antigravity Brain mirrors) walks Claude through validate → desktop analysis → previews-only → **visual pass + write hints** → full pipeline → visual review. The orchestrator enforces a hint gate: it refuses to plan transitions if any track is missing a complete entry in `Hints/track_hints.json` (every track needs `first_drop_sec`, `first_break_sec`, `outro_start_sec` with exact filename keys including extension). `--previews-only` bypasses the gate (previews are how hints get authored). `--no-hints-required` bypasses the gate for development/debugging only. This was added 2026-05-19 because Claude kept forgetting the visual-pass-first rule even though it was documented above. The gate makes it structural rather than memory-dependent. (Sam, 2026-05-19)
- **`/section-detection` pipeline LOCKED IN — algorithm + Claude corrections = finished sections .als (2026-05-20)** — validated end-to-end on Black Book x Defected V2 (V13 → V19). The canonical chopping pipeline is now: (1) `orchestrator.py --sections-layout` for the programmatic pass, (2) `extract_sections_als.py` → JSON, (3) `sections_blind_viz.py` to render **8 quarter PNGs per track** (NOT 4 — 4 missed 1-2 bar fills), (4) Claude reads every PNG and fills `BLIND_VALIDATION_V<N>.md` per-chop table (hard self-check: chop count must equal row count), (5) for `⚠ off N` errors, edit `apply_section_corrections.py` CORRECTIONS list and patch the .als directly. Algorithm tuning is limited to ONE round per project — beyond that, accept and correct manually. `sections_compare_viz.py` exists in the codebase but is FORBIDDEN by the skill (V7-diff trap). Arrangement positioning (`arrange_sections.py`) is the next step AFTER chops are locked, using natural-fill alignment (incoming.drop_1 aligned to outgoing's last fill/break before outro). Skill auto-fires on triggers like "section detection", "Sections V<N>", `phrase_viz.py`, paths under `Sections Review/` etc. — Sam shouldn't have to type the slash command. (Sam, 2026-05-20)

## Connections

- **Social Media Content Engine** — completed mixes become showreel content for social media
- **samwillsmixing.com** — mixes serve as portfolio demos / musical showreels
- **Wired Masters** — showcases tracks the studio has put out
