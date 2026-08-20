# All-In-One (allin1) Second-Opinion Evaluation

**Date:** 2026-08-20 · **Brain:** Claude · **Status:** Evaluation complete — hands-on, 6 tracks, GPU
**Question:** Is allin1 (Taejun Kim et al., music structure analyzer trained on Harmonix) worth wiring in as a second-opinion detector next to our stem/kick/energy detector?

**Verdict up front: YES — integrate as a flag-only second opinion.** On 6 tracks it confirmed 38/45 (84%) of our section boundaries, 33 of those within ±0.3 bars (i.e. dead on the bar line), found the Revoloution bar-147 boundary that every energy detector missed, and saw the Double Dutch break edges (bars 32 and 48) exactly. Its downbeat *phase* cannot be trusted (half-bar error on 2/6 tracks) and its labels need mapping, but as a boundary cross-check projected onto OUR grid it is the strongest independent signal we have tested. Runtime ~27 s/track on the RTX 3050.

---

## 1. Install outcome (Windows, STUDIO-2)

allin1 1.1.0 was already present in the main Python 3.14 env (with demucs 4.0.1 + torch 2.11.0+cu128) but **unrunnable** — two runtime deps missing and a third breakage hiding behind them. Everything below was done in an isolated venv (`--system-site-packages` over C:\Python314 to reuse the CUDA torch); **no repo or global-env changes**.

| Blocker | What happened | Fix |
| --- | --- | --- |
| **madmom** (not in allin1's pip deps; imported at runtime) | PyPI madmom 0.16.1 is broken on py>=3.10 | `pip install git+https://github.com/CPJKU/madmom` — built clean on py3.14 with MSVC Build Tools 2022 (0.17.dev0, numpy 2.4 OK) |
| **natten** (Darwin-only in allin1's deps; hard import on all platforms) | `natten==0.14.6` (the version with the old `natten1dqkrpb` functional API allin1 needs) **does not compile under MSVC** — its CPU kernels use GCC-only constructs; 100+ compile errors in `natten1dav_cpu_kernel.cpp`. No Windows wheels exist for any relevant version, and modern natten (0.17+/0.20+) renamed/removed the API allin1 calls. | **Wrote a pure-PyTorch shim** (190 lines) implementing the 4 ops allin1 uses (`natten1dqkrpb`, `natten1dav`, `natten2dqkrpb`, `natten2dav`), transcribed from natten 0.14.6's reference C++ CPU kernels (`natten_cpu_commons.h` window/bias logic), installed as a fake `natten` package in the venv. **Validated against literal Python transcriptions of the C++ loops: 176/176 case/device combinations pass (CPU + CUDA), max err < 2e-4.** Runs on GPU. |
| **demucs save crash** (torch >= 2.6) | torchaudio 2.11 delegates decode/encode to torchcodec, whose DLL needs FFmpeg *shared* libraries — absent here (winget ffmpeg is the static build). Demucs separation completed, then died writing stems (`ta.save`). Reading was fine (demucs uses the ffmpeg CLI to read). | Venv-local shadow copy of demucs with `save_audio` writing WAV via soundfile; plus a venv `sitecustomize.py` forcing `torch.load(weights_only=False)` (demucs/allin1 checkpoints are pickled objects, pre-2.6 style). |

Total dependency-wrangling time ~35 min. **Honest summary: allin1 does not install on Windows as published; it runs fully once madmom is built from git and natten is replaced by the validated shim.** On Linux (or WSL) it is a plain `pip install natten==0.14.6 allin1` and none of the shim work is needed.

Model weights downloaded: `taejunkim/allinone` 8-fold Harmonix ensemble = **11 MB** total; htdemucs = **84 MB** (was already in the torch hub cache).

## 2. Method

- Tracks: Sam Leagas - Double Dutch, Nic Fanciulli - Revoloution, Harry Romero - Renegades, Fish Go Deep - The Cure & The Cause, Ritmo Da Rua, Soulsearcher - Feelin Love (the season's problem children + two vocal tracks), from `Test Project/14.08.26/Audio`.
- allin1 run with the default `harmonix-all` 8-model ensemble, device=cuda, via its own demucs demix + madmom spectrogram pipeline.
- allin1 outputs seconds; converted to **our** bar grid from each track's SECTIONS_STEM json (uniform grid, bar 0 anchored at `sections[0].start_sec`, bar = 240/bpm). Agreement tolerance ±2 bars.
- allin1 segments at ~8-bar phrase granularity (17-23 internal boundaries per track vs our 5-10), so two match tiers are reported: any-boundary within ±2 bars, and the stricter **label-change** boundary within ±2 bars. Chance can't explain the observed precision: with ~8-bar spacing a random alignment lands within ±0.3 bars ~7.5% of the time; we observed 33/45 at ≤0.3 bars.

## 3. Six-track agreement summary

| Track | Our bounds confirmed (±2 bars) | ...of which label-change | Typical delta | allin1 internal bounds | Downbeat phase vs our grid |
| --- | --- | --- | --- | --- | --- |
| Double Dutch | **4/5** | 2/5 | 0.0 bars | 20 | clean (153/153 within half-beat, +0.026 bar) |
| Revoloution | **8/8** | 4/8 | +0.1..+0.3 | 20 | slight tempo divergence (65/164; drift to +0.27 bar by track end) |
| Renegades | **5/7** | 4/7 | 0.0 | 17 | **half-bar phase error, whole track (0/168)** |
| Fish Go Deep | **9/10** | 6/10 | 0.0..-1.5 | 22 | **half-bar phase error, most of track (71/183)** |
| Ritmo Da Rua | **7/8** | 4/8 | 0.0..+1.0 | 23 | clean (185/185, +0.008 bar) |
| Feelin Love | **5/7** | 1/7 | 0.0..+1.1 | 22 | clean (169/169, +0.056 bar) |
| **Total** | **38/45 (84%)** | 21/45 | 33/38 at ≤0.3 | ~20/track | 4/6 clean |

The 7 misses: three are our ≤4-bar fills/builds (Double Dutch fill@92, Renegades fill@124, Feelin Love build@100 — allin1 works at 8-bar granularity and glides over 4-bar events), four are 4-bar disagreements on break edges (Renegades break@88 — allin1 says 94; Ritmo break@100 — allin1 says 104; Feelin Love break-end@84 — allin1 says 80; Fish Go Deep drop@8 — allin1 keeps intro to 14.5). Worth spot-checking a couple by ear; on Ritmo the 100-vs-104 case is exactly the kind of tie a human should break.

### The three named checks

- **Double Dutch — does allin1 see the bars 32-48 break unaided? YES, edges exactly.** Boundaries at 59.09 s = bar 32.0 and 88.63 s = bar 48.0 (both d = 0.0), plus a phrase split at bar 40. It does NOT use a "break" label — it keeps the region "chorus" and marks the exit as chorus→verse. So: geometry perfect, semantics absent. (Note for ears: allin1 also calls bars 128-152 "outro" where we hold drop_3 — possible real disagreement about the last stretch of the track.)
- **Revoloution — any boundary near bar 147? YES: 277.81 s = bar 147.3.** A chorus→chorus segment split at the width-collapse spot every energy detector missed. This is the single strongest argument for wiring it in: an independent, spectrogram-transformer opinion that fires where our stem/energy features are blind. (All 8 of our boundaries were also confirmed at ≤0.3 bars on this track.)
- **Renegades — its read on the outro region:** agrees drop_4 starts at bar 128 (240.01 s, d = 0.0), then splits the tail at **bar 151.5 chorus→inst** and holds "inst" to the end (never the literal "outro" label). Translation: allin1 hears the last ~16 bars as a stripped-back instrumental — an outro-like transition our detector doesn't mark. Candidate review flag, not a correction.

## 4. Beat/downbeat sanity — the integration red line

- **Tempo/bar length:** essentially identical to ours on all 6 (its downbeat spacing matches our bar duration; reported BPM is integer-rounded).
- **Phase:** on 4/6 tracks its downbeats sit on our bar lines (median offset ≤ 0.06 bar, 100% within half a beat). On **Renegades** its downbeats are a constant **+0.496 bar (2 beats)** off — the classic four-on-the-floor downbeat ambiguity — and on **Fish Go Deep** the phase is half-bar wrong for most of the track with a mid-track flip (offset bimodal at ±0.5). On **Revoloution** the two grids drift apart by ~0.27 bar across the track (one of the two BPM estimates is ~0.3% off; unresolved which).
- **Rule for any integration: never adopt allin1's beats/downbeats/bpm. Always project its boundary seconds onto OUR validated grid** (as done in this eval). Notably its *section boundaries* landed on our bar lines even where its *downbeat phase* was shifted — the boundary head is robust to the phase error, so the projection is safe.

## 5. Label vocabulary -> our vocabulary

Labels observed across 6 tracks: `start, intro, verse, chorus, inst, bridge, outro, end` (its `break` label never fired on these). Proposed mapping:

| allin1 | ours | Notes |
| --- | --- | --- |
| intro | intro | Reliable at track head |
| chorus | drop | Main full-energy groove |
| verse | drop (vocal, reduced) or break | Ambiguous alone; combine with our stems_on — verse+no-bass ⇒ break |
| inst | break (mid-track) / outro (last ~16-24 bars) | "Stripped instrumental"; position decides |
| bridge | break | Rare (1 occurrence) |
| outro, end | outro | `end` is a <1 s terminal marker — discard |
| start | — | <0.1 s marker — discard |

Do **not** trust labels standalone; use label-*changes* as boundary evidence and the label pair only as a weak hint. 21/45 of our boundaries were confirmed by a label-change, the rest by phrase splits.

## 6. Integration verdict + costs

**Recommendation: integrate as a flag-only second opinion (same CueConfig gating pattern as kick cues), never as an authority.** Concretely:

1. At analysis time, run allin1 once per track; store raw output as `SECOND_OPINION_ALLIN1_<track>.json` beside SECTIONS_STEM (raw seconds + our-grid bar projection).
2. Comparator emits three flag types into `signals` (or a review report):
   - `unconfirmed_boundary` — our boundary with no allin1 boundary within ±2 bars (7/45 here; would have flagged 3 fills [expected — allin1 is 8-bar-blind, suppress flags for our sections ≤4 bars] and 4 genuine edge disagreements worth ears).
   - `candidate_missed_boundary` — an allin1 **label-change** boundary ≥4 bars from any of ours (this catches Revoloution 147 and Renegades 151.5).
   - `outro_hint` — allin1 inst/outro run covering the final bars while we hold `drop` (Renegades, Double Dutch tail).
3. Agreement-weighting of our detector's confidence can come later, once a season of flags has been reviewed.

**Costs (measured):**
- **Runtime:** 26.0-29.4 s/track end-to-end on the RTX 3050 (demix ~12 s, spectrograms ~5 s, 8-model ensemble inference + beat post ~10 s). A 20-track project ≈ 9-10 min. GPU peak ~3.5 GB. CPU-only untested; demucs would dominate (est. 5-10 min/track) — but we can feed allin1 our existing pipeline Demucs stems instead of re-demixing (its demix step checks for existing `htdemucs/<track>/{bass,drums,other,vocals}.wav` and skips), which would cut per-track cost to ~15 s and remove the double separation.
- **Weight:** models 95 MB total (htdemucs 84 already cached + ensemble 11). Venv delta 71 MB (madmom, build tools; torch reused). Byproducts if kept: ~40 MB spectrogram npy + ~220 MB stems per track (deletable).
- **Dependency risk (the real cost):** madmom-from-git needs MSVC at install time; natten needs our shim (inference-only, 4 ops, validated — but it is a re-implementation we own); torchaudio>=2.6 save workaround needed for demucs on this box. None of this touches the main env; an integration would reproduce the venv recipe or (cleaner) run allin1 in WSL. Pin everything.

## 7. Validation

- natten shim vs literal transcription of natten 0.14.6 C++ reference kernels: **176/176 pass** (K ∈ {3,5,7,13}, dilation 1-4, edge-length sequences, 1D+2D, CPU+CUDA) — `validate_natten_shim.py`.
- End-to-end sanity: allin1 BPM matches ours on all 6 (integer-rounded); downbeat count == our n_bars ±1 on all 6; first downbeat = our anchor (0.02 s) on the clean-phase tracks. Comparator re-run reproducibly from raw JSONs.

## 8. Artifacts (scratchpad, session `3226d5df`)

Base: `C:\Users\Carillon\AppData\Local\Temp\claude\C--Users-Carillon-Wired-Masters-Dropbox-Sam-Wills-0-1---GIT-HUB----Automated-DJ-Mixes\3226d5df-29b2-4c80-a16a-ed8ed4b6e472\scratchpad\`

- `venv-allin1\` — the working venv (py 3.14 + system torch cu128; madmom 0.17.dev0; natten shim at `Lib\site-packages\natten\`; patched demucs shadow copy; `sitecustomize.py`)
- `allin1_out\*.json` — raw allin1 results, 6 tracks (beats, downbeats, segments)
- `allin1_demix\htdemucs\<track>\` + `allin1_spec\` — kept byproducts
- `run_allin1.py`, `run_allin1.log`, `allin1_timings.json` — runner + timings
- `validate_natten_shim.py` — shim validator (176 cases)
- `compare_allin1.py`, `compare_output.txt`, `compare_summary.json` — full per-track boundary tables (everything summarized above, with every boundary delta)
- `natten-src\natten-0.14.6\` — reference C++ source the shim was validated against
