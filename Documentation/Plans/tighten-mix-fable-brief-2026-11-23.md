# Brief for the Fable planner: take the blend planner past "almost good enough"

Written 2026-11-23 (STUDIO-2, Claude Sonnet 5.5 for Sam). Read-only planning job: return a PLAN as text. Do not edit any file. No em dashes anywhere in your answer (Sam never wants to see one; use commas, full stops, colons or a plain hyphen).

## The question

The blend planner (`Tools/plan_mix_decisions.py` feeding `propose_arrangement.py --decisions`, default arranger since 2026-11-22) plans every transition from rules Sam and the team set. Sam says the rules work and this is the first base point that is "almost good enough". He now wants a plan to take it PAST that: tighten and consolidate the mix, and find out whether the planner plans the BEST mix or only a rule-satisfying one. He cannot tell today.

## What Sam said (2026-11-23, voice-dictated, lightly cleaned)

- Cue points were great. Transition 3 was a little boring but technically clean.
- Transition 1 had a little flamming (two kicks slightly apart, not a helicopter). Later: transitions 1 and 2 flam because the warp on both tracks is off very slightly, in opposite directions, which makes it worse.
- Hiisak - Lift Off: the warp was off, so he re-warped it by hand. That is why that track shows one "intro" clip: he did not re-section it. He thinks its sections were correct.
- He moved things around in some transitions because he thinks it is preferable. "A bunch of the mixes are actually bang on." He has not gone mega detailed. Only the first three transitions were listened to properly before the tweak.
- He judges as a punter first; picture answers are provisional until he has heard them; he prefers to judge one thing at a time in plain words.

## The facts from the files (measured by Claude, 2026-11-23)

Planned set: `Listening Sets/Tech House 2026 Planned.als` (+ `.wav`, bounced 2026-11-22). Sam's tweak: `Listening Sets/Tech House 2026 Planned Sam Tweak Project/Tech House 2026 Planned Sam Tweak.als`. Nine tracks in this order: Get Right, No Panties, Taste, TCFS, 007, POP, Lift Off, Interlude, Killa. All warp markers are one per beat from the owned grid.

Geometry (arrangement beats, 4 beats per bar):

| Transition | Planned overlap | Sam's overlap | What Sam changed |
| --- | --- | --- | --- |
| Get Right to No Panties | 32 bars | 32 | nothing |
| No Panties to Taste | 32 | 32 | nothing |
| Taste to TCFS | 32 | 32 | nothing |
| TCFS to 007 | 30 | 33.5 | TCFS ends 14 beats later; 007 intro lengthened 60 to 64 beats (everything after +4 beats) |
| 007 to POP | 24 | 32 | 007 runs 36 beats longer (extra drop_4 clip); POP enters 4 beats later |
| POP to Lift Off | 32 | 28 | POP drop_4 lengthened 32 to 64 beats, POP outro cut 96 to 48 beats; Lift Off enters 3.9 beats later |
| Lift Off to Interlude | 32 | 40 | Interlude enters 12 beats EARLIER, gains an extra 32-beat intro clip; Lift Off ends 20 beats later; Interlude last drop cut 64 to 32 beats |
| Interlude to Killa | 32 | 16 | Killa enters 52 beats LATER; Killa break_2 cut from 96 to 32 beats (16 bars removed) |

Reading of that table (a claim, not proof): Sam mostly made the mix TIGHTER (cut breaks and outros, shorter last blend) and adjusted overlap lengths by single bars, not by new ideas. The first three transitions kept the planner's geometry, but he found flamming in 1 and 2 anyway.

Warp markers: identical (0.00 ms difference at every beat) for all tracks except Lift Off. Lift Off went from 449 per-beat markers to 2 markers (a straight line at 132.000 BPM, first marker at 25.76 ms). The old per-beat markers differ from that straight line by at most 2.4 ms. A 2.4 ms difference cannot explain an audible fix, so the cause is NOT visible in the marker file alone. That is the key open question: the grid has to be compared with the real audio.

Grid wander from a straight line (planned set, per track): max 0.8 to 3.4 ms, standard deviation 0.3 to 1.2 ms. The grids are smooth. So an audible flam is not coming from the grid wobbling; it must come from a CONSTANT offset between where the grid says the beat is and where the kick actually is, with different signs on the two tracks in a blend (Sam: "opposite directions").

MEASUREMENT FROM AUDIO: see the section "Kick offset measurement" at the end (added by Claude when the measurement job returns).

## Hypotheses to test (not conclusions)

H1. The pipeline's grid gate tolerates an absolute error per track (a 15 ms gate appears in the project notes). Two tracks each within tolerance but with opposite signs can be up to twice that apart in a blend, which is a flam. The gate should judge the RELATIVE kick offset between the two tracks inside each overlap, not each track alone.
H2. A different class of error exists in tracks where the grid is smooth but offset by a constant few ms (the beat-grid step uses stems and a certified grid; its own error against real kick onsets has never been measured on the blend regions).
H3. The planner picks the first admissible placement that passes its rules and never compares alternatives, so nobody knows whether another entry bar, swap bar or overlap length would score better. Sam's tweaks (more or fewer bars of overlap, cutting breaks) are evidence of where the rules leave quality on the table.
H4. Sam's own hand-made mixes (`Teaching Mixes/`, 20 finished mixes, card library under `Documentation/Mix Patterns Library/Teaching Mixes Cards/`) and the `pair_history` learner data are the only independent ground truth about what "better" means. Prior attempts to learn from them as a formula lost to a trivial baseline (C7 step 1, 19 percent vs 68 percent), so any use must be honest about that.

## What a top professional would do (the /deeper answers Sam saw)

1. Measure kick-to-kick offset in each overlap in milliseconds instead of judging by ear, and generate several candidate blends per transition and rank them.
2. Start from raw material: sample-level kick onsets from the drum stems or wav, the bounced audio, the ALS markers, Sam's hand-made mixes and tweak history. Not the planner's own scores.
3. Methods: onset detection on the low band, cross-correlation of the two kick streams, candidate generation scored on a measured objective (kick offset, bass clash, energy continuity, loudness), comparison of planner picks with Sam's picks.
4. Outside check: a measured kick offset on the bounce under a tolerance Sam sets, and a held-out replay against Sam's hand-made mixes. Sam's ear stays the final judge, one transition at a time.

## What I want back (as text, in this order, plain words, short sentences)

1. The single most likely cause of the flamming in transitions 1 and 2, with the one measurement that would prove or disprove it, and the fix at the right layer (grid, gate, or placement). Say what you would test first and what result would change your mind.
2. A plan to answer "does the planner plan the best mix?": how to generate alternatives per transition, what to score them on (a measured objective, not taste), how to use Sam's tweaks and the 20 Teaching Mixes as held-out checks without repeating the C7 mistake, and what result would show the planner is already good enough.
3. A ranked list of at most five changes to try, each with: the exact file or tool it touches, the build owner you would pick (Codex is capped until 2026-11-27 11:56; Kimi weekly quota is spent; MiniMax and Claude subagents are available), the review pair, the test that must fail before and pass after, and the outside check.
4. What to do with Sam's tweak as data: which of his changes are one-off taste and which look like a rule the planner could learn (name each, with the bars involved).
5. Risks and what NOT to build. Be willing to say "leave this alone".

Rules for you: ground every claim in a file you read (quote the path). If you did not verify something, say "not verified". Do not invent numbers. Sam reads in small bites: lead with the single most important line.

## Files worth reading (read-only)

- `Documentation/AI_CONTEXT.md` (Current State), `Documentation/BURN_LIST.md` (the START HERE block), `Documentation/Golden Sections/`, `Documentation/Plans/`
- `Tools/plan_mix_decisions.py`, `Source/align_engine.py`, `Source/learn_from_correction.py`, `Source/apply_automation.py`
- `Documentation/Mix Patterns Library/` and `Teaching Mixes Cards/INDEX.md`
- The two ALS files named above (gzip XML; parse, do not edit)
- Memory notes (in `C:\Users\Carillon\.claude\projects\C--Users-Carillon-Wired-Masters-Dropbox-Sam-Wills-0-1---GIT-HUB----Automated-DJ-Mixes\memory\`): `fills-are-phrase-points.md`, `blind-listening-test-too-heavy.md`, `claude-arranged-mode-is-wanted.md`

## Kick offset measurement (added 2026-11-23, measured from the real audio)

Method: a Sonnet subagent measured the CLICK onset (above 150 Hz envelope, leading edge) of each kick against the ALS warp markers, in ms. Positive = the kick lands late against the grid. The first method (40 to 120 Hz envelope peak) measured the sub body instead, which lags the click by 26 to 84 ms, so it was discarded (kept in `v1/`). Sign convention proven by shifting a grid +10 ms and seeing -10.0 ms. Raw data: `D:\Claude Workspace\Automated DJ Mixes\kick-offsets\` (one JSON per track with beat, grid_sec, offset_ms; `measure_kicks2.py`; `summary_v2.json`; `diag_stacks.png`, `diag_zoom.png`). Resolution is about 1 to 3 ms (markers are rounded to 1 ms). Kick coverage is a lower bound (strict gates: 15 to 67 percent of beats).

Per track, median offset over the whole track in ms: Get Right -2.5 (drifts from -2 at the start to -6 at the end), No Panties +1.1 (but a second estimator, the steepest rise, says +10.4: its click is small and the main swell peaks about +10 ms), Taste +0.8, TCFS +1.5, 007 +0.3, POP +0.6, Lift Off -0.9 on the planned grid and -0.25 on Sam's straight line (both under 1 ms, so the re-warp changed almost nothing in click timing), Interlude -0.5, Killa +1.8.

Per transition (the lead mapped the per-beat offsets onto the clips using each clip's source-beat offset, loop_start minus start, and kept only beats inside the overlap; "same-beat both" = arrangement beats where both tracks have a kick; "out-in" = outgoing minus incoming offset, the size of the flam):

| Transition (planned set, the bounce Sam heard) | out median | in median | same-beat both | median out-in | max abs |
| --- | --- | --- | --- | --- | --- |
| Get Right to No Panties | -5.55 | +1.66 | 34 | **-7.61 ms** | 20.8 |
| No Panties to Taste | +1.10 | +1.38 | 29 | -0.42 | 29.8 |
| Taste to TCFS | +1.60 | +2.39 | 72 | -0.19 | 18.6 |
| TCFS to 007 | +1.61 | +0.29 | 22 | +1.42 | 11.1 |
| 007 to POP | +0.17 | +0.18 | 9 | +0.01 | 8.6 |
| POP to Lift Off | +0.50 | -0.93 | 2 | +1.79 | 2.3 |
| Lift Off to Interlude | -0.65 | -0.13 | 3 | +1.17 | 8.2 |
| Interlude to Killa | -0.41 | +0.02 | 32 | -0.32 | 15.1 |

Reading (claims for you to test, not proven): transition 1 is explained by the click timing: Get Right's end sits about 5.5 ms early against its grid while No Panties' start sits about 1.7 ms late, so the two kick streams are about 7.6 ms apart for the whole 32-bar overlap, which matches Sam's "off slightly in opposite directions". Get Right's own offset DRIFTS (-2 to -6 ms across the track) although its markers are a straight line within 1.3 ms, so a constant-tempo grid does not follow the real audio there. Transition 2 is NOT explained by click timing (median -0.4 ms), but No Panties' main sub swell is about +10 ms late against its grid while Taste's is about +1.5 ms, so a sub-body offset of roughly 9 ms is a candidate cause for the flam Sam heard there (not verified; Sam may number transitions differently). The max-abs column (up to 30 ms) is not yet understood: it may be detector mistakes, fills, or real off-beat kicks, so do not treat it as a flam size.

The planned bounce and the tweak share transitions 1 to 3 exactly, so the tweak did not fix transitions 1 and 2. Unknown: whether the existing grid gate (a 15 ms gate appears in the project notes, in `Source/stem_detector.py` or the Audio Analysis Toolkit) judges each track alone; a relative gate inside each overlap would have flagged transition 1 at 7.6 ms. Verify in code before relying on it.
