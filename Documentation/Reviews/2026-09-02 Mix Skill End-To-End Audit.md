# 2026-09-02 — /mix Skill End-To-End Audit (02.09.26 House 10)

**Brain:** Claude
**Context:** Sam asked for a live audit of the whole `/mix` pipeline while building a 10-track House mix
selected via the samwillsmixing Neon catalog (`credits` table, genre column). Every bottleneck, pinch
point, or "could be better" observed during the run gets logged here as it happens, for a post-mix fix
session.

**Project:** `Test Project/02.09.26 House 10/` — 10 extended masters, all Neon-tagged `House`,
all sourced from `1. Stereo Masters/*/MASTER RENDERS/` (copied, never moved).

---

## Selection phase (Neon → disk) — before /mix proper

- **S1. Neon genre selection works, but only ~half the catalog is tagged.** `credits` has 6,868 rows
  with `genre IS NULL` vs ~6,200 tagged. House=1,812, Tech House=752, Deep House=470. Selection quality
  is capped by enrichment coverage. If this becomes the standard selection route, the null half needs
  enriching (it's the enrichment pipeline's existing backlog, not new work).
- **S2. No link from Neon rows to disk audio.** The `credits` table has no file path / "audio available"
  column. Matching DB rows to `1. Stereo Masters` folders required fuzzy artist+title matching against
  folder names (~20 matched of 1,812 House rows — most House credits have no local master folder on C:).
  A `local_audio_path` (or at least a nightly "exists on disk" flag) column would make "find ten tracks
  from <label/genre>" a single SQL query. Release-date → backup-drive-year routing (Sam's suggestion)
  is the fallback for rows whose audio is only on F:/G:.
- **S3. PowerShell `[label]` folder names break naive scanning.** Every work-folder path contains
  `[Client]`, which PowerShell's `-Path` parses as a wildcard character class — per-folder scans
  silently return empty. Everything must use `-LiteralPath`. Cost ~10 minutes of confusion; any future
  selection tooling must bake this in.
- **S4. Version-picking inside MASTER RENDERS is judgment, not code.** Choosing "the" master per track
  (Extended over Radio, AMENDED over plain, UPDATE-dated over not, MASTER over ADM) was manual. The
  versioning ladder is documented in CLAUDE.md but not implemented anywhere as a reusable resolver.

## Phase 0b — MIK metadata + previews

- **P0-1. Legacy masters are already in the MIK DB — no app drive needed.** 10/10 keys came from the
  DB read; MIK never took focus. Nice surprise: for catalog-sourced mixes the "MIK takes focus" warning
  in the skill mostly won't apply. 0/10 tracks had MIK auto-cues though — cue enrichment absent for
  legacy tracks.
- **P0-2. BPM at this stage is librosa lattice noise presented as fact.** All 10 tracks logged
  `WARNING: BPM detected by librosa`; four unrelated tracks all "detected" at exactly 129.2 (the
  lattice value). Downstream the stem grid owns BPM, but the console prints these as `129.2 BPM` next
  to real keys/LUFS with equal confidence. Cosmetic but misleading; and it's the same neighbourhood as
  the KNOWN pending bug "t.bpm comes only from the MIK database — fresh projects die on --tempo-arc".
  Watching for that at Phase 2c.
- **P0-3. Phase 0b runtime fine:** ~3 min for 10 tracks including preview PNGs.

## Phase 1a — sections + grids + kick model

- **P1-1. Runtime: ~15.5 min for 10 tracks** (12:26→12:42, GPU Demucs + grids + Kick V3 + layout).
  Matches the carded SPEED item's trajectory (22 min / 15 tracks). Not new, but now has a second
  datapoint: ~90 s/track.
- **P1-2. Quality: clean sweep.** 10/10 owned grids ≤6.5 ms on kicks (one LOWC pass at 3.0 ms),
  no JIT, no exclusions, zero chop corrections needed on the DETECT scan. The detector is genuinely
  reliable on Defected-style house — this is what "validated on clean 4-to-floor house" looks like.
- **P1-3. Output layout drift vs the skill doc.** The skill says Phase 1a writes
  `Output/Sections V<N> Project/Sections V<N>.als`; the orchestrator actually wrote `Output/Sections
  V1.als` flat. Every downstream command in the skill doc carries the `Sections V<N> Project/` path —
  each had to be hand-corrected during the run. Either fix the orchestrator or the skill doc.
- **P1-4. `extract_sections_als` still names exactly-V1 extracts `V1_baseline.json`** (needs a manual
  copy to `Sections_V1.json` for the gate) — known since 2026-06-11, still unfixed, still costs a
  confused minute every fresh project.

## Phases 1b–1f — extraction, DETECT scan, hints

- **P1-5. REAL BUG, fixed + tested: `validate_hints_vs_sections.py` silently skipped every track
  with `&` in its name.** Sections JSON keys carry ALS XML escapes (`&amp;`); the hint lookup missed,
  `continue` swallowed it, and 12 of 40 checks vanished with no trace — the 2026-06-11 "zero-checks"
  guard only catches ALL-skipped, not SOME-skipped. Three of ten tracks (Andrea Oliva & Bensy,
  RUZE & Chesster, Deetron & Seth Troxler) were unvalidated. Fixed (html.unescape + unmatched-hint
  hard error), pinned in new `Tests/test_validate_hints_vs_sections.py` (a HARD gate had zero test
  coverage). The same escaping class bit `transition_review_viz` in June — worth a one-off sweep for
  other consumers of ALS-derived names.
- **P1-6. Fixed + tested: hint semantics vs drums-only early "drops".** Two tracks (RUZE, Crvvcks)
  open with full-energy drums-only sections labelled `drop` with zero bass; the meaningful first drop
  for bass-swap targeting is where bass lands (bars 64/32). The validator anchored to the first
  labelled drop and errored on deliberate later-drop hints. Now: nearest-drop anchoring +
  first_break anchored to the same drop the first_drop hint chose. Possible upstream question for a
  future session: should the DETECTOR label a no-bass full-drums section `build` instead of `drop`?
- **P1-7. Hint authoring is the least-assisted step.** Reading 4 timestamps × 10 tracks off DETECT
  pictures by eye took ~20 min and produced 2 misreads (both caught by the gate — it earns its keep).
  `hints_from_stem_result` (auto-derivation) exists per the 2026-08-27 extraction audit (O3: "invoke
  existing hint derivation from /mix") — wiring it in and having the human/agent only ADJUST would
  cut both the time and the misread class.

## Phase 2 — arrangement + tempo arc + MixPlan

- **P2-1. The pending `t.bpm`/`--tempo-arc` card did NOT reproduce** — these legacy masters were
  already in the MIK DB (keys 10/10 from DB), so `t.bpm` was populated. The card's scope narrows to
  tracks never analysed by MIK. Still worth fixing (stem grid knows the BPM), but catalog-sourced
  mixes dodge it because Sam's mastering workflow feeds MIK.
- **P2-2. Phase 2 runtime ~3 min, clean first pass:** 9/9 aligned, 5 tail loops, 1 intro trim,
  tempo arc solved (held 124.5→130.5→126.7 from natives 120→138), MixPlan frozen, ALS gate green.

## Phase 3 — automation (+ BASS_RESIDUAL=1 A/B)

- **P3-1. REAL BUG, fixed + tested: the swap-clamp end margin was style-blind.** T8's outgoing
  (Crusy "Kids") ends ONE BAR after its final drop — align_engine correctly picked
  drop:end/outro:start as the swap cue (4 beats before track end), and `plan_transitions`' flat
  8-beat fade margin hard-errored the whole phase. But a <24-bar overlap always selects QUICK_SWAP,
  which silences the outgoing AT the swap — there is no post-swap fade to protect. Margin is now
  4 beats (one bar) for quick-swap-width overlaps, 8 otherwise. Codex round-2 pins updated (their
  _tight_pair geometry widened to stay in fade territory — refusal behavior unchanged) + 2 new pins.
  **Class insight: cold-ending tracks (1-5 bar outros — 3 of 10 in this pool!) will keep stressing
  end-of-track edge cases.**
- **P3-2. BASS_RESIDUAL=1 A/B: zero firings on 9 transitions** (2 quick-swap-excluded, 1 refused by
  the cross-band guard on a real 3.7 dB sub hole, 6 no-hole). Consistent with the 4/79 qualifying
  rate from calibration. A/B on THIS mix is a no-op — the residual needs a mix with a genuinely
  bass-light incoming to prove itself. Note for the flag-flip decision: no evidence gathered either
  way today; the guard behaved exactly as designed.

## Phase 4/5 — visual review, render gate, report

- **P4-1. REAL BUG, fixed: `loop_review_viz` crashed on Windows** building `Loops_V?` (literal `?`
  is an invalid path char) when the report is the unversioned `ARRANGEMENT_REPORT.json` that Phase
  2c's own `--report` convention produces. Falls back to `V0` now. The skill's two conventions
  (versioned vs unversioned report names) disagree — worth unifying.
- **P4-2. BIG ONE — FOUND, ROOT-CAUSED, AND FIXED SAME-DAY: `transition_review_viz` drew tracks
  END-TO-END.** All 9 FULL/ZOOM views claimed "overlap 0 beats" and placed the incoming after the
  outgoing's last clip (RUZE drawn at 1408; the ALS has it at 1188 with a 76-beat overlap). Root
  cause: the viz took geometry from the SECTIONS JSON — which is extracted from the Phase-1 layout
  where tracks genuinely sit end-to-end — and never saw Phase 2's shifts or loop clips. The June
  backlog items ("can't render looped tails", "short-overlap FULL compression") were symptoms of
  the same stale-geometry read. **Fix:** the viz now auto-locates the arranged `Sections V<N>.als`
  (or takes `--als`) and re-derives every record via `parse_sections_als` — true per-clip
  arr_time/arr_end including loop clips (stamped as separate AudioClips, so per-record linear
  source mapping stays valid) and front-trimmed intros; JSON geometry remains only as a loudly
  warned fallback. Verified: all 9 regenerated FULL views match the report and the raw clip scan
  beat-for-beat (T1 64-bar loop-built overlap renders real repeated audio; T2 76 beats at
  1188-1264; T8's cold-ender swap on Kids' final bar visible). Mid-run geometry was proven by the
  75-check MixPlan reconciliation plus an independent raw clip-span scan (two artifacts, neither
  written by the code under test) — that belt-and-braces pattern is worth keeping even now the viz
  is honest.
- **P4-3. Loop PNGs fine** (5/5 rendered, quality 0.58-0.64, all clean) — three listen-flags
  recorded in REVIEW_V3.md (1-bar cell ×7; early-drop-sourced tail; fade-edge region).
- **P4-4. Render gate RAN on Sam's bounce (2976s, -17.2 LUFS) and its FAIL decomposed into two
  gate-blindness classes, both fixed + pinned same day:**
  - *loop_verbatim under automation:* all 4 FAILs were tail loops inside their transition's
    post-swap automation span (a loop under a volume ramp cannot correlate verbatim by design);
    pre-swap pairs on the same loops read r 0.94-0.97. Fix: pair-level exclusion past the swap
    beat (`_verbatim_gated_pairs`), all-excluded loops report INFO `loop_verbatim_under_automation`.
  - *source-faithful silence:* both hard_silence FAILs mapped to the tracks' OWN near-silent audio
    (Deetron's written-in stop, -62.7 dBFS source floor; Demarkus' end tail, -74.2). Fix:
    `reclassify_source_faithful_silence` consults the source WAV at the mapped position and
    downgrades to INFO; fails closed on any doubt.
  - Re-run verdict: **WARN (exit 1)** — the honest state. Remaining ears-items: T3's momentary
    5.5 dB sub dip (the exact spot the bass-residual guard refused to fill — direct evidence for
    Sam's option-2 case on a future bass-light pairing), T4's +6.5 dB loop-exit rewind splice,
    4 musical exposed-solos, and the uncharacterised tempo-arc drift report.

## Burn round (same day, Sam's directive: "use it as a burn list, spin up the team")

| Burn item | Owner | Status |
|---|---|---|
| B1 learn_from_correction reads Phase-1 sequential geometry — vacuous 0-transition pass on Sam's first real tweak set | Codex (worktree `burn/learner-geometry`) | **work complete in-worktree** (WIP commit `80087b2`; acceptance-verified 9/9 transitions found, was 0) — Codex's covering note died to an upstream 503, transport failure not a work failure; not yet merged, pending review |
| B2 render_check loop_verbatim automation-blindness | Claude | **DONE + pinned (6 tests)**, committed `32efa36` |
| B3 render_check source-faithful silence | Claude | **DONE + pinned**, committed `32efa36` |
| B4 extract_sections_als V1_baseline naming | MiniMax (worktree `burn/mechanical-cluster`) | **DONE**, commit `e219c4d` — my review passed |
| B6 librosa-BPM console honesty | MiniMax | **DONE**, commit `3b1873b` |
| B7 '&amp;' escape sweep across ALS-name consumers | MiniMax | **DONE**, commit `445a27b` — 33-row audit, no broken crossings found |
| B10 Sam-tweaks lessons distilled | Claude | **DONE** — `Documentation/Mix Patterns Library/02.09.26 House 10 Sam Tweaks.md`; learner rerun pending merge of B1; Crusy grid change still uninspected |
| Team tooling: three Home-PC hardcoded paths in run_seat/queue_runner killed the first launch silently | Claude | **FIXED** (USERPROFILE resolution) + ledger + Known Workarounds — **reverted by a concurrent Home-PC edit and re-fixed a second time** (see ledger, same day) |
| Reviews of all three burn diffs (MiniMax r1 on the render-gate diff; Fable subagent r2 on all three) | MiniMax + Claude subagent | **paused mid-run at Sam's request** — not yet relaunched |

---

## Fix-list candidates (running tally)

| # | Item | Phase | Severity | Status |
|---|---|---|---|---|
| 1 | `local_audio_path`/availability column (or view) in Neon `credits` | Selection | Medium — unlocks one-query selection | open — **handed to Sam as a prompt for the samwillsmixing agent** (out of this repo's scope) |
| 2 | Genre enrichment backlog: 6,868 untagged rows | Selection | Medium | open — not this project's work (existing enrichment backlog) |
| 3 | Reusable "latest master" version resolver (Extended>Radio, AMENDED>plain, etc.) | Selection | Low | open — no owner yet |
| 4 | Console prints librosa-lattice BPM as fact in Phase 0b | 0b | Low — cosmetic | **FIXED** (MiniMax, `3b1873b`) |
| 5 | Hint gate dropped `&`-named tracks silently | 1f.5 | High | **FIXED + tested** |
| 6 | Hint gate first_drop/first_break anchoring vs drums-only drops | 1f.5 | Medium | **FIXED + tested** |
| 7 | Style-blind swap end margin blocks cold-ending outgoings | 3a | High | **FIXED + tested** |
| 8 | `loop_review_viz` `V?` Windows crash | 4b | Medium | **FIXED** |
| 9 | `transition_review_viz` end-to-end placement — Phase 4a gate unusable | 4a | High | **FIXED + verified on all 9 views** |
| 10 | Skill doc vs orchestrator output layout (`Sections V<N> Project/` vs flat) | 1a | Medium — every command needs hand-editing | **FIXED** (Claude subagent, both brains, content-identical) |
| 11 | `V1_baseline.json` naming quirk (since June) | 1b | Low | **FIXED** (MiniMax, `e219c4d`, dual-write) |
| 12 | Wire `hints_from_stem_result` into /mix so hints are derived then adjusted | 1d/1f | Medium — kills the misread class | open — **highest-leverage remaining item, no owner yet** (matches 2026-08-27 audit O3) |
| 13 | Detector labels no-bass full-drums openings as `drop` | 1a | Low — question for Sam | open — needs Sam's call, not a build item |
| 14 | Sweep other consumers of ALS-derived names for `&amp;` handling | all | Medium | **FIXED** (MiniMax, `445a27b` — 33-row audit, all boundary crossings already handled; correctly refused to "fix" two escaped-consistent comparisons) |

**Priority order for what's genuinely still open** (added 2026-09-02, second pass — the severity tags above didn't previously carry an explicit rank):
1. **#12** (wire `hints_from_stem_result`) — highest real leverage of anything left; kills a whole misread class every future project pays for. No owner assigned yet.
2. **#3** (version resolver) — low severity but cheap, mechanical, MiniMax-shaped; worth picking up opportunistically.
3. **#13** — blocked on Sam, not a dispatch decision.
4. **#1, #2** — both out of this repo's scope (external Neon-schema work / existing enrichment backlog); tracked here for visibility only, not burn-list work.
