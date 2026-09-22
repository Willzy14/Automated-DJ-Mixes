# Render Check - 22.09.26 Tech House Core Sample (2026-09-22)

**Brain:** Claude
**Subject:** `Test Project/22.09.26 Tech House Core Sample/Output/Sections V3.wav` (694 MB, 44.1kHz/2ch,
45:52, -15.26 LUFS integrated) - Sam's bounce of an 11-track Tech House cross-section built to
validate today's D9 (rescue-signal defaults) and D4 (key-fabrication fix) on real audio, and to
stress-test the D5 `--write-hints` fix found live during this same build (see `BURN_LIST.md` D5).
Full build notes: `Test Project/22.09.26 Tech House Core Sample/Output/Visualisations/REVIEW_V3.md`
(gitignored, project-local).

## TL;DR

**Clean render.** `render_check.py` verdict: **WARN, exit 1** - per the gate's own contract that
means musical judgment calls for Sam's ear, not objective defects. 0 FAIL findings. 7 checks ran
clean outright (`eof_truncated_reads`, `exposed_solo`, `hard_silence`, `level_cliff`,
`loop_exit_jump`, `loop_hole`, `loop_period`). Full findings: `RENDER_CHECK_V3.md` (project-local,
gitignored - summarised in full below since that file itself isn't committed).

**Sam's ear-note, while bouncing:** "you haven't extended some of the outros to the next cue
point... is that an oversight?" - investigated against the ALS and the source stem-analysis data
directly (not by ear, since he hadn't listened yet). **Not an oversight.** Checked all 10
transitions: every single outgoing track's last clip runs bar-for-bar to that track's own true
last bar - zero unused source bars anywhere. Detail below.

---

## Render check findings (6 total, 0 FAIL)

| # | Time | Check | Level | Note |
|---|------|-------|-------|------|
| 1 | 0:00.0 | map_vs_render | INFO | endpoint consistent, +0.08s vs the ALS-derived length |
| 2 | 0:30-45:13 (102 boundaries) | boundary_click_skipped_tempo_arc | SKIP | expected - click-window check doesn't characterise timing uncertainty on a tempo arc |
| 3 | 5:13-5:24 | loop_verbatim_under_automation | INFO | HARTY tail loop plays under transition automation - verbatim check n/a |
| 4 | 6:05-39:28 | grid_fold | WARN | 61.0 ms drift measured; gate states this is report-only on a tempo arc (uncertainty not characterised, threshold can't gate) - not actionable as stated |
| 5 | 9:10-9:54 | loop_verbatim_under_automation | INFO | Jones tail loop, same as #3 |
| 6 | 36:14-36:29 | transition_dip | WARN | T10 (Shilla -> Yellody, pair_index 9): 2.5 dB dip vs baseline (-14.9 LUFS), deficit in the mid band, flagged UNBRACKETED (measured minimum sits on the edge of the search window - real trough may be lower/elsewhere) |

**Recommendation:** T10's transition (~36:14-36:29) is the one worth Sam's ear. Everything else is
clean or informational.

## The outro-extension question - investigated, not an oversight

Method: extracted every outgoing track's LAST arrangement clip from the ALS (`Loop/LoopStart` /
`LoopEnd`, in source beats) and compared its end bar against that same track's own `n_bars` from
its `SECTIONS_STEM_*.json` (the true, detected length of the source file).

**Result: 0 unused bars on all 10 transitions.**

| Transition | Outgoing track | Last clip (source bars) | Track's true n_bars | Unused bars | `loop_source` |
|---|---|---|---|---|---|
| T1 | HARTY - There's A Party Going On | 168-186 | 186 | 0 | outro (8bx3 tail loop added - native outro alone was short) |
| T2 | Jones - Shuffle | 144-160 | 160 | 0 | outro (32bx3 tail loop added) |
| T3 | Detlef - HighRoller | 153-164 | 164 | 0 | none - native outro sufficient |
| T4 | Enzo Is Burning - All The Ladies | 120-136 | 136 | 0 | none |
| T5 | Jewel Kid - Talking to You | 165-173 | 173 | 0 | none (8-bar native outro - short) |
| T6 | Zaro - Pach (Radio Edit) | 100-132 | 132 | 0 | none |
| T7 | Freejak - Know You Better | 148-165 | 165 | 0 | none |
| T8 | RSquared - Ooo La La | 191-200 | 200 | 0 | none (9-bar native outro - short) |
| T9 | TCTS ft. SOMA - Slippin | 136-152 | 152 | 0 | none |
| T10 | Shilla - Rewind | 168-176 | 176 | 0 | none (8-bar native outro - short) |

Every outgoing track also has 5-16 total arrangement clips covering its run through the
transition zone (drops/breaks/fills chopped from the same source file, not one lonely clip) - the
short "outro" Sam saw in Ableton is the LAST of several clips, and it's short because that
specific track's own detected outro *section* is short (8-18 bars depending on the track), not
because the pipeline stopped before the file ran out. Two tracks (HARTY, Jones) needed an
algorithmic tail-loop extension on top of their native outro to cover the planned overlap; the
other eight didn't - their native pre-outro material (drops/breaks/fills) already filled the
overlap window before the short final outro clip.

**Worth noting:** three of the shortest native outros (Jewel Kid 8b, RSquared 9b, Shilla 8b) sit
on transitions T5, T8, T10 - and T10 is the one render-check flagged with the 2.5 dB dip. Plausible
(not confirmed) that a short native outro leaves less material for the automation envelope to
smooth over. Worth listening to T5 and T8 as well as T10 with this in mind, even though only T10
triggered a WARN.

## Process note for next time

Answering "is this outro short on purpose" required a one-off script cross-referencing the ALS
against `SECTIONS_STEM_*.json` - useful enough that it's worth turning into a permanent per-
transition field (e.g. `outgoing_unused_bars`) in `ARRANGEMENT_REPORT.json` or the `REVIEW_V<N>.md`
generation step, so this question is answered automatically in future review docs instead of
needing a bespoke investigation each time a track's tail looks short in Ableton.
