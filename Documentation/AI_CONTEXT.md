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
├── report.py              — Per-track CSV + per-mix Markdown reports
├── waveform_preview.py    — Blank-canvas preview PNG (for writing visual hints before pipeline run)
├── desktop_analyzer.py    — MIK + Rekordbox desktop UI automation (staging folder, dialog detection)
└── config.py              — Settings loader
```

Pipeline: desktop analysis (MIK + RB) → analysis → Rekordbox enrichment → sequencing → warping → per-beat features (cached) → factual intervals → ranked cue candidates → candidate-driven transition planning → ALS generation → objective validation.

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

**Four-phase mix pipeline operational. Stem-kick beat detector (our own IP) WIRED INTO the pipeline via `--stem-grid` — self-sufficient (own broadband transient timing, matches Ableton ~1ms, no .asd/RB needed), adversarially verified, gate-guarded. ⚠ OPEN BLOCKER: the arrangement/automation LOOP layer corrupts the mix downstream — see Known Issues.**

**⚠ OPEN BLOCKER (end of 2026-06-24, Sam's Ableton eyeball on In-Key Mix V4):** the grids are good (Mr V base warp = smooth 124bpm) but the mix is wrong downstream — **big loops (`apply_loops` cloning 22× / 15× clips) drift the per-clip shift so sections + automation walk out of place**. NOT the detector — the loop layer. Park + fix tomorrow: (1) trace `apply_loops` shift math on big loops; (2) **cap loop length** (no 88-bar tail loops — bug magnet + musically wrong); (3) re-check automation targets post-loop beats; (4) **open the .als in Ableton before calling done.** Process gap: the Phase-4 FULL transition pictures render SOURCE envelopes at arrangement positions, NOT the actual warped output / cloned loop clips — so they can't catch warp/section/loop placement. Only the Ableton eyeball does. Fix the review step.

**As of 2026-06-24 PM (STEM-KICK DETECTOR → PIPELINE-READY + ADVERSARIALLY VERIFIED):**
- **Timing self-sufficient + gate-guarded:** `detect_transients()` (spectral flux on stem ∪ full mix = Ableton's method) refines per-beat timing to ~1ms vs Ableton with NO `.asd` (snaps to `.asd` when present for 0ms). 3 gate holes closed (commit 24a0b89): JIT fires post-snap on kf>15; the beatgrid gate FAILs stem grids with grid_vs_kick>15ms (no more blanket stem_fitted pass); no-RB JIT tracks hard-stop. `Tests/test_beatgrid_stem_gate.py`. First two real-mix casualties: Afro/Latin (Izinque) + jackin' (Natural High) correctly excluded — detector validated on clean 4-to-floor house only.

**As of 2026-06-24 PM (STEM-KICK DETECTOR → PIPELINE-READY + ADVERSARIALLY VERIFIED):**
- **Detector now in the package: `Source/automated_dj_mixes/stem_grid.py`** (committed; the `_Bakeoff` copy was scratch). Algorithm (Sam+Claude): kick onsets (sub <150Hz) + snare onsets (200–3kHz) from the Demucs drum stem → period histogram → smooth UnivariateSpline grid (NOT raw onsets — the warp-jitter fix) → downbeat fusion. Sam's "sub locates, click times": `refine_to_click()` snaps to the >1.5kHz beater transient.
- **Adam Ten FAR fixed:** histogram-mode seed period (0.0006s off) compounded across a 137-beat breakdown → 44% kicks rejected. Fix: `_robust_period` (per-segment-median seed) + ±0.3% lstsq clamp → 44%→99% spine, **0 regressions** (6 strategies tested on the corpus).
- **Validation reframed RB-INDEPENDENT:** `grid_vs_kick` is the warp-fidelity truth (**median 1.60ms/35, 34/35 within 15ms**). RB is advisory — confirmed RB locks the wrong tempo on 3 house tracks (we sit <3.4ms on the kicks, RB 111–123ms off). The kicks arbitrate.
- **Wired into the pipeline (`--stem-grid`):** `detect_beat_grid()` → `BeatGrid` emits the exact RB contract (`beat_times_ms` + `first_downbeat_offset`); `extrapolate_grid()` covers the full file at edge tempo. Injected into `rb_matches` BEFORE every grid consumer → **one-clock invariant holds by construction**. Confident grids = authority, RB = cross-check; LOWC/JIT keep RB. Requires `--stem-sections` (the one-clock-safe cut path).
- **Adversarial 4-lens verification (Workflow) found+fixed 4 blockers — incl. the bug Sam heard, in OUR detector:** (1) flat-snare false veto inverted a confident downbeat (Definite Grooves phase 3→0 = the V3 clash) → parity-pair + contrast gate + honest agreement (0/35 confident-wrong, crack-band verified); (2) beatgrid gate false-failed perfect stem grids (10/35 hard-stop) → wired `stem_fitted` provenance + `verdict_from` bypasses the librosa-R test for stem grids (35/35 PASS, no MIK); (3) realigned `a.first_downbeat_sec` (latent 2nd clock); (4) edge guards (negative SecTime, <2-beat grid, <1s input, --stem-grid requires --stem-sections). **46/46 pass the full warp-marker contract.**
- **Open (non-blocking, documented in memory `project-stem-kick-grid-detector`):** bass-stem downbeat cue, tighter crack-band snare cue, snare-coincidence MIX gate, half-time octave folding, Phase-1a stem reuse, features.py grid-aware cache key. Next: Sam's ear on a real `--stem-grid --stem-sections` render (the Ableton eyeball is his gate).

**As of 2026-06-24 (THIRD-PARTY /mix 23.06.26 → In-Key Mix V3 + STRATEGIC PIVOT to curated-mix market):** Sam dropped 11 fresh **third-party** deep/soulful-house tracks (`Test Project/23.06.26`, 118–125 BPM) and said `/mix`. Ran the full three-phase pipeline end-to-end → **`Output/In-Key Mix V3.als`** (11 tracks, 10 transitions, 14 loop specs, 0 chop corrections; review in `Output/Visualisations/REVIEW_V3.md`). **Two blockers cleared first:** (1) a **THIRD duplicate-gate bug** — the master-file gate exists twice (orchestrator + `desktop_analyzer._validate_masters_only`); the orchestrator honoured `--allow-non-master` but the driver copy still raised, caught as a warning, so MIK/RB never imported the tracks → RB 0/11. Threaded `allow_non_master` through `_validate_masters_only` + both `analyze_folder_with_*` signatures. (2) **C: 100% full** — Dropbox is a junction to another drive, so clearing old test mixes freed *that* drive, not C:; the real culprit was 35 GB in C:'s Recycle Bin (Sam OK'd emptying it). After both: **MIK 11/11 + RB 11/11 enriched**. Pipeline: beatgrid gate **11/11 PASS** (5 clean R 0.44–0.84, **4 rescued by the MIK tempo tiebreaker** — soft swung house kicks smear kick-phase R; all phase advisory, no `.asd` ticks), one-clock 11/11, validate_als PASS ×3. DETECT chop review run as an **11-agent Workflow fan-out** (ultracode) = 0 corrections (Osunlade's 104-bar drop hand-rechecked — genuine long groove). Hints derived from section boundaries → 1f.5 gate **44/44 ✓**. Arrangement: harmonic 7/10 identical-smooth, **T7 1A→8A is a forced harmonic clash** (no bridging key in the pool, not an error). Transition review = **10-agent Workflow**; 3 "misaligned" verdicts personally re-read and confirmed **false alarms** (T2 a **renderer layout bug** that compresses short-overlap FULL views to the left edge → 73 KB file; T3/T5 read-time downscaling; T6 raw-energy-vs-automation). **0 algorithmic errors.** All-tracks-read −14 LUFS (streaming masters) so levelling did ~0 dB. **Open:** Sam's ear on V3; **viz layout-bug fix** spawned as a task (`transition_review_viz.py` short-overlap FULL width); Sam's 11.06.26 V1 car-verdict still pending. **STRATEGIC PIVOT (this session):** the loudness-levelling work opened a real buyer — **curated playlists + DJ mixes for hotels/bars** (companies that do both). This shifts the project from "monthly showcase + maybe-sell-to-labels" to a genuine product with a market. Implication: **quality must step up hard, and analysis is the #1 lever** (everything downstream depends on it). Next: a no-code-changes **brainstorm on the analysis stack** — what we have, SOTA we're not using (allin1 structure model, Beat This!/madmom DL beat-tracking to kill the Rekordbox dependency, Essentia for key/energy/loudness, BS-RoFormer stems, MERT/CLAP embeddings), the ideal-but-nonexistent "DJ mix-sheet" tool, and build difficulty (off-the-shelf adoption → fusion/consensus layer → moonshot: learn mix points from real DJ sets).

**As of 2026-06-12 overnight (V1 WARP POST-MORTEM → GATE v3 → RB-LESS REBUILD → In-Key Mix V2, rendered by automating Ableton itself):** Sam's ear on In-Key Mix V1: warping "all over the place", naming Hold Me/Blackout/Bullerengue/La Trumpter (+Floorplan/Huxley close-not-close) — **exactly the four modified-grid tracks**. Forensics (5 probes): override propagation clean, timeline clean, per-track librosa metrics no worse than the ear-validated 22.05 V4 control — the ruler itself was the bug. **Root cause: librosa kick-onset phase carries up to ~100ms track-dependent bias** (Latin percussion in the kick band skews the circular mean); the gate's overrides "corrected" three healthy RB grids 25-36ms off the transients and re-passed them with the same biased ruler. **Sam's .asd insight cracked it**: Ableton caches sample-accurate transient ticks (OnSets/Positions uint32 array) next to every analyzed WAV — `Source/asd_onsets.py` parses them; against that ruler all 12 original RB grids measured ≤14ms (RB was never wrong) and the three overrides were the poison. **Gate v3**: phase verdicts/overrides are tick-based (12ms PASS/20ms FAIL); librosa phase is advisory-only (can never FAIL or write overrides); tick-lattice agreement = independent tempo confirmation (replaces the MIK tiebreaker when its machine-local DB is absent). **Machine discovery**: this session ran on Sam's home PC — RB/MIK DBs are machine-local (home copies stale since May), .asd files sync with the project via Dropbox → **RB-less mode**: `fit_grids_from_ticks.py` fitted all 12 grids to ticks (≤0.7ms; Cure WARN drift ±9ms = real tempo wobble), orchestrator synthesizes `[tick-grid]` shells, grid = BPM authority for a.bpm. Two regressions caught en route: La Trumpter one-beat downbeat slip (hint-diff = the parity oracle; `verify_grid_bar_parity.py` can be fooled by pre-roll warp markers) and a Repitch leak (lattice BPMs hit the old ≤1.0 threshold → 7 tracks would have detuned; now ≤0.05 + grid-true a.bpm). Rebuild V3→V6 sections (gate 12/12, all phases ≤0.8ms vs ticks, DETECT 12/12 read clean, hints gate 44/44) → V7 arrangement (11 transitions) → **In-Key Mix V2.als** (LUFS-levelled, validate_als OK, tick probe ≤0.8ms every track). **In-Ableton eyeball** (Sam's request, via new `Source/ableton_ui.py` pyautogui driver — crash-recovery dialogs handled, virtual-desktop-origin click calibration): T1/T2/T4/T10/T11 zoomed shots show kicks columned across overlapping lanes, swaps on exact bar lines, a tail-loop clip on-grid (screenshots in repo `Output/_ableton_check/`). **Bounce automated**: drove Live's Export Audio/Video (Main, 1.1.1→1870, 44.1k/24-bit WAV) → `Output/In-Key Mix V2.wav` for Sam's remote listen (he's away ~4 days). Suite 84/84. **Backlog:** variable tick-grids for wobbly tracks (Cure); loop-aware viz mapping; gate PASS message still says "confirmed by MIK" when the confirmation came from ticks; ableton_ui drag-zoom x-calibration drifts ~5 bars at the far end.

**As of 2026-06-12 midday (V2 CAR VERDICT → DRUM-STEM RULER → In-Key Mix V3):** Sam's first V2 verdict: "Idris is still well out" — and he was right past every automated ruler. `probe_stem_kick_grid.py` (full-res Demucs drums, sample-accurate attack edges) proved La Trumpter's kicks +113.5ms (one SIXTEENTH) off the tick-fitted grid: **on Latin material Ableton's ticks sit on the anticipating congas, not the kick** — the same trap as librosa, one ruler deeper. `refit_grid_from_stem.py` = the real drum-stem escalation (coarse phase-centering to escape the wrong-sixteenth basin → LSQ lattice fit → bass-vote downbeat; tumbao bass anticipates so the vote is advisory on Latin tracks). 12-track sweep: Sam's flags ranked exactly by measured offset (+113 La Trumpter / +46 Floorplan / +25 Huxley); his standing rule captured in memory: **fix to the measurement floor, never calibrate tolerances to his flags** — all 12 grids shifted onto stem kicks (+6..+48.5ms). Gate v3.1 ruler hierarchy: **drum-stem kicks > Ableton ticks > librosa** (stem-provenance overrides judged on stem evidence; tick offset informational). Rebuilt V8 sections → V9 arrangement → **In-Key Mix V3.als/wav** (24-bit render, 59.5min). **New standing pre-render gate (Sam's request): zoomed in-Ableton screenshots of all transitions — 11/11 verified on exact bar lines with kick columns aligned before rendering.** Next: Sam's ear on V3; AbletonOSC adoption discussion (security review then install — OSC for hands, pixels for eyes); fuse stem-kick harvest into the Phase-1a separation pass (free on GPU); variable stem grid for Cure's wobble.

**As of 2026-06-11 late evening (FIRST FULL /mix THROUGH ALL THE NEW GATES → In-Key Mix V1, superseded by V2 above):** Sam dropped 12 fresh commercial tracks (`Test Project/Test Mix 11.06.26`) and said `/mix` — the pipeline ran end-to-end to **`Output/In-Key Mix V1.als`** (12 tracks, 11 transitions, 0 harmonic clashes, 0 chop corrections, LUFS-levelled; full review in `Output/Visualisations/REVIEW_V1.md`). The beatgrid gate earned its keep on first contact: hard-stopped run 1 with 5 FAILs → **gate v2: MIK tiebreaker** (two independent analyzers agreeing on tempo rescues percussion-smeared R; never overrides bad phase, internally-inconsistent grids never rescued) fixed 2 false tempo-fails; 3 genuine phase-shifts fixed via `--write-override` (+85.9/+97.4/−83.1ms, all re-gate at phase 0.00); and 1 REAL broken grid (La Trumpter — span 123.87 vs RB DB 125.00 vs MIK 126.00) → Sam confirmed 126 → built the **`replace_grid` override** live (`_fit_anchor` kick-fits a constant grid, gate-proves before writing) → track restored, 12/12 PASS. Also found+fixed a **vacuous-pass hole in `validate_hints_vs_sections`** (first runs had no ARRANGEMENT_REPORT for BPMs → 0 checks → "PASS"; now stem-JSON BPM fallback + zero-checks=FAIL + semantics aligned with the model: first_break AFTER first drop, last_bass_drop in the pre-outro 32-bar swap window) → 44/44 real checks. Suite 78/78. **Listen flags for Sam:** T4/T9 ride tail-loop regions (Floorplan 4×, Hyzteria 11× — monotony), T8 tight 19-bar quick swap, four 56-57-bar long blends. **Backlog:** transition_review_viz can't render looped outgoing tails (linear source mapping — T4/T9 lanes empty); stem_detector `--write-hints` standalone path expects retired blind-viz stats (worked around by deriving hints from the fresh SECTIONS_STEM JSONs); extract_sections_als names exactly-V1 extracts `V1_baseline.json` (copied to `Sections_V1.json` for the gate).

**As of 2026-06-11 evening (WARP/CUT REGRESSION FIXED — awaiting Sam's ear):** The morning's blocker is diagnosed and fixed in code; `Sections V4.als` (11 tracks, Kelly excluded) builds clean through the whole new chain. **Two causes, not one:** (1) **Cuts off = two-clock bug** — the stem path converted section times to beats via librosa's *quantized* tempo (a ~2.5% lattice that cannot say 128.00) while audio warps to the per-beat RB grid; the old RB-phrase path cut on grid beats, which is why pre-Demucs mixes were fine. Measured on Todd: cuts +3→+9.5 beats off. **Fixed with the one-clock rule** — detector runs on grid BPM + true downbeat, and section boundaries map through `warping.sec_to_clip_beats` (the warp-marker convention) then bar-snap. (2) **Warp out = Todd's grid PHASE-shifted** (+0.15 beat ≈ 70ms — tempo locked; "markers floating between transients" quantified), not the suspected ~1%-off-tempo grids (Say My Name's grid is fine; 129.2-vs-128 was librosa lattice junk). **Fixed via `Hints/grid_overrides.json`** (+72.5ms, written by `validate_beatgrid.py --write-override`, applied by the orchestrator before enrichment). **New hard gate:** `Source/validate_beatgrid.py` — whole-track kick-phase concentration vs a per-track +1% detuned control (window sampling and offset-magnitude approaches both proven unreliable during calibration) — wired into sections-layout, `--allow-bad-grids` to override; calibrated on 22 tracks (every validated-good track passes, the acapella and pre-fix Todd fail). Suite 63→75. Drum-stem beat-tracking = documented escalation if a track ever FAILs on TEMPO (none does). **NEXT: re-run the full /mix (sections → arrangement → automation) on V4's chain and Sam listens** — the DETECT pictures are regenerated and the Phase-1d scan must not be skipped. Kelly G.'s WAV moved to `_Excluded Audio/`. Full detail: memory `project-warp-beatgrid-bug`.

**As of 2026-06-11 morning (GPU win + engine hardening + a real warp bug found — superseded by the evening fix above):** Infrastructure + correctness session on the **Test Mix 09.06.26** (12 tracks). Shipped: (1) **GPU stem separation** — `stem_section_probe.py` is device-aware (`cuda` if available); a full 12-track Phase 1a dropped from 30+ min to ~4.5 min (`a13d801`; needs a torch cu128 build — memory `reference-gpu-stem-separation`). (2) **Template-capacity fix** — `_count_audio_tracks` now excludes the reserved Session-Time track, so a 12-track mix no longer silently drops the 12th (Huxley) onto an 11-slot template (`d456787`). (3) **Transition-viz fix** — `transition_review_viz` unescapes track names before the WAV match, so tracks with `&`/`'` no longer skip (`b6720b2`). (4) **Golden-mix regression test** — `Tests/test_align_engine_golden.py` pins the validated 08.06.26 arrangement (swaps/break-skip/loops); suite 59→63 (`68d7973`). (5) **`/mix` + `/section-detection` rewritten** to the DETECT-picture + transition-picture review (retired the 80-PNG blind pass + `validate_sections_review` gate), 3-brain synced. **OPEN — THE BLOCKER:** Sam reviewed In-Key Mix V1 FINAL by ear → **section cuts off + Todd Edwards warping out** ("markers aren't even close"). Diagnosed: clips warp to each track's **Rekordbox beatgrid**, but those grids read ~1% off the actual audio (Todd grid 128.0 vs audio 129.2) → drift + the bar-based cuts land off — one bug, both symptoms. **Blocked on Sam** confirming whether his RB grids are tight (decides: fix track-matching vs beat-analyse from audio). Kelly G. = an acapella accidentally included (remove it; its WAV was Ableton-locked at session end). Full handoff: memory `project-warp-beatgrid-bug`. **Process note:** I skipped the Phase 1d DETECT-picture scan this run, which would have caught the over-segmentation (Say My Name → 19 sections) before the mix — don't skip it again.

**As of 2026-06-08 (latest — AUTONOMOUS MIX + ARRANGEMENT ENGINE):** Two milestones. (1) Wired the stem detector INTO the pipeline (`--stem-sections`) + auto-hints (`stem_detector.py --write-hints` passes the production gate) → ran the full 3-phase chain with ZERO manual input → **first fully-autonomous mix** (`Autonomous Mix V1 FINAL.als`). (2) Captured Sam's DJ arrangement model and built `Source/align_engine.py` — a **bass-to-bass alignment engine + per-transition visualiser** (testing tool; production stays autonomous). The model: **natural-marker coincidence BEATS literal bass-to-bass** — the bass switch can be *faked* (drop the outgoing's bass early at any natural marker), so alignment = slide the incoming on the 8-bar grid to maximise energy-matched section coincidence; clean bass swap = the mix point, volume blends slowly; swap in outgoing's last min + incoming's first min; short 1–2 bar loops/cuts. It reproduces Sam's expert Call Me→Samm read exactly. Sam eyeballed all 9 transitions of `08.06.26` (verdicts in memory `reference-arrangement-model`). **CRITICAL NEXT (learned the hard way — I handed Sam a mock built by the OLD single-anchor `propose_arrangement`, which is NOT what he wants): the #1 job is to WIRE `align_engine` INTO THE ALS** so a render reflects the new engine. Only then is a mock worth his ears. Also queued: T7 lineup-0 deep-dive, T3 break-start swap, T2 prefer-natural-bass-out; detector fixes (James Poole phantom `build`+short drop, My Own Thang split drops → one 32-bar); GPU cu128 build (wheel confirmed, deferred — needs torch+torchaudio+torchcodec co-versioning). New `--order` flag on the orchestrator overrides the auto-sequencer for testing arrangements.

**As of 2026-06-08 (earlier — STEM-BASED DETECTION, the strategic pivot):** Sam reframed the goal — if section detection were reliable, mixing is a "jigsaw" (label the pieces; assembly is rule-based). A spike proved **Demucs stems** are a far cleaner detection substrate than the 3-band amplitude detector (it had mislabelled VLAD's 148s "build"). Built `Source/stem_detector.py` (committed ca58fd4 → 6d5fe97) and **calibrated it by eye with Sam across all 10 tracks** of `08.06.26 Mix`. **Analysis-only** (in-memory Demucs, `soundfile` I/O, original WAV untouched, envelopes cached). It outputs labelled sections (intro/drop/break/fill/outro, bar counts) + the mix signals that matter: **kick cues, fills, bass-to-bass regions, loop windows (drums-on/bass-off), vocal regions (clash avoidance), ~1-min in/out cues**. Calibration rules: dynamic kick threshold (0.80 × the 2-means *solid* full-drop kick level), kick out→in = new 16-beat section, drop = sustained top-tier energy, break = kick/bass-out >6 bars, fill = kick-out ≤6 bars flanked by drops (edge fills fold into breaks), pre-drop long kick-out = "first break", outro = end of last fill (else lead-drop), every track guaranteed intro+outro. See memory `reference-stem-section-detector`. **Standalone so far — NOT yet wired into the pipeline.** Next: make it the section source (replace/augment `phrase_viz`), CUDA torch build for GPU Demucs, and an ensemble (stems vs RB phrases vs amplitude → agreement = confidence → only review disagreements). Essentia flagged to eventually also own key (MIK) + beats (RB). This is the path to a *sellable*, owned analysis stack (the MIK/RB UI-automation dependency is the productisation blocker).

**As of 2026-06-08 (Rekordbox weak-link hardening):** Hit "Communication with rekordboxAgent failed" for the first time mid-run on the new `08.06.26 Mix` (10 tracks). Root cause: the `rekordboxAgent` `options.json` was pinned to an old **rekordbox 7.0.1** (`app_ver`/`lang-path`) while the launcher used **7.2.14** — a version-mismatched handshake caused by **three coexisting installs** (7.0.1 / 7.2.12 / 7.2.14). Not firewall (network profile Private, loopback port 30001 free/exempt). **Fix — environment:** reset the stale `options.json` (reversible `.bak`), Sam uninstalled 7.0.1 + 7.2.12 (kept only 7.2.14), Desktop shortcut recreated → 7.2.14. **Reboot + one clean RB launch still pending** to re-register the install for pyrekordbox + analyse the 10 tracks. **Fix — code hardening** (`desktop_analyzer.py` + `orchestrator.py`): version-pinned launch of the newest install (not the drifting shortcut); full-family ordered kill; **agent-state preflight** that detects a stale version-pin and auto-resets it (kills the exact 06-08 cause); port-30001 conflict guard; **agent-error-dialog detection + bounded self-recovering relaunch** (`_launch_rb_healthy`) instead of blindly burning the window timeout; and the most important change — `enforce_rekordbox_coverage`, a **HARD GATE that stops the pipeline if any track lacks RB phrase data** (the real 06-08 bug was the orchestrator catching the failure, printing "continuing", and silently building a mix on 3/10 tracks), with a `--allow-partial-rekordbox` opt-in. New `Tests/test_rekordbox_health.py` (11 tests); suite 45→56 all green; gate proven live (hard-stops on the 08.06.26 tracks). **Next session: after reboot, launch RB 7.2.14 once, then re-run the pipeline and resume the test mix.** Tidy-up pending: `/mix` skill Phase 0b 3-brain doc sync.

**As of 2026-06-01 (Opus 4.8 audit + fix campaign):** consolidated the track matcher (killed the resurrected "Your Love" 20-char prefix bug still live in `apply_automation` + `learn_from_correction`), fixed the backwards energy-arc cooldown, **auto-wired `validate_als` into every `.als` write** (was an orphan run only by hand), right-sized template selection, guarded zero-length clips, and ported the stranded loop-quality / `loop_source_sec` / `intro_skip_bars` features into the three-phase production path. Full test suite 45/45 green. **Awaiting Sam's render of one test mix to confirm the Wave-2 behaviour changes by ear.** Also this session: **collapsed to ONE production pipeline** — retired the old single-command "Path A" engine (deleted `transition.py` / `transition_viz.py` / `track_viz.py` / `validation.py` / `skills/`, stripped the orchestrator full-mix back-half + `--visualize`, ~2,000 lines), leaving **`--sections-layout` → `propose_arrangement` → `apply_automation`** as the sole path; the bare full-mix mode now raises a "retired" error. Full breakdown in the 2026-06-01 session entry below.

**As of 2026-05-22**: 22.05.26 Mix produced V4.als end-to-end. 7 robustness gaps closed, plus 4 more bug classes surfaced during real-world run and fixed live. `/validate` auto-fires before every "done" claim. `validate_als.py` has 4 layers covering the failure modes shipped to date. Sam is rendering V4 to listen on holiday — next session picks up with his listen notes.

0. **Desktop analysis** — WORKING. `desktop_analyzer.py` drives MIK + Rekordbox via Win32 API with auto-detecting folder dialog handlers. Staging folder pattern bridges both dialog types. 10/10 tracks analyzed end-to-end on 2026-05-21.

1. **Section detection** — LOCKED IN. `/section-detection` skill with blind validation. No changes.
2. **Arrangement** — NEW. `/arrange-mix` skill built this session. `propose_arrangement.py` computes natural-fill alignment with overlap capping (~128 beats target). `apply_loops.py` handles mechanical clip cloning for loop extensions. Tested on V18→V25: all 9 tracks positioned with 124-132 beat overlaps (31-33 bars). Positions within 32-168 beats of Sam's V20 (gap largely from V20's loop extensions not yet added).
3. **Automation** — V24 analysis complete. `apply_automation.py` has 6 learned rules with two critical bug fixes (priority 2 boundary check + Rule 2 boundary guard). Accuracy: V21 5/9 → V23 ~7/9 → V24-effective ~8/9.

**Sections pipeline LOCKED IN (unchanged).** `/section-detection` skill with un-skippable blind validation.

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

### 2026-06-11 late evening (Latest Session) — First full /mix through all the new gates → In-Key Mix V1

**Focus**: Sam: "there are some new tracks at this location /mix" — Test Mix 11.06.26, 12 fresh commercial tracks. First production run exercising every gate built this week, end-to-end autonomous.

**Completed**:
- **Phase 0**: 0a flagged nothing (12 real tracks); desktop analysis MIK 12/12 + RB 12/12 + previews 12/12.
- **Beatgrid gate v2 — MIK tiebreaker** (`Source/validate_beatgrid.py`): run 1 hard-stopped with 5 FAILs. Probe showed 11/12 grids agree with MIK exactly → Floorplan + Light It Up were FALSE tempo-fails (Latin/gospel percussion smears the half-beat-circle R below the absolute threshold). Added the principled rescue: `verdict_from(..., tempo_confirmed)` — requires R≥0.20, ≥5× detuned control, grid internally consistent (span vs RB DB ≤0.5%), MIK agreement ≤0.2%, clean phase. Never rescues noise-floor grids (acapella class), never overrides a bad phase.
- **3 phase corrections** via the proven `--write-override`: Hold Me +85.9ms, Blackout +97.4ms, Bullerengue −83.1ms → all re-gate at phase 0.00.
- **`replace_grid` override — the escalation case, built live**: La Trumpter (Sam's own master) had an internally inconsistent RB grid (span 123.87 / DB 125.00 / MIK 126.00). Sam confirmed 126 → `_fit_anchor` (pure, kick-phase zeroing, bar-phase inherited from the old grid's downbeat) + `write_grid_replacement` (PROVES the fit with the gate before writing — refuses a failing fit) + `apply_grid_override` synthesizes the full constant grid. Fitted: first=0.430s, 708 beats, offset 1 → PASS phase 0.00, 15× control. Track restored; 12/12 PASS on the final run.
- **Vacuous-pass hole fixed in `validate_hints_vs_sections.py`**: `_bpm_lookup` only read ARRANGEMENT_REPORT (a Phase-2 artifact) → on FIRST runs every track was skipped and the gate "PASSed" over 0 checks. Fixed: stem-JSON BPM fallback + `rows == 0` → exit 2. Also aligned validator semantics with the current model (first_break = first break AFTER the first drop — Hyzteria has a pre-drop intro break; last_bass_drop ∈ pre-outro 32-bar swap window, not inside-outro). 44/44 checks PASS.
- **Phase 1d**: all 12 DETECT pictures read — zero chop corrections (notes: La Trumpter 18 busy-but-honest sections; Blackout's 44-bar BAR-MATH flags are real structure; Floorplan/Cure short outros).
- **Phases 2-4**: align_engine arrangement (11 transitions, overlaps 19-57 bars, 13 loop fills, 0 clashes, BPM Δ≤3, energy crest on Hyzteria E8 with the fixed descending cooldown) → automation + LUFS levelling (anchor Hold Me −10.2) → all 11 FULL transition pictures read → `REVIEW_V1.md`. Every .als validate_als [OK].
- Hints derived from the verified grid-true stem JSONs via `hints_from_stem_result` (the standalone `--write-hints` CLI expects retired blind-viz stats JSONs — worked around; backlog: make it fall back to SECTIONS_STEM files).

**Key Learnings**:
- **A gate that compares nothing must FAIL, not pass.** The hints gate had been capable of vacuous passes since it was built — first virgin-project run exposed it.
- **Two independent analyzers agreeing beats one noisy stat.** MIK-vs-grid agreement is a stronger tempo signal than any threshold on a single concentration measurement — and the detuned-twin control stays as the floor.
- **Genre changes calibration.** Thresholds set on clean four-on-floor pools under-score percussion-heavy material; build in independent evidence, don't loosen thresholds.
- **The viz can't render looped outgoing tails** (linear arrangement→source mapping) — T4/T9 lanes empty in the FULL pictures. Backlog: loop-aware mapping in `transition_review_viz`.
- Naming bridges: exactly-"Sections V1" extracts as `V1_baseline.json` (copied to `Sections_V1.json` for the gate).

### 2026-06-11 evening — Warp/cut regression diagnosed + fixed (one-clock + beatgrid gate + phase override)

**Focus**: Sam's steer: RB is analysis-only (he doesn't DJ), warp was validated good pre-Demucs, suspect the analysis change. Meticulous look-back → diagnosis → staged fix, all proven on real data.

**Diagnosis (two causes, not the morning's "one bug"):**
- **Cuts = two-clock bug.** Git archaeology proved the warp-marker chain unchanged since the good mix; the NEW stem path converts section seconds→bars→beats via constant `analysis.bpm` = librosa's beat_track, which is **quantized to a ~2.5% lattice** (cannot output 128.00; said 129.20 for five unrelated tracks) — while audio warps to the per-beat RB grid. The old RB-phrase path cut on grid-native beats (one clock) = why pre-Demucs warping was "boxed off". Plus the downbeat anchor used `beat_times_ms[0]`, not the offset-indexed true downbeat (3 beats early on Todd). Measured: Todd's cuts +3.0→+9.5 beats off, compounding.
- **Warp = Todd's grid PHASE-shifted, not tempo-wrong.** Whole-track kick-phase analysis: Todd tempo-locked (R=0.56) but markers +0.15 beats (~70ms) off the kicks — exactly Sam's "floating between the transients" screenshot. Yesterday's "~1% off grids" theory was wrong for the others — Say My Name etc. PASS; the 129.2-vs-128 discrepancy was librosa lattice junk, and there are no RB duplicates.

**Fixes (all proven):**
- **One-clock rule**: `warping.grid_bpm_and_downbeat` + `warping.sec_to_clip_beats` (grid-exact, matches the warp-marker convention); detector parameterized from the grid (`[one-clock]` log lines); `segments_from_stem_sections(…, beat_times_ms, first_downbeat_offset)` maps section TIMES through the grid + bar-snaps with zero-length/monotonic guards; `analysis.py` downbeat anchor offset-indexed.
- **Beatgrid gate** (`Source/validate_beatgrid.py`): whole-track kick onsets (150Hz lowpass — NOT mel fmax, which produced empty filters), HALF-beat-circle concentration R (folds house offbeat-bass stabs), full-circle mean phase, per-track +1% detuned twin as a known-bad control. Hard stop in sections-layout, `--allow-bad-grids` override, `--write-override` measures + writes phase corrections. Calibration on 22 tracks killed two earlier designs (20s windows = sampling luck — a 1%-off grid cycles through alignment every ~47s; offset magnitude = biased by onset lag).
- **Grid overrides**: `Hints/grid_overrides.json`, applied by the orchestrator BEFORE enrichment so warp/cuts/gate all see the corrected grid. Todd +72.5ms → re-gates at phase +0.00 PASS.
- Kelly G. (acapella) moved to `_Excluded Audio/`; 11 tracks remain.

**Validation**: suite 63→75 (12 new tests incl. the regression case + Todd phase case + verdict boundaries); golden-mix test green; Todd before/after cut-error probe on real data; full production re-run (`--sections-layout --stem-sections`) = `[one-clock]` 11/11, gate 11/11 PASS, stem JSONs grid-true, DETECT pictures regenerated, `Sections V4.als` + validate_als [OK]. (First re-run attempt accidentally omitted `--stem-sections` — caught via fractional-bar anomaly + zero one-clock banners; verify the flags, not just the exit code.)

**Next**: full /mix re-run on the fixed chain (Phase-1d DETECT scan NOT to be skipped) → arrangement → automation → **Sam's ear on the render** = final validation.

### 2026-06-11 morning — GPU stem separation + engine hardening + a real warp bug found

**Focus**: Run a take-home mix for Test Mix 09.06.26 — turned into a GPU win, three engine fixes, a regression test, and surfacing a real warp/beatgrid bug.

**Completed**:
- **GPU stem separation** (`Source/stem_section_probe.py`, `a13d801`) — device-aware `_device()` (cuda/cpu); installed torch/torchaudio 2.11.0+cu128 on Carillon AC-1 (RTX 3050). ~8-13s/track vs minutes; 12-track Phase 1a ≈ 4.5 min. The parked "GPU speed" win — done. Memory `reference-gpu-stem-separation`.
- **Template-capacity fix** (`orchestrator.py` `_count_audio_tracks`, `d456787`) — counts usable mix tracks (minus the reserved Session-Time track); a 12-track mix now lands on the 35-track template instead of silently dropping Huxley off the 11-slot one.
- **Transition-viz fix** (`transition_review_viz.py`, `b6720b2`) — `html.unescape` track names before the WAV match; tracks with `&`/`'` no longer skip (only T11 had rendered before).
- **Golden-mix regression test** (`Tests/test_align_engine_golden.py`, `68d7973`) — pins the validated 08.06.26 arrangement (swaps `[528,1136,...]`, break-skip at T2 only, 6 intro + 9 outro loops) + a pure `align_pair` check. Suite 59→63.
- **Skill rewrite** — `/mix` + `/section-detection` moved to the DETECT-picture (per-track) + transition-picture (per-transition) review; retired the 80-PNG blind pass, the `BLIND_VALIDATION` verdict table, and the `validate_sections_review` hard gate. 3-brain byte-identical (Dropbox brain folders). `orchestrator.py` make_viz=True (`20928d3`).
- Produced In-Key Mix V1 FINAL (12 tracks, loops + break-skip + LUFS levelling) — but it carries the warp/cut bug below.

**Key Learnings**:
- **Warp/cut bug (OPEN, blocked on Sam)** — clips warp to each track's Rekordbox beatgrid, but the grids read ~1% off the actual audio (Todd grid 128.0 vs audio 129.2; round BPMs 128.00/131.00/127.00) → warp drifts + the bar-based cuts land off (one bug, both symptoms). Two RB BPMs appear (enrichment 129.2 vs `read_rekordbox_library` 128.01 — duplicate entries?). Blocked on Sam confirming his RB grids are tight. Full detail: memory `project-warp-beatgrid-bug`.
- **Don't skip the Phase 1d DETECT scan** — I trusted the stem detector and skipped the per-track picture scan; it would have caught Say My Name over-segmenting to 19 sections before the mix shipped. Reinforces `feedback_never_skip_visual_review`.
- Kelly G. "Power Of One (Melvo Lead BKG)" was an **acapella** — no beats → garbage RB grid → its "intro-outro, no drops" detection AND broken warp. Strip accidental acapellas from the input.

### 2026-06-01 (Previous Session) — Opus 4.8 audit + two-wave bug-fix campaign

**Focus**: Sam (back from a week away, now on Opus 4.8) asked for a fresh audit of the whole pipeline for holes the previous model missed + easy wins. Ran a 4-agent parallel code audit, verified every finding by hand, then fixed everything across two waves. Sam chose "do it all, render once."

**The two-path discovery (root of half the doc drift):** there are effectively TWO mix paths sharing a front half (analyse → harmonic sequence + energy arc). Path A = the old single-command `run_pipeline()` (forbidden for real mixes). Path B = the three-phase `/mix` (sections → `propose_arrangement` → `apply_automation`). The previous model rewrote Path B's stages 2-3 from scratch but left several features stranded in Path A's `transition.py` — so the skill documented loop-quality / `loop_source_sec` / `intro_skip_bars` as working when they only ran on the path you're told never to use.

**Wave 1 — confirmed bugs + safety net (validated by code tests):**
- **Track matcher**: the 20-char prefix bug (the "Your Love"/"Your Love (Instrumental Mix)" collision thought killed in May) was STILL live in `apply_automation.match_tracks_to_als` and `learn_from_correction._match_name`. `apply_automation` now routes through the canonical `apply_loops._match_track` (exact-first, then substring, NO prefix); `learn_from_correction` dropped its prefix clauses; removed a dead duplicate `return None`.
- **Energy arc**: `sequencer.apply_energy_arc` cooldown re-spiked to its loudest track right after the peak (peak sorted descending). Peak now ASCENDING — energy crests at the 2/3 mark then falls to a quiet finish.
- **`validate_als` auto-wired**: was an orphan (manual-only). Added `report_als()` and called it at the tail of all three `compress_als()` copies (als_generator, apply_automation, apply_loops) — every emitted .als now self-validates with an `[OK]/[FAIL]` banner. This safety net then backstopped the Wave-2 clip surgery.
- **`clone_clip`** raises on zero/negative-length clips (was silent corruption); **`_find_template`** picks the smallest template that fits (was always the 35-track one); **`enrich_from_mik`** no longer overwrites a good LUFS with `None` and now logs swallowed DB errors; first-drop window: verified `(30,75)` is Sam's deliberate rule, fixed the stale "30-120s" docstring.

**Wave 2 — ported stranded features into Path B (need a render to confirm musically):**
- **`loop_source_sec`** hint now honoured by `propose_arrangement._plan_loop_extensions` (directs the outgoing tail-loop source).
- **Loop-quality gate**: tail loops run through `amplitude_analysis.find_clean_loop_window` to avoid dead-air/dissipating regions (reuses the tuned detector; falls back to the section default on error). Replaces the never-reached `transition.py` `MIN_LOOP_QUALITY` gate.
- **`intro_skip_bars`** now actually removes the skipped sections' clips from the .als (new `apply_loops.remove_named_clips`) — previously only dropped from alignment maths so the intro still played. Removal validated on a real .als.
- **Multi-loop fix**: `apply_loops` re-finds each track's range per spec instead of one cumulative offset — a middle track with BOTH an intro loop and a tail loop used to push the second insertion past the track. Validated by inserting 2 specs into the same track on Sections V18.als (clips +4, validate_als clean).
- **`apply_automation` phrase-snap (audit item)** — verified NON-issue: its swaps return section `arr_time` = chops, which ARE the phrase lineup points by construction. A global snap would have regressed it. No change made.

**Validation**: full suite 45/45 (fixed a pre-existing stale `test_automation.py` that imported the removed `generate_transition`); behavioural asserts for matcher exact-first / energy-arc shape / clip guard / validate_als catching bad XML; multi-loop + clip-removal tested on real Sections V18.als; a full `propose_arrangement` run on V18 produced a valid .als (`[OK]`). **Next: Sam renders one test mix to confirm the Wave-2 behaviour changes by ear.**

**Recommended follow-ups (not done):** archive ~20 dead research scripts in `Source/` root; populate `Documentation/Golden Sections/` baselines (regression gate is currently a no-op); sync the `/mix` skill gap-table across the 3 brains to mark loop-quality/intro_skip/loop_source as now-working.

### 2026-05-22 (Latest Session) — Robustness gauntlet + Validation Discipline meta-rule + 22.05.26 Mix V4

**Focus**: After the V11/Latest Releases Mix listen revealed alignment + automation gaps, do a deep robustness pass on the whole pipeline. Then run end-to-end on a new 22.05.26 Mix and surface every bug class the gates didn't yet catch.

**Phase 1 — Lessons from /mix V11 (visual review):**
- Built 3-band envelopes (low <250 Hz / mid 250-2500 / high >2500) into `sections_blind_viz.py` — the missing layer that distinguished "bass-only DJ outro" (correct) from "outro labelled mid-drop" (wrong).
- Added overview PNG per track + per-section stats JSON + auto-flag heuristics + `NOTES.md` scratchpad template.
- Built `transition_review_viz.py` with both `_ZOOM` (overlap close-up) and `_FULL` (Ableton arrangement view — both full tracks at arrangement positions) outputs.
- Caught 3 real V12 chop errors (Lifeline outro, Slippin break_3, Tumblr Girls outro) the algorithm missed.

**Phase 2 — Pipeline audit (Documentation/PIPELINE_AUDIT.md):**
Identified 7 robustness gaps and closed them all:
1. `validate_sections_review.py` — hard gate after Phase 1d. Parses `BLIND_VALIDATION_V<N>.md` and fails if any row is missing band stats, verdict, or ✗-without-correction. Tracks attempts in `validation_state.json`; escalates after 2 same-error attempts with `ESCALATE.md`.
2. `validate_hints_vs_sections.py` — hard gate after Phase 1f. Compares `track_hints.json` timestamps to section boundaries via BPM; fails if >8 bars off. Found 12 silent disagreements in existing Latest Releases Mix hints.
3. Auto-propose corrections — `sections_blind_viz.py` now emits `PROPOSED_CORRECTIONS_V<N>.json` with `(track_substr, from_clip, to_clip, old_bar, new_bar_or_DELETE, arr_offset)`. `apply_section_corrections.py` accepts `--corrections-json` + `"DELETE"` sentinel + cascading-DELETE chain handling.
4. `loop_review_viz.py` — per-loop PNG (waveform + 3-band envelopes + quality score + rep count). Phase 4b.5.
5. Attempt counter (folded into validator) — escalates on persistent same-error.
6. `regress_section_detection.py` + `Documentation/Golden Sections/` — pre-commit test against blessed section JSONs from past projects.
7. TL;DR template baked into Phase 4c REVIEW format.

**Phase 3 — `/validate` meta-skill + Validation Discipline rule:**
- Created `/validate` skill — auto-detects target type (skill .md / .py / .als / pipeline output) and runs the appropriate validator.
- Added **Validation Discipline** meta-rule to all 3 brains (`Claude Code Brain/CLAUDE.md`, `Codex Brain/AGENTS.md`, `Antigravity Brain/GEMINI.md`): "No work is complete until validated by an artifact you didn't write yourself."
- `/validate` is in the **Auto-Fire Skills** section — silently runs before any "done" report. Triggers: edited `.md` in `commands/`, edited `.py` in `Source/`, produced a pipeline output, user asks "are you sure" or "did that actually work."

**Phase 4 — 22.05.26 Mix end-to-end (22 tracks):**
Real-world run surfaced 4 more bug classes the gates didn't catch, all fixed live:
- **XML entity collision** — `&apos;` / `&amp;` in section JSON keys broke BPM lookup, validator heading matching, and `find_track_block` in 3 sites. All fixed (entity-tolerant matching).
- **V4 ScaleInformation corruption** — `apply_loops.py` overwrote integer `<Name>` inside `<ScaleInformation>` with the clip name string. Ableton rejected the file with "Unexpected value for int node." Fixed with an `in_scale_info` flag in the clone loop.
- **Built `validate_als.py`** — gzip+XML parse, integer type checks on known fields, **clip sanity** (no zero/negative-length clips), and **track ordering** (AudioTrack file order must match arrangement time order). Four layers, run after every .als-producing phase. Catches synthetic injection of all known corruptions.
- **Auto-propose bounds** — `sections_blind_viz.py` now clamps proposed bars to leave ≥4 bars of section length on both sides. Without this, the band-derivative search returned a bar AT or PAST the to_clip's end, producing zero/negative-length outros in 7 of 22 tracks. 9 clamps fired on real data.
- **Tail loop placement** — loops were being inserted AFTER the outro (drop_7 → outro_1 → drop_7_tai), producing musically backwards energy. Now inserted BEFORE the outro with `shifts_before_insert` pushing the outro later in `apply_loops.py`. Sequence is now drop_7 → tail_loops → outro_1 → end.
- **`_match_track` collision bug** — `nn[:20]` loose-prefix match was matching "Mike Richters - Your Love" to "Mike Richters - Your Love (Instrumental Mix)" via shared 20-char prefix. Both Your Love shifts hit the Instrumental track; the regular Your Love never shifted. Fixed in BOTH copies of `_match_track` (apply_loops.py and the duplicate in propose_arrangement.py), then consolidated by deleting the duplicate and importing the canonical version.

**Final V4 output**: 22 tracks in clean monotonic order, 0 collapsed clips, 6 loops correctly placed, all 4 als gates pass. Sam rendered for car listening.

**Key Learnings**:
- Three-band envelope (low/mid/high) is the difference between catching real chop errors and being fooled by amplitude-only envelopes. Bass-stripped DJ outros look like full-energy mistakes in amp-only view.
- Validation gates are only as strong as the layers they explicitly check. Each bug class we ship discovers a layer the gates didn't cover. Add it then; the next bug will find the next gap.
- Duplicate functions are the most insidious bug — fix one copy and the other lurks. Consolidate ruthlessly.
- "Validated by an artifact you didn't write yourself" — algorithmic auto-proposals checking algorithmic sections is the SAME source twice. Real validation requires an outside signal (a script running, a diff against a known-good baseline, or a human ear).

**Files changed**:
- NEW: `Source/validate_als.py`, `Source/validate_sections_review.py`, `Source/validate_hints_vs_sections.py`, `Source/loop_review_viz.py`, `Source/regress_section_detection.py`, `Source/transition_review_viz.py`, `Documentation/Golden Sections/README.md`, `Documentation/PIPELINE_AUDIT.md`
- EDITED: `Source/sections_blind_viz.py` (un-hardcoded + 3-band + auto-propose + bounds), `Source/apply_section_corrections.py` (--corrections-json + DELETE sentinel + entity-tolerant), `Source/apply_loops.py` (3-band consolidation + ScaleInformation guard + shifts_before_insert + _match_track tightening), `Source/propose_arrangement.py` (best_swap_source + outro shift + _match_track consolidation)
- SKILLS (all 3 brains synced byte-identical): `commands/mix.md` (Phase 1c/1d.5/1e/1f.5/2/3/4 gates wired in), `commands/validate.md` (NEW), `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` (Validation Discipline + /validate auto-fire)

### 2026-05-21 (Session 7) — All 9 pipeline gaps closed

**Focus**: Implement all 9 pipeline gaps documented in the `/mix` skill, following a methodical 4-stream plan approved by Sam.

**Stream 1 — Data Foundation (Gaps 1, 2, 8, 9):**
- Fixed `enrich_from_mik()` bug — key/BPM from MIKStore.db now copied back to `MikTrackData`. Orchestrator patches `TrackAnalysis.key`, `.camelot`, `.bpm` after enrichment.
- Gap 2 (MIK cue reading) confirmed unsolvable — MIKStore.db has no cue table for WAV files.
- Gap 8 (template capacity) already solved — 35-track template exists, `_find_template()` now selects by audio track count (not modification date).
- Gap 9: `ARRANGEMENT_REPORT.json` now includes per-track `camelot`, `bpm`, `energy`, `intro_skip_bars` and per-transition `harmonic_score`, `harmonic_type`, `bpm_delta`, `selected_style`, `loop_source`, `overlap_bars`.

**Stream 2 — Smarter Sequencing (Gaps 3, 4):**
- `build_harmonic_path()` now uses composite score: `(camelot_norm * 0.6) + (bpm_norm * 0.4)`. Both normalized to 0-1 scale.
- `apply_energy_arc()` post-pass: divides tracks into build/peak/cooldown thirds, sorts by MIK OverallEnergy. BPM-gap guard rejects if reorder creates 15+ BPM gap.

**Stream 3 — Hints Extensions (Gaps 6, 7):**
- `intro_skip_bars` in hints modifies clip sample start offset (Ableton `CurrentStart`), not timeline position.
- `loop_source_sec` in hints directs loop search to a specific mid-track region, quality gate still applies.

**Stream 4 — Transition Style Variety (Gap 5):**
- `TransitionStyle` enum: STANDARD, LONG_BLEND, QUICK_SWAP. Auto-selected by overlap length.
- Style-specific automation generators: QUICK_SWAP (instant swap, no sneak), LONG_BLEND (linear crossfade, partial EQ, delayed swap), STANDARD (existing two-phase).
- `learn_from_correction.py` now classifies which TransitionStyle Sam's corrections most closely match, stores in `pair_history.jsonl`.

**Updated `/mix` skill across all 3 brains** (Claude Code, Codex, Antigravity) — pipeline gaps table now shows all 9 closed or documented. Added `--hints` flag, transition styles docs, richer arrangement report review checklist.

**Files changed**: `mik_reader.py`, `orchestrator.py`, `sequencer.py`, `propose_arrangement.py`, `transition.py`, `apply_automation.py`, `learn_from_correction.py`, all 3 brain `mix.md` files, `AI_CONTEXT.md`, `ai-activity-log.md`.

### 2026-05-21 (Session 6) — /mix skill complete rewrite + loop quality gate + pipeline gaps documented

**Focus**: Complete rewrite of the `/mix` skill across all three brains (Claude Code, Codex, Antigravity) to incorporate all learnings from the project.

**Completed: Mix V1 generated via old single-command pipeline (Latest Releases Mix).** 10 tracks, 9 transitions, all pass visual review. But missing section chopping, arrangement optimisation, and learned automation rules.

**Completed: Loop quality gate added to transition.py.** `MIN_LOOP_QUALITY = 0.20` threshold in `_score_loop_interval()`. Eats Everything had a bad loop (score 0.11 — dissipating hi-hat at end of track) that slipped through. Gate now rejects sparse loops and falls back to intro.

**Completed: Full `/mix` skill rewrite.** Replaced the single-command orchestrator workflow with the proven three-phase pipeline: (1) Phase 0: Setup + Desktop Analysis (MIK/RB), (2) Phase 1: Section Detection + 8-quarter blind PNGs + COMBINED visual pass (section validation + hint authoring in one step) + corrections, (3) Phase 2: Arrangement via propose_arrangement.py, (4) Phase 3: Automation via apply_automation.py with learned rules, (5) Phase 4: Final visual review, (6) Phase 5: Report. Key improvements over old skill: 8-quarter PNGs for hint authoring (catches 1-2 bar fills), section chopping as required step, pipeline gaps table (9 known missing features), anti-patterns list.

**Completed: Pipeline gaps documented.** 9 known limitations: MIK key/cue reading from DB, track sequencing/energy arc, BPM proximity sorting, transition style variety, clip trim/skip, mid-track loop source, template capacity, key signature display.

**Files changed**: Claude Code Brain/commands/mix.md, Codex Brain/commands/mix.md, Antigravity Brain/commands/mix.md (all three rewritten), Source/automated_dj_mixes/transition.py (MIN_LOOP_QUALITY gate), Documentation/AI_CONTEXT.md, .github/ai-activity-log.md.

### 2026-05-21 (Session 5) — Desktop automation fix + full MIK/RB pipeline end-to-end

**Focus**: Fix the broken browse dialog handling for both MIK and Rekordbox, clean MIK DB, run full pipeline on Sam's last 10 released tracks.

**Problem**: `_select_folder_in_browse_dialog()` could not reliably navigate either app's folder dialog. Three approaches failed for MIK (Edit text, Enter key, junction). MIK kept importing hundreds of wrong files from Desktop. RB's dialog was misidentified as old-style when it's actually a modern IFileDialog.

**Completed: rewrote `desktop_analyzer.py` with dialog-type detection.**
- Discovered Windows has TWO fundamentally different folder dialog APIs: MIK uses `SHBrowseForFolder` (TreeView-based, OK follows tree selection), RB uses `IFileDialog` (Vista+, address bar + Folder text field + Select Folder button).
- `_select_folder_in_browse_dialog()` now auto-detects dialog type via child control signatures (ComboBoxEx32/ToolbarWindow32 = modern, SysTreeView32 = old-style) and delegates to the correct handler.
- MIK handler: pywinauto `tree.get_item("\\Desktop\\_Pipeline_Import")` selects TreeView node directly → BM_CLICK OK.
- RB handler: two-step confirmation — (1) set path in "Folder:" Edit + Enter to navigate, (2) `WM_COMMAND IDOK` to confirm. Single-step failed because Enter navigates INTO folder but doesn't select it.
- Alt-tap focus-stealing bypass (`keybd_event(VK_MENU)` before `SetForegroundWindow`).
- RB launches via Desktop shortcut (versioned subfolder breaks direct exe paths). Kill+relaunch retry on menu navigation failure.
- Staging folder (`Desktop/_Pipeline_Import/`) created BEFORE dialog opens (was after — timing bug).

**Completed: cleaned MIK DB (358 junk entries from previous failed imports).**
Deleted non-master rows + VACUUM. Added `is_mik_analyzed()` filename fallback for staging paths.

**Completed: full pipeline end-to-end on 10 tracks (Latest Releases Mix).**
MIK 10/10 analyzed, RB 10/10 enriched with phrase data, 10/10 preview PNGs generated. Sam confirmed successful run.

**Known gaps**: MIK cue points (0/10) and key data not showing in previews — MIK stores these in DB for WAV files, not in GEOB ID3 tags. `mik_reader.py` only reads GEOB tags. Low priority.

**Files changed**: `Source/automated_dj_mixes/desktop_analyzer.py` (major rewrite — dialog detection, staging folder, focus bypass, RB shortcut launch, two dialog handlers).

### 2026-05-21 (Sessions 1-4) — Full PROPOSE→LEARN cycle (V21→V24) + `/arrange-mix` skill built

**Session 1: Built apply_automation.py, generated V21.** Sam corrected → V22.

**Session 2: V21→V22 diff → 6 rules. V23 generated with rules baked in.** Effective ~6-7/9.

**Session 3: V23→V24 diff → bug fixes + accuracy to ~8/9.**
- V24 analysis: 6/9 raw, ~8/9 effective. T4 was only real correction (two bugs: priority 2 boundary check + Rule 2 boundary guard). T8/T9 arrangement noise.
- Two bug fixes applied to apply_automation.py: (A) _inside_overlap() enforced on priority 2 outro, (B) two-stage bass kill_beat checked against boundary.
- Accuracy progression: V21 5/9 → V23 ~7/9 → V24-effective ~8/9.

**Session 4 (this session): Built `/arrange-mix` skill.**
- `Source/apply_loops.py` (~300 lines) — mechanical clip cloning. LoopSpec dataclass, clone_clip with unique ID allocation, handles self-closing Events blocks. Not yet tested with actual loops.
- `Source/propose_arrangement.py` (~450 lines) — arrangement orchestrator. Natural-fill alignment (incoming.first_drop at outgoing.last_fill/break) + overlap capping (TARGET 128b, cap threshold 144b). Analyses each pair for loop requirements, consults pair_history.jsonl. Generates arranged ALS + JSON report.
- Tested V18→V25: all 9 positions verified, overlaps 124-132 beats (31-33 bars). Positions within 32-168 beats of V20 (gap from V20's loop extensions).
- Created `~/.claude/commands/arrange-mix.md` skill file.

**Files added**: Source/propose_arrangement.py, Source/apply_loops.py, ~/.claude/commands/arrange-mix.md.
**Files output**: Sections V25.als + arrangement report JSON.

### 2026-05-20 — Section-detection pipeline LOCKED IN + arrangement principle learned (V13→V20)

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

> **🔴 TOP — BLOCKED ON SAM: fix the warp/beatgrid bug (Test Mix 09.06.26).** Clips warp to each track's Rekordbox beatgrid, but the read grids are ~1% off the actual audio (Todd grid 128.0 vs audio 129.2) → warp drifts + the bar-based section cuts land off. **Blocked on Sam's answer:** are these tracks' RB grids tight when he DJs them? → if yes, fix track-matching (wrong/duplicate RB entry); if no, beat-analyse from the audio (or he re-grids in RB). Then re-run **without Kelly G.** (acapella accidentally included; its WAV was Ableton-locked — run from a copied input folder or once closed) and verify warp-vs-audio + the cuts. Full handoff: memory `project-warp-beatgrid-bug`. Then resume the `als_io.py` refactor (#2, dedup decompress/find_track_line_ranges/_normalise/_match_track across 9 files — golden test is now the safety net).

> **Production-polish backlog** — the smaller "mix → Wired Masters production" details (future fine-tuning, not urgent): see [`Documentation/Production Polish Backlog.md`](Production%20Polish%20Backlog.md). (1) transition loudness compensation — duck ~0.25–0.5 dB so overlaps don't creep louder; (2) bass-switch energy match — boost the incoming bass to hold energy across the swap, then fade it out. Captured from Sam 2026-06-10 after V17 listen.

1. **Render a test mix to validate the 2026-06-01 Wave-2 changes** — the loop-quality gate, `loop_source_sec`, `intro_skip_bars` trimming, and the multi-loop fix all change how a mix sounds. Run `/mix` on a project, render, and listen. This is the validation the code tests can't provide.
2. **Housekeeping from the audit** — archive ~20 dead research scripts in `Source/` root; populate `Documentation/Golden Sections/` with blessed baselines (the regression gate is currently a no-op without them); sync the `/mix` skill gap-table across the 3 brains to mark loop-quality/intro_skip/loop_source as now-working.
3. **Run `/mix` three-phase pipeline on Latest Releases Mix** — Mix V1 was generated via the old single-command pipeline (no section chopping, no arrangement optimisation). Need to re-run using the new three-phase `/mix` skill (sections → arrangement → automation) to produce colour-coded section clips, natural-fill alignment, and learned automation. Hints already exist (10/10 tracks hinted). This will be the first test of the newly-closed pipeline gaps (harmonic sequencing, BPM proximity, energy arc, transition styles).
2. **End-to-end verification of all 9 closed gaps** — Run `--previews-only` to confirm WAV tracks now show Camelot keys + BPM. Run `--dry-run` arrangement to confirm track ordering shows BPM clustering + energy arc shape. Review ARRANGEMENT_REPORT.json for transition style variety.
3. **Add loop learning** — propose_arrangement.py currently produces uniform ~128b overlaps. V20 varies from 62-190b depending on loops Sam added. Need to analyse V20's loop patterns and feed them back into the proposal logic. apply_loops.py is built but not yet triggered by the proposer.
4. **Test hint extensions** — Add test `intro_skip_bars` and `loop_source_sec` entries to a project's hints and verify they work end-to-end.
5. **Expand pair_history.jsonl with style data** — Run `learn_from_correction.py` on existing Claude→Sam correction pairs to populate `classified_style` field. Future transitions with similar characteristics will auto-select the right style.

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
- **EMERGING rules need ≥3 observations before auto-applying broadly** — Rule 3 (two-stage volume) with 1 observation and section_count>=14 trigger caused false positives on 2/9 transitions. Rule 4 (low sneak) with `clips>=3 OR len<=32` caused 7/9 false positives. Conservative triggers or disable until confirmed. (2026-05-21 V23 testing)
- **V22 arrangement changes make V23↔V22 comparison noisy** — When Sam both corrects automation AND changes arrangement, the diff tool can't reliably separate the two. Clean comparisons require same base arrangement. Future: extract V22's section data separately for a fair comparison. (2026-05-21)
- **Boundary avoidance is NOT absolute** — T7 (Ease My Mind → Professor X) shows Sam accepted a boundary bass swap (beat 4304 = overlap end). When the overlap end coincides with a natural structural handoff (outro end + incoming drop), boundary swaps can work. Rule 1 needs a structural-handoff exception. (2026-05-21)
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
- **Two Windows folder dialog types require different automation strategies (2026-05-21)** — MIK uses old-style `SHBrowseForFolder` (TreeView-based, OK follows tree selection — Edit text is cosmetic). Rekordbox uses modern `IFileDialog` (Vista+, "Folder:" text field + Select Folder button — TreeView in left panel is Quick Access pins, not shell hierarchy). Auto-detection via child control signatures (ComboBoxEx32 = modern, SysTreeView32 only = old-style). Staging folder (`Desktop/_Pipeline_Import/`) is the bridge — shallow enough for both dialog types to reach. MUST be created before dialog opens (tree populates on open). Three approaches failed for MIK's old-style dialog before TreeView node selection worked: Edit text (ignored by OK button), Enter key, NTFS junction.
- **PROPOSE→LEARN cycle starts with automation, not arrangement extraction (2026-05-21)** — Sam's pivot: rather than passively extracting V20's patterns, Claude adds automation to V20 and Sam corrects it. The correction diff IS the first training data. This means Claude's proposals improve from real corrections, not from analyzing Sam's finished work. `apply_automation.py` handles the PROPOSE side; `learn_from_correction.py` (to be built) handles the LEARN side.
- **Automation lives on Utility Gain (volume) + ChannelEQ LowShelfGain (bass) — same as existing pipeline (2026-05-21)** — `apply_automation.py` follows the exact same targets discovered during the May 14-15 sessions. Volume on Utility (not mixer fader), EQ bass kill at 0.18 (~-15dB), two-phase transition model with section-structure-driven bass swap detection. Standalone script, not wired into orchestrator — this is for Sections .als files, not full pipeline mixes.
- **`/section-detection` pipeline LOCKED IN — algorithm + Claude corrections = finished sections .als (2026-05-20)** — validated end-to-end on Black Book x Defected V2 (V13 → V19). The canonical chopping pipeline is now: (1) `orchestrator.py --sections-layout` for the programmatic pass, (2) `extract_sections_als.py` → JSON, (3) `sections_blind_viz.py` to render **8 quarter PNGs per track** (NOT 4 — 4 missed 1-2 bar fills), (4) Claude reads every PNG and fills `BLIND_VALIDATION_V<N>.md` per-chop table (hard self-check: chop count must equal row count), (5) for `⚠ off N` errors, edit `apply_section_corrections.py` CORRECTIONS list and patch the .als directly. Algorithm tuning is limited to ONE round per project — beyond that, accept and correct manually. `sections_compare_viz.py` exists in the codebase but is FORBIDDEN by the skill (V7-diff trap). Arrangement positioning (`arrange_sections.py`) is the next step AFTER chops are locked, using natural-fill alignment (incoming.drop_1 aligned to outgoing's last fill/break before outro). Skill auto-fires on triggers like "section detection", "Sections V<N>", `phrase_viz.py`, paths under `Sections Review/` etc. — Sam shouldn't have to type the slash command. (Sam, 2026-05-20)

## Connections

- **Social Media Content Engine** — completed mixes become showreel content for social media
- **samwillsmixing.com** — mixes serve as portfolio demos / musical showreels
- **Wired Masters** — showcases tracks the studio has put out
