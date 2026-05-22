# Pipeline Audit — What Runs vs What Should Run

**Date:** 2026-05-22
**Why this doc exists:** We have built ~all the tools needed for a great mix, but several existing tools are skipped, never wired in, or hardcoded to old projects. Result: we chase our tails fixing alignment in Phase 2 when the real bug is wrong chops in Phase 1.

---

## Pipeline AS IT IS (what actually runs end-to-end)

```
PHASE 0 — Setup
  └─ orchestrator.py --previews-only
       ├─ desktop_analyzer.py (MIK + RB UI)
       ├─ MIK enrichment (key, BPM, energy)
       └─ per-track preview PNGs

PHASE 1 — Section Detection
  ├─ 1a. orchestrator.py --sections-layout
  │       └─ phrase_viz.py (chops the audio into intro/build/drop/break/fill/outro)
  │       └─ output: Sections V1.als (colour-coded clips)
  ├─ 1b. extract_sections_als.py
  │       └─ output: Sections_V1.json (source + arr beats per section)
  ├─ 1c. ❌ sections_blind_viz.py  ← HARDCODED to "Black Book x Defected V2"
  │       └─ DOES NOT RUN for any other project. Silently skipped.
  ├─ 1d. ❌ Visual pass / BLIND_VALIDATION_V1.md
  │       └─ Should read 8 PNGs per track and write a validation table.
  │       └─ NO PNGs exist (1c failed silently) → table is bluffed or skipped.
  ├─ 1e. apply_section_corrections.py
  │       └─ Only runs if 1d caught errors. Since 1d never ran, no corrections.
  └─ 1f. track_hints.json
          └─ Authored from preview PNGs (lower zoom than 1c would have).

PHASE 2 — Arrangement
  ├─ 2a. propose_arrangement.py
  │       └─ output: Sections V<N+1>.als + ARRANGEMENT_REPORT.json
  │       └─ Uses chops from Phase 1. If Phase 1 chops are wrong, this is built on sand.
  └─ 2b. Read ARRANGEMENT_REPORT.json (numerical, no audio)

PHASE 3 — Automation
  └─ 3a. apply_automation.py
          └─ output: Sections V<N+2>.als with EQ + volume curves

PHASE 4 — Final Visual Review
  ├─ 4a. ❌ Per-transition PNGs
  │       └─ /mix skill says they go in Output/Visualisations/Mix V<N>/Transition_*.png
  │       └─ TOOL DOES NOT EXIST for three-phase pipeline.
  │       └─ transition_viz.py exists but is wired to the OLD orchestrator
  │           (uses TransitionSpec from automated_dj_mixes/transition.py).
  │       └─ Three-phase output uses different data structures → no PNGs generated.
  ├─ 4b. ❌ Per-track PNGs
  │       └─ Same gap — no tool re-runs section viz on the final output.
  └─ 4c. REVIEW_V<N>.md
          └─ Falls back to "numerical verification, no PNGs available."

PHASE 5 — Report
  └─ Hand off to Sam. He listens, finds the bugs we couldn't see because
     nobody ever looked at the audio.
```

---

## Pipeline AS IT SHOULD BE

```
PHASE 0 — Setup (unchanged)

PHASE 1 — Section Detection
  ├─ 1a. orchestrator.py --sections-layout                                   ✓
  ├─ 1b. extract_sections_als.py                                             ✓
  ├─ 1c. ✅ section_viz.py  (PROJECT-AGNOSTIC, takes path as arg)
  │       └─ 8 PNGs per track showing waveform + section bands + chop lines
  │       └─ output: <project>/Sections Review/Blind_V<N>/<Track>_Q1..Q8.png
  ├─ 1d. ✅ Claude reads every PNG → BLIND_VALIDATION_V<N>.md
  │       └─ Per-chop row with specific bar + observation
  │       └─ MUST BLOCK proceed to Phase 2 if any chops wrong
  ├─ 1e. apply_section_corrections.py (run if 1d flags errors)
  └─ 1f. track_hints.json

PHASE 2 — Arrangement
  ├─ 2a. propose_arrangement.py
  └─ 2b. Numerical review of ARRANGEMENT_REPORT.json

PHASE 3 — Automation
  └─ 3a. apply_automation.py

PHASE 4 — Final Visual Review
  ├─ 4a. ✅ transition_review_viz.py  (built this session — needs wiring in)
  │       └─ 1 PNG per transition: overlap zone + section bands +
  │          outro-start line + incoming-rise line + automation curves
  │       └─ output: <project>/Output/Visualisations/Transitions_V<N>/T01..T09.png
  ├─ 4b. ✅ Re-run section_viz.py on V<N+2>
  │       └─ Same tool as 1c. Confirms chops haven't shifted in Phase 2/3.
  │       └─ output: <project>/Output/Visualisations/Tracks_V<N+2>/<Track>_Q1..Q8.png
  └─ 4c. REVIEW_V<N>.md — table of per-transition AND per-chop verdicts

PHASE 5 — Report
```

---

## The Holes (where IT IS diverges from IT SHOULD BE)

| # | Hole | Tool that exists | What's missing |
|---|------|------------------|----------------|
| 1 | Phase 1c skipped | `sections_blind_viz.py` exists at 8 quarters with chop lines | Hardcoded `base = Path("Test Project/Black Book x Defected V2")`. Needs CLI args. |
| 2 | Phase 1d skipped | `BLIND_VALIDATION_V<N>.md` format defined in /mix skill | No enforcement — Claude can proceed without doing it. |
| 3 | Phase 4a skipped | `transition_viz.py` exists for orchestrator pipeline only | Three-phase output uses different data structures. Built `transition_review_viz.py` this session — needs to be wired into the skill. |
| 4 | Phase 4b skipped | Same tool as 1c | Same fix as Hole 1 — un-hardcode. |
| 5 | No "block if chops wrong" gate | — | A validation report could exit non-zero if any row has `⚠ off N` — would stop the pipeline cold. |

---

## Why we keep chasing our tails

When Sam says "the chops are in the wrong place," the right response is:
1. Look at `Blind_V<N>/<Track>_Q1..Q8.png`
2. Find the wrong chop
3. Apply correction via `apply_section_corrections.py`
4. Re-run Phase 2 + Phase 3

But because Phase 1c is silently skipped, those PNGs don't exist. So instead I:
1. Run Phase 2 logic changes (alignment formula)
2. Run Phase 3 logic changes (automation timing)
3. Generate new output
4. Sam reviews, says chops still wrong
5. Repeat

We are tuning alignment on top of broken chops. The fix is: NEVER skip Phase 1c/1d.

---

## What needs to happen now (in this project)

1. Make `sections_blind_viz.py` project-agnostic (CLI args, no hardcoded path).
2. Run it on Sections_V12.json (current state) → produce 8-quarter PNGs.
3. Look at every PNG. Find every wrong chop. Build a BLIND_VALIDATION_V12.md.
4. Apply corrections via `apply_section_corrections.py`.
5. Re-run Phase 2 (V14) and Phase 3 (V15) on the CORRECTED sections.
6. Add `transition_review_viz.py` to Phase 4 of the /mix skill so this never happens again.
7. Add `section_viz.py` re-run to Phase 4 so post-arrangement chops are re-verified.

The tools exist. They just aren't wired into the steps. That's the work.
