# Fable 5 Project Review — 2026-06-10

> **What this is:** a read-only review of the whole pipeline by Claude Fable 5, written as a work brief for Opus 4.8. Nothing in the codebase was changed. Every claim below was verified against the code (file:line refs), the git log (up to `946eb06`, 2026-06-09), and the live Python environment on Carillon AC-1.
>
> **How to use it (Opus):** work the phases in order. Each task has a **Validation** line — per the Validation Discipline rule, a task is not done until that artifact exists. Do NOT bundle tasks into one mega-refactor; land them one at a time with the test suite green between each.

---

## TL;DR

The architecture is genuinely good now: three clean phases, align_engine as sole position authority, an exact harmony-first sequencer, validation gates that came from real shipped bugs. The three biggest problems are **not** algorithmic:

1. **Demucs runs on CPU** — torch is the `2.11.0+cpu` build (`torch.cuda.is_available() == False`) while an RTX 3050 8GB sits idle. Stem separation is the slowest step in the pipeline and it's ~10–30× slower than it needs to be. One pip install fixes it.
2. **ALS I/O is independently implemented in 9 files** (4 named copies of `decompress_als` + 5 more files with inline `gzip.open` read/write of `.als`; `find_track_line_ranges` ×3, `_normalise` ×3, `_match_track` with a history of resurrection bugs). This is the exact bug class that produced the "Your Love" prefix collision twice. One shared module ends it.
3. **The 3,050-line arrangement/automation core has zero tests** (`propose_arrangement.py` 1,277 + `apply_automation.py` 986 + `apply_loops.py` 787), and `align_engine.py` + `stem_detector.py` (the two newest, most strategic modules) also have none — yet `align_pair()` is pure-function, trivially testable.

Beyond hygiene, the two highest-value *new* directions:

- **Close the loop without Ableton**: the stem envelope caches (`.npz`) already contain everything needed to compute summed-energy curves, vocal-clash overlap and kick-alignment cross-checks per transition — and to bounce approximate per-transition preview MP3s Sam can approve from his phone. Today nothing listens to the mix until Sam renders it.
- **The owned analysis stack is closer than the docs think**: Rekordbox is now only supplying BPM + beat grid + downbeat (phrases are already replaced by the stem detector). `Beat This` (CPJKU, ISMIR 2024 SOTA) can produce per-beat grids with the corpus you already have as ground truth to prove it. MIK is only supplying key + energy. That's the whole path to deleting `desktop_analyzer.py` (1,800 lines, the most fragile file in the repo) and unlocking productisation.

---

## 1. What's strong — do NOT "improve" these

Listing these so they don't get refactored into regression:

- **Line-level ALS text patching** (never ElementTree). Proven decision; Ableton rejects rewriter output. Keep it.
- **`sequencer.py`** — Held–Karp exact optimal path for ≤15 tracks with a strict lexicographic cost (clash ≫ smoothness ≫ BPM-ascent ≫ BPM-distance), harmony-preserving energy arc. Clean, tested, correct. Leave it alone (one *additive* idea in §6/E3).
- **`align_engine.py` design** — Sam's mixing model captured as an explicit, interpretable rule search (anchors × handoff candidates, ranked by lineup score with bass-to-bass tiebreak), position baseline pinned to 0.0 so the orchestrator can't contaminate positions (`align_engine.py:544`, commits `5c1f73f`, `171006e`). This is the right shape — a learned black box would be *worse* for this use case. Don't replace it; test it.
- **Stem-based section detection as the substrate** — section = which stems are playing. Correct domain insight, calibrated by Sam's eye across a real mix. New analyzers below are proposed as *ensemble votes around it*, not replacements.
- **The gates** — hint gate, Rekordbox coverage hard-stop, `validate_als` auto-run after every `.als` write, blind-validation workflow. Every one of these exists because a real bug shipped. Never bypass them to make a task pass.
- **Analysis caching discipline** — `.npz` stem envelopes per track, features cache keyed on mtime/size/`ANALYSIS_MODEL_VERSION`.

---

## 2. Environment findings (Carillon AC-1, verified 2026-06-10)

| Fact | Value | Implication |
|---|---|---|
| GPU | RTX 3050 8GB, driver 591.86 | Plenty for htdemucs inference |
| torch | **2.11.0+cpu** — `cuda_available: False` | Demucs separations run on CPU: the #1 fixable bottleneck |
| torchaudio / torchcodec | 2.11.0 / 0.11.1 (CPU) | Must be co-bumped with torch to the cu128 wheels (session notes 2026-06-08 already confirmed the wheel exists) |
| Python | **3.14.0** | Bleeding edge. librosa 0.11/numpy 2.4 work, but `madmom` is dead here (needs numpy<1.20) and anything depending on it won't import |
| allin1 | 1.1.0 installed, **import FAILS** | Broken on this env (madmom/numpy). Don't fight it natively — see C3 (WSL2) or drop it |
| demucs | 4.0.1 (htdemucs) | Fine. `htdemucs_ft` optional later; envelopes don't need it |
| onnxruntime | 1.24.4 already installed | Opens the `audio-separator` (Mel-Band RoFormer) option without new heavy deps |
| Dropbox | repo lives inside Dropbox | Conflict artifacts exist already (`Documentation/Mix Patterns Library/MIX_PATTERNS.md.tmp.7200.*`) — high-churn caches should not fight the sync client |

---

## 3. Phase A — Code health (low risk, do first)

### A1. Consolidate ALS I/O into one module — **P0**
**Why (verified by grep, 2026-06-10):** named `decompress_als` copies ×4 (`apply_loops.py:38`, `apply_automation.py:67`, `als_generator.py:57`, `learn_from_correction.py:36`) **plus** inline `gzip.open` ALS read/write in 5 more files (`apply_section_corrections.py:265,298`, `arrange_sections.py:171,190`, `extract_sections_als.py:20`, `orchestrator.py:34`, `validate_als.py:94`) — 9 files independently touching the gzip+XML format. Also `find_track_line_ranges` ×3, `_normalise` ×3, `_alloc_id` ×2, `_label`/`ordered_tracks` ×3. The May "Your Love" 20-char-prefix bug resurrected precisely because of copies. Any ALS-format change currently needs up to 9 coordinated edits.
**What:** new `Source/automated_dj_mixes/als_io.py` (or `Source/als_io.py` given the split layout): `decompress_als`, `compress_als` (with the `validate_als.report_als` hook built in, so no writer can forget it), `find_track_line_ranges`, `normalise_name`, `match_track` (the canonical exact-then-substring matcher), and an **instance-based `IdAllocator`** replacing the three module-level `_NEXT_ID` globals (`als_generator.py:35`, `apply_loops.py:148`, `apply_automation.py:56`). All callers import from it; delete the copies.
**Validation:** full test suite green; then run `propose_arrangement` + `apply_automation` on an existing project (e.g. Sections V18) before/after the refactor and diff the emitted `.als` — must be byte-identical (or differ only in already-random IDs, in which case compare `validate_als` + clip-count + position dumps).
**Effort:** M (half a day). **Risk:** low if landed as pure code-motion with the byte-diff check.

### A2. requirements.txt is missing half the real dependencies — **P0**
**Why:** `soundfile`, `matplotlib`, `numpy` are imported throughout but absent; `pyperclip` appears unused; torch/demucs are invisible. A fresh machine cannot install this project.
**What:** add `soundfile`, `matplotlib`, `numpy` with floors; a commented optional block for GPU (`torch/torchaudio/torchcodec` cu128 + `demucs`); remove `pyperclip` after grep confirms (only re-add if `desktop_analyzer` uses it dynamically).
**Validation:** `pip install -r requirements.txt` into a scratch venv, then `python -c "import automated_dj_mixes.orchestrator"` plus a `--sections-layout --help` smoke run.
**Effort:** S.

### A3. Fix the dead features cache path — **P0 (live bug on this machine)**
**Why:** `features.py:30-32` hardcodes `F:/Wired Masters Dropbox/...` as `DEFAULT_CACHE_DIR`. This machine is `C:`. Best case the cache silently never hits (every viz iteration re-runs librosa — the exact cost the cache was built to kill); worst case it errors on first write.
**What:** default to a per-project folder (`<project>/Analysis Cache/`) or `%LOCALAPPDATA%/AutomatedDJMixes/cache`, overridable via `config.py`. While there: hardcoded user paths in `mik_reader.py:23-26` and `desktop_analyzer.py:39-59` → move to `config.py` with the current values as defaults.
**Validation:** run a `--previews-only` pass twice on one track; second run must log a cache hit and be measurably faster.
**Effort:** S.

### A4. Tests for the untested core — **P0, the biggest quality lever**
**Why:** 0 tests across `propose_arrangement` / `apply_automation` / `apply_loops` / `align_engine` / `stem_detector` / `cue_candidates` (~5,200 lines). Current suite (56 tests) covers the old package modules only. Every regression so far was caught by Sam's eyes/ears — expensive.
**What (in value order):**
1. **`align_engine` unit tests** — `align_pair`, `_score_lineup`, `_handoff_candidates`, `plan_fill_or_cut`, `_resolve_break_to_break`, contraction propagation in `compute_aligned_positions`. These are pure functions over small dataclasses; synthetic `Track` fixtures take minutes to write. Encode the 9 verdicts from Sam's `08.06.26` eyeball session (memory `reference-arrangement-model`) as regression cases — that's expert ground truth nothing else captures.
2. **`stem_detector` golden tests** — commit 2–3 cached envelope `.npz` files (tiny) under `Tests/Fixtures/Stem Envelopes/` + the blessed `SECTIONS_STEM_*.json`; assert `detect()` reproduces the blessed sections. This finally arms `regress_section_detection.py` (currently a no-op — `Documentation/Golden Sections/` is empty).
3. **Three-phase integration test** — tiny 2-track fixture project (sine-sweep WAVs or 5-second real clips), canned stem JSONs, run sections → arrange → automate; assert `validate_als` OK, clip counts, monotonic track order, swap inside overlap.
**Validation:** the new tests fail when you deliberately break the thing they cover (mutate one threshold, watch it go red), then green on HEAD.
**Effort:** L (1–2 days) but pays for itself the first time Opus touches `propose_arrangement`.

### A5. Archive the research scripts — P1
**Why:** ~20 dead one-offs in `Source/` root (`check_bass.py`, `check_vlad_automation.py`, `extract_mix_patterns*.py`, `analyze_teaching.py`, `bass_detection.py`, `find_bass_swaps.py`, `diagnose_sections.py`, `arrange_sections.py` (superseded), `sections_compare_viz.py` (forbidden by skill), …). They bloat grep surface and confuse every new session. AI_CONTEXT already recommends this.
**What:** move to `Source/Archive/` (folder exists). Keep `learn_from_correction.py` OUT of the archive — it's the LEARN half of the PROPOSE→LEARN loop (TOOLING, not dead) and should be included in A1's consolidation (it carries its own `decompress_als`).
**Validation:** `/mix` skill dry-read: no skill/doc references a moved path (grep the three brains' `commands/` + `Documentation/`).
**Effort:** S.

### A6. Single source of truth for thresholds — P1
**Why:** magic numbers duplicated across files (kick 0.80 in `stem_detector.py:46`; EQ kill 0.18, sneak 0.2, boundary margin 64 in `apply_automation.py:39-51`; overlap targets in `propose_arrangement.py:56-70`; fill≤6 bars in two places). Tuning sessions with Sam edit these by hand — scattered constants mean missed copies.
**What:** keep constants AT the top of their owning module (that part is good — they're documented there), but kill *cross-file duplicates* and have `config.py` own anything two+ modules read (FILL_MAX_BARS, phrase grid, overlap targets). Don't build a config framework; a plain module is fine.
**Validation:** grep shows each constant defined exactly once; tests green.
**Effort:** S–M.

### A7. Silent-failure cleanup — P1
**Why:** bare `except Exception` swallowing at `analysis.py:70,81,437` (degrades to `first_downbeat_sec=0.0` with no warning — a wrong downbeat poisons every later phase, and you've already paid for downbeat bugs); `hints_from_stem_result` **fabricates** fallback hints (`stem_detector.py:556-564`: e.g. `first_drop = 16 bars`) specifically to pass the production gate, with no marker that they're fabricated.
**What:** log every swallow with the track name; add `"fabricated": true` (or confidence 0.3 vs 0.95) to fallback hints and surface a per-track WARN line in the autonomous path, so the review queue knows which tracks the detector actually failed on.
**Validation:** run `--write-hints` on a track with no detectable drop; the warning appears and the hint carries the flag.
**Effort:** S.

### A8. Packaging — P2
**Why:** `$env:PYTHONPATH="Source"` + sys.path hacks; `Source/` root vs package split is historical, not architectural.
**What:** minimal `pyproject.toml`, editable install, console entry points (`djmix-sections`, `djmix-arrange`, `djmix-automate`, `djmix-validate`). Don't move files yet (imports break cheaply — A1/A4 first); packaging makes the eventual move safe.
**Validation:** fresh venv, `pip install -e .`, run each entry point `--help`; suite green without PYTHONPATH.
**Effort:** M.

---

## 4. Phase B — Performance: light up the GPU

### B1. Install CUDA torch — **the single biggest speed win, ~30 min**
**Why:** every stem separation (`stem_section_probe.py:88`, `apply_model(..., device="cpu")`) runs minutes-per-track on CPU. On an RTX 3050 expect roughly seconds-to-tens-of-seconds per track (community benchmarks put htdemucs at ~5s/track on a 3090; a 3050 is ~4–6× slower — still a 10–30× win over CPU). New mixes are 10–22 tracks → this turns "go make tea" into "wait a minute".
**What:** `pip install torch torchaudio torchcodec --index-url https://download.pytorch.org/whl/cu128` (co-versioned set — session notes 2026-06-08 confirmed the cu128 wheel; verify cp314 availability, else pin the documented working trio). Then in `_separate_envelopes`: `device = "cuda" if torch.cuda.is_available() else "cpu"`, pass through to `apply_model`, and add `segment`/`split` defaults if 8GB VRAM trips on long tracks (htdemucs splits internally; if OOM, `apply_model(..., split=True, segment=12)`).
**Validation:** (1) `torch.cuda.is_available()` True; (2) delete one track's `.npz`, re-run detection on GPU, `np.allclose` the new envelopes vs the CPU-cached ones (rtol 1e-3 — separation is deterministic enough at envelope granularity); (3) log wall-clock before/after in the activity log.
**Effort:** S. **Risk:** low — envelope cache means a bad install can't corrupt anything already analysed.

### B2. (Optional, later) Higher-quality stems via `audio-separator` — P3
**Why/when:** only if vocal-region false positives/negatives are observed (vocal bleed into "other" causing missed clash warnings). [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) wraps Mel-Band RoFormer / BS-RoFormer (SDX23 winners, measurably above htdemucs on vocals/drums SDR) and runs on onnxruntime — already installed. **Not** a default swap: htdemucs envelopes are sufficient for section detection, and the calibration was done against them.
**Validation:** A/B the stem viz PNGs on the 2–3 tracks with known vocal-bleed annoyances; only adopt if Sam's eye prefers the new vocal lane. Bump `ANALYSIS_MODEL_VERSION` if adopted (cache invalidation).

### B3. Parallelise per-track analysis — P3
**Why:** tracks are independent; the pipeline is serial. With GPU Demucs the win shrinks, so this is last.
**What:** `ProcessPoolExecutor` over tracks for the librosa/feature stage only (keep Demucs serial on the single GPU). The module-level `_MODEL` global is per-process, so it's compatible.
**Validation:** identical JSON outputs serial vs parallel on one project; wall-clock logged.

---

## 5. Phase C — Analysis stack: ensemble now, owned stack next

> Strategic frame: the MIK + Rekordbox **desktop-automation dependency is the productisation blocker** (AI_CONTEXT, 2026-06-08). After the stem pivot, the *only* things still taken from the desktop apps are: RB → BPM + per-beat grid + downbeat; MIK → key + energy (+ trusted cues as a candidate source). Each has a credible open replacement, and — crucially — **you already own a labelled corpus to validate against**: every previously analysed track has RB grids, MIK keys, and Sam-verified sections on disk. That corpus is the moat; use it as the benchmark for every candidate below.

### C1. Beat This pilot — per-beat grids without Rekordbox — **the strategic one**
**Why:** [Beat This](https://github.com/CPJKU/beat_this) (CPJKU, ISMIR 2024) is the current SOTA beat/downbeat tracker, pure PyTorch (no madmom, no DBN), runs on the torch you already have, and outputs exactly what `warping.py` needs: per-beat timestamps + downbeats. If it matches RB grids on your corpus, the most fragile 1,800 lines in the repo (`desktop_analyzer.py` RB half: version pinning, agent handshakes, dialog automation) become deletable.
**What:** standalone `Source/beat_grid_probe.py` (mirror the stem-probe pattern): run Beat This over every WAV that already has an RB grid; report median/p95 per-beat deviation (ms), downbeat agreement, count of tracks drifting >1 beat anywhere. **Decision gate:** if median deviation <10ms and downbeats agree on ~95% of tracks, promote to a `--beat-source beatthis` orchestrator flag (RB stays default until a full mix ships on the new grids and survives Sam's ear).
**Validation:** the probe report itself (CSV per track) + one rendered A/B mix.
**Effort:** M for the probe; M for the flag. **Risk:** contained — probe is read-only; the flag is opt-in.

### C2. Key + energy without MIK — P2 (after C1 proves the pattern)
**Why:** MIK is genuinely best-in-class for EDM keys (~95% on dance material per the 2026 Dubspot lab test) — so this is the *last* dependency to cut, not the first. But for a sellable stack: [Essentia](https://essentia.upf.edu/reference/streaming_Key.html) ships `edma`/`edmm` key profiles *specifically tuned on EDM corpora*; energy is trivially replaceable (you already compute LUFS + RMS percentiles — MIK "OverallEnergy 1–10" can be regressed from them on your corpus).
**Caveat:** Essentia has **no Windows wheels** → run it in WSL2 (already working on this machine — the freqtrade project runs there) as part of the C4 worker.
**What:** batch-compare Essentia key vs MIK tags across the corpus; review only disagreements with Sam (Camelot-adjacent disagreements are low-stakes; tritone errors matter). Adopt only at ≥95% agreement-or-better-on-review.
**Validation:** agreement report + Sam's verdict on the disagreement list.
**Effort:** M.

### C3. allin1 as an ensemble vote — P2, only via WSL2
**Why:** [allin1](https://github.com/mir-aidj/all-in-one) (ISMIR 2023) does joint beat/downbeat/tempo/section boundaries+labels on demixed audio — conceptually the academic twin of your stem detector. As a second *independent* section opinion it powers the confidence-routing idea already in AI_CONTEXT (agree → auto-accept; disagree → review queue). **Verified today: it does not import on this machine** (Python 3.14 + numpy 2.4 kill its madmom dependency). Do not fight that natively — it lives in the WSL2 worker (C4) with a pinned older env, or skip it entirely if C4 feels heavy (the ensemble also works with just stems-vs-RB-phrases-vs-amplitude, which you have).
**What:** WSL2 venv (py3.11, pinned numpy<2 for madmom), wrapper that emits allin1 JSON per track; an `ensemble_sections.py` that maps allin1 labels onto your taxonomy (chorus→drop, verse→body, bridge→break — mapping needs eyeballing on 5 tracks first) and scores boundary agreement (±2 bars) vs `SECTIONS_STEM_*.json`; disagreements land in a per-mix review list.
**Validation:** ensemble report on a finished mix whose sections Sam already blessed — agreement rate becomes the baseline metric.
**Effort:** M–L.

### C4. The "Analysis Worker" boundary — the productisation shape — P2
**Why:** today analysis is smeared across desktop automation, tag reading, ANLZ parsing, librosa, Demucs. The sellable unit (and the thing that makes MIK/RB swappable at all) is one contract: `analyze(track.wav) → TrackAnalysis.json` (bpm, beat grid, downbeat, key/Camelot, LUFS, energy, sections, signals: bass in/out, loop windows, vocal regions, kick cues). The mix engine should consume *only* this JSON.
**What:** define the JSON schema (mostly exists across `SECTIONS_STEM_*.json` + hints + features cache — unify, version it `analysis_schema: 1`); refactor orchestrator inputs behind a loader; backends become pluggable (`desktop` today / `owned` = BeatThis+Essentia+stems tomorrow). WSL2 hosts the Linux-only pieces; the contract hides where each field came from. This is boundary-drawing, not new algorithms — and it's the demo a label eventually buys.
**Validation:** pipeline runs end-to-end consuming only the new JSONs (grep: no direct `mik_reader`/`rekordbox_reader` calls outside the worker).
**Effort:** L. Schedule after A-phase lands.

### C5. CUE-DETR as a cue-candidate source — P3 (research dessert)
**Why:** [CUE-DETR](https://github.com/ETH-DISCO/cue-detr) (ISMIR 2024) is a DETR fine-tuned on **EDM-CUE: 21k expert cue points across 4,710 EDM tracks** — exactly this genre, exactly this problem. It would slot into `cue_candidates.py` as one more ranked source with its own confidence (the architecture was literally built for that). The EDM-CUE dataset is also free evaluation data for your own detectors.
**Validation:** its cues vs Sam's hint history on already-hinted projects — agreement % per cue type before it ever influences a mix.
**Effort:** M–L. Pure upside, zero urgency.

---

## 6. Phase D — Close the loop: automated transition QA (highest-leverage NEW capability)

> Today, nothing *listens* to a mix until Sam renders it in Ableton. But the stem envelope caches + planned automation already contain enough to predict most failures numerically — and to bounce preview audio. This converts "Sam finds it in the car" into "the pipeline flags it before the .als is even opened". It also directly implements the saved feedback memory: *lower vol/bass across overlaps so summed energy doesn't jump/distort*.

### D1. Summed-energy check per transition — S effort, do first
For each transition: take both tracks' cached envelopes (`mix` + bass band), apply the planned volume/EQ automation gains at the planned positions, sum, and flag (a) > ~1.5 dB RMS jump at the swap, (b) sustained overlap energy above either track's solo drop level (the distortion predictor), (c) post-transition dip (the dead-air predictor). Pure numpy over `.npz` — no rendering, runs in milliseconds. Emit per-transition PASS/WARN into `ARRANGEMENT_REPORT.json` + a strip on the alignment PNGs.
**Validation:** back-test on the 08.06.26 mix — the transitions Sam marked weak should score worse than the ones he blessed (that's 9 labelled data points already in hand).

### D2. Vocal-clash + kick-alignment checks — S effort
**Vocal clash:** both tracks' `vocal_regions` are already in the stem JSONs; intersect them in arrangement space across each overlap → WARN with bar ranges. (Currently vocals are only avoided in *loop source* picking — `align_engine.pick_clean_drum_loop` — not across the overlap itself.)
**Kick alignment:** cross-correlate the two drums envelopes within the overlap at the planned offset; the correlation peak should sit at lag 0 ± tolerance. A peak at ±1 beat = the classic off-by-one-downbeat bug class (V46 era) caught automatically.
**Validation:** synthetic test — shift one track's envelope by a beat, watch the check go red.

### D3. Per-transition preview bounces — M effort, big workflow win
Offline-render each overlap window (±16 bars) to MP3: load both WAVs, time-stretch the incoming to the outgoing's BPM (librosa/`pyrubberband`; BPM deltas are ≤2 in-mix, so artifacts are negligible), apply the volume/EQ-approximation curves, sum, normalise. Output `Output/Transition Previews/T01 … .mp3`. Sam approves transitions from his phone before opening Ableton — and these previews are exactly the artifact a label client would receive in the productised service.
**Caveat to document in the output folder README:** preview ≠ Ableton render (warp engine differs, EQ is approximated). It exists to catch arrangement/energy errors, not to judge sound quality.
**Validation:** bounce one transition from an already-rendered mix and A/B against the real render — structure and levels should track within ~1 dB. (this would take longer and be a hinderance, ignore [annotaed by sam])

### D4. Wire D1/D2 into the existing gate pattern
Same shape as `validate_als`: every `propose_arrangement`/`apply_automation` run ends with the QA pass; WARNs print in the `VISUAL REVIEW REQUIRED` block and append to `REVIEW_V<N>.md`. WARN not FAIL — Sam's taste outranks the checks; the goal is attention-routing, not a hard gate.

---

## 7. Phase E — The learning layer (make corrections compound)

**Current state:** `Documentation/Mix Patterns Library/MIX_PATTERNS.md` holds 4–6 rules at N=1/N=2 confidence, hand-baked into `apply_automation.py` as code + magic numbers; `pair_history.jsonl` does BPM±2 lookups; `learn_from_correction.py` diffs proposal vs Sam's edit. Honest, but it plateaus: every new rule is a hand-written if-statement, and N=1 rules already caused false-positive waves (Rules 3/4, V23).

### E1. Structured correction records — S effort, start immediately
Every time Sam edits a generated `.als`, capture (extend `learn_from_correction.py`): transition context (BPMs, keys, energies, section shapes, overlap length, handoff kind, lineup score), Claude's choice, Sam's choice, per-parameter deltas — one JSONL row. **The schema is the asset**: at today's pace (~9 transitions/mix, multiple mixes/month) that's hundreds of expert-labelled examples within months. The 08.06.26 verdicts + V20–V24 diffs can be backfilled from artifacts already on disk.

### E2. Retrieval-first proposals (k-NN over rules) — M effort, after ~50 records
At proposal time, find the k nearest past transitions in feature space; if neighbours agree on a choice, apply with confidence = agreement; *else* fall back to the rules. Interpretable ("3 similar past transitions: Sam swapped at the outro all 3 times"), needs no training run, and converts every correction into immediate behaviour change — the actual flywheel. Keep the ≥3-observations discipline for anything that auto-applies.

### E3. (Additive sequencer idea) Vocal-density edge cost — S effort
Two vocal-heavy tracks adjacent = the hardest transitions to mix clean. The stem JSONs already carry vocal coverage %; add a small term to `_edge_cost` (`sequencer.py:192`) *below* the BPM weights so harmony still dominates. Zero risk: weights are strictly hierarchical.

---

## 8. Suggested phasing (for the Opus session backlog)

| Phase | Tasks | Theme | Effort |
|---|---|---|---|
| **A** | A1 als_io, A2 requirements, A3 cache path, A7 silent failures | Stop the bleeding | ~1 day |
| **A+** | A4 tests (align_engine first), A5 archive, A6 constants | Safety net before touching anything else | 1–2 days |
| **B** | B1 CUDA torch | 10–30× analysis speedup | 30 min + validation |
| **D** | D1 energy check, D2 clash/kick checks, D3 previews | Close the loop — pipeline hears mistakes first | 1–2 days |
| **C** | C1 Beat This probe → flag; then C4 worker boundary; C2 key; C3 ensemble | Kill the desktop-app dependency, own the stack | 1 wk spread |
| **E** | E1 records now; E2 retrieval later; E3 whenever | Corrections compound | drip |
| (B2/B3/C5) | separator upgrade, parallelism, CUE-DETR | Only on observed need | — |

Rule of thumb baked into the ordering: **hygiene before speed, speed before new analyzers, validation harness before any swap.** Every C-phase candidate gets judged against the corpus you already own, never adopted on reputation.

---

## 9. Smaller observations (fix opportunistically)

- `align_engine._mix_order` (`align_engine.py:587`) uses first-match prefix fallback — the exact pattern `_resolve_stem_key` (line 330) deliberately forbids (requires unique match). Align them.
- `align_engine.main()` calls `load_track(j)` twice per JSON (line 607).
- Stem path skips `refine_segments` while the RB path runs it (`orchestrator.py:457-502` area) — believed intentional (the detector *is* the refinement); confirm and document it in the orchestrator docstring so a future session doesn't "fix" it.
- `apply_automation` re-detects overlap zones from the `.als` rather than consuming `Alignment.swap_beats` it could be handed — fine today, but note the implicit coupling; D-phase checks should read the report JSON, not re-derive.
- Dropbox: `.tmp.7200.*` conflict files in `Mix Patterns Library/` — when A3 moves caches, prefer atomic-rename writes and keep high-churn caches out of synced folders.
- `Tests/test_*.py` import only the package; after A8 packaging, move the 2 stray smoke tests (`Source/test_mik_driver.py`, `Source/test_rb_driver.py`) into `Tests/`.
- Python 3.14 is adventurous for an audio-ML stack; if any wheel pain appears (numba, torch-cu), a pinned 3.12 venv via `uv` is the boring fix. Not urgent while everything imports.

---

## 10. Sources

- [all-in-one (allin1) — GitHub](https://github.com/mir-aidj/all-in-one) · [paper: arXiv 2307.16425](https://arxiv.org/abs/2307.16425) · [PyPI](https://pypi.org/project/allin1/)
- [Beat This — CPJKU, accurate beat tracking without DBN (ISMIR 2024)](https://github.com/CPJKU/beat_this) · [data/spectrograms](https://zenodo.org/records/13922116)
- [python-audio-separator (UVR models incl. RoFormers)](https://github.com/nomadkaraoke/python-audio-separator) · [Mel-Band RoFormer paper](https://arxiv.org/pdf/2310.01809) · [BS-RoFormer implementation](https://github.com/lucidrains/BS-RoFormer)
- [CUE-DETR — Cue Point Estimation using Object Detection (ISMIR 2024)](https://github.com/ETH-DISCO/cue-detr) · [paper: arXiv 2407.06823](https://arxiv.org/abs/2407.06823) · [M-DJCUE earlier dataset](https://github.com/MZehren/M-DJCUE)
- [Zehren et al., Automatic Detection of Cue Points for DJ Mixing](https://arxiv.org/pdf/2007.08411) · [CMJ follow-up](https://direct.mit.edu/comj/article/46/3/67/117159/Automatic-Detection-of-Cue-Points-for-the)
- [Essentia Key (edma/edmm EDM profiles)](https://essentia.upf.edu/reference/streaming_Key.html) · [Key Estimation in EDM (Faraldo et al.)](https://link.springer.com/chapter/10.1007/978-3-319-30671-1_25)
- [Dubspot 2026 lab report — MIK vs Beatport vs RB vs KeyFinder](https://blog.dubspot.com/dubspot-lab-report-mixed-in-key-vs-beatport)
- [madmom (maintenance state, numpy<1.20)](https://pypi.org/project/madmom/) · [py3.10+ compat fork](https://github.com/The-Africa-Channel/madmom-py3.10-compat)
- [demucs.onnx (C++/ORT)](https://github.com/sevagh/demucs.onnx) · [Mixxx GSoC 2025: Demucs→ONNX](https://mixxx.org/news/2025-10-27-gsoc2025-demucs-to-onnx-dhunstack/) · [HT-Demucs FT ONNX export notes](https://stemsplit.io/blog/htdemucs-ft-onnx-export)

---

*Written by Claude Fable 5, 2026-06-10. Verified against commit `946eb06` and the live env. No code was modified; the only repo changes this session are this document and an activity-log entry.*
