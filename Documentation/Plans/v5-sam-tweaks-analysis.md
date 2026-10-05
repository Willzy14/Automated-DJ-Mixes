# V5 vs Sam's Tweaks: outgoing-loop simplification analysis (2026-10-05)

**Brain:** Claude (read-only analyst). Inputs: `Output/Sections V5.als` (baseline, post-D15),
`Output/Sections V5 Sams Tweaks Project/Sections V5 Sams Tweaks.als` (Sam), both parsed directly;
`Receipts/2026-10-05/v5_vs_sam_diff.json`; `Output/ARRANGEMENT_REPORT.json`; the pipeline's own
`_Stem Analysis/SECTIONS_STEM_*.json` (what `align_engine` reads). Bars below are **source bars of
the named track** unless marked "IN" (incoming's bars). "Natural end E" = where the outgoing would
end, in IN bars, with no tail loop (`o.n_bars - arr_offset_bars`).

**Everything here is n=10 transitions from one mix.** Rules below are fitted to these 10 and are
in-sample until replayed on a held-out correction (section 5 gives the held-out result we could
already compute: it's weak).

## 1. One-screen summary

| T | Pair | V5 -> Sam overlap | Sam's move | Class | Rule that reproduces it |
|---|---|---|---|---|---|
| 1 | HARTY -> Jones | 44 -> 46 | Kept HARTY tail loop (2-bar chunk), +1 rep (10 -> 12 bars) | c | R3 (reach 10 <= 12); +2 bars needs R6 |
| 2 | Jones -> Detlef | 56 -> 32 | Removed 24-bar Jones loop; Detlef enters 8 bars earlier; Jones outro trimmed 8 bars | a | R1 (E=32 is already Detlef drop_2) |
| 3 | Detlef -> Enzo | 32 -> 32 | Removed 5-bar loop; moved Enzo 5 bars earlier so swap = Detlef drop_8 start (148, not the off-grid outro at 153) | d (re-anchor) | R2 |
| 4 | Enzo -> Jewel Kid | 24 -> 39 | ADDED Jewel Kid intro loop (8 bars x2, enters at Enzo's last-drop start 104) AND ADDED an Enzo own-last-4-bars tail (~7.4 bars, fade ends at JK bar 32) | b + d | R4 (no-outro tail) + R5 (16-bar pre-swap) |
| 5 | Jewel Kid -> Zaro | 52 -> 37 | Removed 8-bar JK loop; Zaro trimmed 4.5 bars; swap ~2.5 bars later; JK runs only 5.7 bars past swap | a | none (R3 predicts a 4-bar loop) |
| 6 | Zaro -> Freejak | 48 -> 64 | ADDED Freejak intro loop (16 bars x2); entry moved to Zaro bar 68 = start of Zaro's 32-bar kick dropout | b | R5b (entry not inside an outgoing kick dropout) |
| 7 | Freejak -> RSquared | 48 -> 32 | Removed 15-bar loop; cut Freejak at bar 156.5 (its last kick, dropout 157-165); staggered swap (RSq rise at Freejak 141, kill at 148) | a | R3 cap (15 > 12) |
| 8 | RSquared -> TCTS | 40 -> 32 | No loop either side; moved TCTS 8 bars LATER so swap = RSq outro start 184 and RSq's natural end = TCTS break_1 | d (re-anchor) | R2 |
| 9 | TCTS -> Shilla | 40 -> 33 | Removed 8-bar TCTS loop; TCTS plays to its natural end = Shilla break_1 | a | R1 (E=32 is already Shilla break_1) |
| 10 | Shilla -> Yellody | 32 -> 32 | Kept Shilla 4-bar x2 tail loop to Yellody break_1 | c | R3 (reach 8) |

Classes: (a) removed an unneeded outgoing loop and accepted a shorter overlap; (b) loop on the
incoming intro instead; (c) kept; (d) other. None of the 10 is a literal "swap the loop from
outgoing to incoming" on the same transition; the incoming-intro loops (T4, T6) were added where
V5 had **no** outgoing loop.

**Top findings**
1. **D15's candidate filter forces a loop even when the outgoing already ends on a section line.**
   `_outro_section_target_candidates` keeps only boundaries `>= natural_end + 2`
   (`align_engine.py:2232`), so when E sits exactly on a boundary the loop runs on to the *next*
   one. That is T2 (E=32 = Detlef drop_2, looped 24 bars to break_2) and T9 (E=32 = Shilla break_1,
   looped 8 bars to drop_2); Sam deleted both. A read-only replay of the 14.08.26 corpus shows this
   case is **58 of D15's 215 loops (27%)**.
2. **Sam's blend shape is "16 in, 16 out".** Pre-swap (entry to swap) in Sam's file:
   16,16,16,16,31.5,32,16,16,16,16. Post-swap (swap to outgoing end): 30,16,16,23.4,5.7,32,16,16,17,16.
   Overlap 32-33 bars on 6/10. The August correction agrees on post-swap (median 16; both 48-bar
   runways cut to 15-18).
3. **Where the gap was small, Sam moved the incoming rather than loop (T3, T8)**: he slid the
   incoming by 5 / 8 bars onto a neighbouring outgoing section start so the outgoing's own ending
   landed on the incoming's next section. `plan_fill_or_cut` cannot do this by contract (never alters
   `arr_offset_bars`/`swap_beats`); it has to live in the aligner.

Corrections to the brief: (i) the "HARTY intro loop" is an **outgoing tail loop whose source is
HARTY's intro** (clips named `outro_1_tail_loop` play source beats 0-8 = bars 1-2; the
`pick_cue_bounded_drum_loop` fallback-to-intro case its own comment at `:2057-2062` warns about).
(ii) V5 had 7 outgoing loops, not 5: the diff JSON's repeat-group detector misses partial repeats
(Detlef 4+1 bars, Jewel Kid 4+1+3 bars) and missed Sam's added Enzo tail in T4. (iii) The ALS clip
names do not match `SECTIONS_STEM` boundaries for several tracks (e.g. RSquared outro 191 in clip
names vs 184 in SECTIONS_STEM, Zaro's whole layout); all rule work below uses SECTIONS_STEM.

## 2. Per-transition detail (outgoing-source bars; IN = incoming bars)

| T | V5: entry / swap / out end | Sam: entry / swap / out end | Out loops V5 -> Sam | In loops V5 -> Sam |
|---|---|---|---|---|
| 1 | HARTY 152 (drop_5) / 168 (outro, kickless 168-186) / IN 44 = break_1 | same / same / IN 46 | intro-drums 2b x5 -> x6 | none |
| 2 | Jones 128 / 136 = Detlef drop_1 (IN 8) / IN 56 = break_2 | Jones 120 / 136 = IN 16 (drop_1+8) / IN 32 = drop_2 | 8b x3 (src 152-160) -> none; Sam also skips Jones 144-152 | none |
| 3 | Detlef 137 / 153 (outro start, off 8-bar grid) / IN 32 | Detlef 132 (drop_7) / 148 (drop_8, end of 140.75-148 kick dropout) / IN 32 | 4b+1b (src 156-160) -> none | none |
| 4 | Enzo 112 / 120 = JK drop_1 / IN 24 (natural) | Enzo 104 (last drop start) / 120 / ~IN 31.4, vol 0 at IN 32 (fill_1) | none -> own last 4 bars x~1.9 | none -> JK intro 8b x2 |
| 5 | JK 129 / 165 (inside loop) / IN 52 | JK 136 / 167.5 / IN ~41.7 | 4b x2 (src 161-165) -> none | none (Zaro head trimmed 4.5 bars) |
| 6 | Zaro 84 (mid kick dropout 68-100) / 100 = Freejak drop_1 / IN 48 = drop_2 | Zaro 68 / 100 / IN 48 | none (D15 target abandoned: quality gate) | none -> Freejak intro 16b x2 |
| 7 | Freejak 132 / 148 (in loop) / IN 48 = break_1 | Freejak 125 / rise 141, kill 148 / IN ~32 | 4b x3+3b -> none; Freejak cut at its last kick (156.5) | none |
| 8 | RSq 160 / 176 / IN 40 (natural) | RSq 168 / 184 (outro start, kickless 184-200) / IN 32 = break_1 | none (abandoned) -> none | none |
| 9 | TCTS 120 / 136 (in loop) / IN 40 = drop_2 | TCTS 120 / 136 / IN 32.9 = break_1 | 2b x4 (src 149-151) -> none | none |
| 10 | Shilla 152 / 168 / IN 32 = break_1 | same / same / same | 4b x2 (src 169-173) kept | none |

Automation: Sam moved envelopes with clips; swap kept at the same outgoing-source bar on T1, T2,
T4, T6, T9, T10; moved on T3 (153 -> 148), T5 (165 -> 167.5), T7 (rise 148 -> 141, kill kept at
148), T8 (176 -> 184). T5's 167.5 and Zaro's 4.5-bar trim are both half a bar off our JK grid
(JK `n_bars` 173.23 is non-integer) - possibly a JK grid-phase issue; not investigated.

## 3. How the pipeline decides today (production = landmark mode)

- `plan_fill_or_cut` (`align_engine.py:2252`) never moves entry/swap. Branch (1) last-drop incoming
  intro loop is gated by `CUE_CONFIG.incoming_intro_loop` (default **False**, `:199`, `:2318`);
  branch (1a) entry extension needs `policy.extend_incoming_entry` (only `SAM_V1`,
  `transition_policy.py:155`). In production neither fires, so incoming loops never happen.
- Branch (3) outgoing tail: fires whenever the outgoing has an `outro` section (`:2416`, `:2431`).
  Targets = incoming section starts `>= E+2` (`:2232`), then Kick-Detector landmarks (`:2247`), tried
  in that order (D15/D1, `:2444`). Reach limited only by `loop_budget` = min(32 bars,
  64 - overlap) (`:2282`). The first candidate whose `pick_cue_bounded_drum_loop` succeeds wins.
  There is no "already ends on a line" stop and no "is a loop needed at all" test.
- An outgoing with **no outro section** (Enzo, T4) skips branch (3) entirely, so it can never get
  the own-ending loop Sam added (here T4; August T4 Zaro, "+7 bars, it had no outro").
- D15 raised looping pairs on the 380-pair corpus from 133 to 215
  (`Documentation/Plans/d15-outro-loop-targeting/*.json`); this mix went 2 -> 7.
- Replay check: re-running `align_pair` + `plan_fill_or_cut` on this mix's SECTIONS_STEM
  reproduces all 7 V5 loops with the same lengths (10, 24, 5, 8, 15, 8, 8 bars). The harness is
  valid for re-scoring.

**Agreement with earlier corrections**
- "Sam loops the incoming's intro; the pipeline extends the outgoing tail" (Fresh Mix V2 T2/T3/T6):
  **partly confirmed**. Sam removed 5 of 7 outgoing loops, and the 2 loops he added on the incoming
  side (T4, T6) are where V5 had none. But he kept 2 outgoing loops and added 1 (Enzo).
- August (15.09.26) "entries move later, never earlier": **contradicted here** - entries moved
  earlier on T2, T3, T4, T6, T7 and later only on T5, T8. What both mixes share is the result:
  ~16 bars after the swap, overlap around 32.
- August "keep loops of the outgoing's own last 1-2 bars, remove loops built from other material":
  **contradicted here** - Sam kept HARTY's intro-sourced loop and removed own-outro loops on Jones,
  Detlef, JK, Freejak, TCTS. Loop source does not explain this mix's decisions.
- August "swap snaps to the outgoing's 8-bar grid when the boundary is odd" (135 -> 136): **confirmed**
  (T3 153 -> 148; T9 swap at 136 vs detected outro at 135).

## 4. Proposed simplification policy (derived; each rule falsifiable)

Order matters: R1 -> R2 -> R3/R4 -> otherwise no loop. R5 is independent (incoming side).

**R1 - Stop when the outgoing already lands on a line.** If an incoming section start lies within
+-2 bars of E, plan no outgoing tail loop.
- Change: `plan_fill_or_cut` branch (3), `align_engine.py:2408-2411` (compute E =
  `current_incoming_bar`; check all `i.sections` starts, not only `>= E+2` from `:2232`). Record
  "not needed: natural end on <section>" in a new field (not `outgoing_loop_abandoned`, which means
  "wanted but failed" - D2b's transparency must stay unambiguous).
- Evidence: T2, T9 (Sam removed both); August T6 (E=32 on a line, Sam removed the loop).
  Counter: August T1 (near=2.0, Sam kept) - the +-2 edge is untested.
- Falsified if: on a future correction Sam re-adds a loop where E was within +-2 of an incoming
  section start.

**R2 - Re-anchor before looping (aligner, not loop planner).** If E is off a line by d <= 8 bars,
and shifting the incoming by d puts the swap on another outgoing section start that passes the
existing 16-48 bar / swap-progress checks, take that alignment and plan no loop.
- Change: `_align_pair_landmark_aware` (`align_engine.py:1217`), as a tie-break between valid
  outgoing-cue candidates for the chosen incoming drop anchor. Must not go into `plan_fill_or_cut`
  (its contract) and must leave `matched_tail_head_swap`/rescue ordering alone.
- Evidence: T3 (shift -5, swap 153 -> 148), T8 (shift +8, swap 176 -> 184) - both reproduced to the
  bar. It correctly declines T1 (shift to HARTY 158 is not a section start) and T10 (Shilla 160 is
  mid drop_5), where Sam kept the loop.
- Risk: changes the alignment itself, so `Tests/test_alignment_baseline.py` will diff; every changed
  pair must be read, not counted. Highest-risk rule; build last.
- Falsified if: Sam moves the incoming back to the V5 position and loops instead.

**R3 - Loop only a short reach.** If the next incoming section start is <= 12 bars past E, loop the
outgoing to it (existing D15 tiers and quality gate unchanged); otherwise no loop.
- Change: `:2448` - replace `candidate_gap > loop_budget` with
  `candidate_gap > min(loop_budget, policy.max_outgoing_reach_bars)`; new `TransitionPolicy` field
  (`transition_policy.py:44-102`). Keep INTERIM_V1 unchanged for A/B; set 12 in an experimental policy.
- Evidence: kept T1 (10), T10 (8); removed T7 (15), T2 (24). The 10/15 boundary is **one pair on
  each side** - the 12 is a guess inside that gap. August T1 (18-bar loop, kept) is a counterexample.
- Falsified if: Sam keeps a >12-bar outgoing loop again, or deletes a <=12-bar one that R1/R2 didn't
  already remove (T5 already is one: a 4-bar reach R3 would loop and Sam would not).

**R4 - Own-ending loop when the outgoing has no outro section.** Same reach test as R3, source = the
outgoing's last 4 bars (2 if 4 fails the gate), inserted at track end.
- Change: `:2416`/`:2431` gate on `outro is not None`; synthetic window in
  `pick_cue_bounded_drum_loop` (`:2064-2076`, only built from an outro section today); consumer in
  `propose_arrangement.py:907-919` inserts "BEFORE the outro" and needs an end-of-track insert point.
- Evidence: T4 (Enzo, +7.4 bars to JK fill_1 at 32); August T4 (Zaro, +7 bars). 2/2 across mixes.

**R5 - Incoming side (experimental, SAM_V1 lane only).** (a) If pre-swap incoming material < 16 bars,
loop the incoming intro back to 16 bars (T4 exact; T2 entry exact, though Sam swapped mid-drop_1
instead of looping). (b) Never start the incoming inside an outgoing kick dropout; extend the intro
loop back to the dropout start (T6 exact: Zaro 68). Change: `_plan_incoming_entry_extension`
(`:2153`) targets. Do **not** turn on `CUE_CONFIG.incoming_intro_loop` as-is: replayed here it fills
T4 correctly but also fires on T2 (48 bars, budget-capped), T7 (16) and T10 (8), where Sam added
nothing. August contradicts R5 (entries moved later), so this needs its own held-out test.

**R6 (low confidence) - round loop totals up to a 4-bar multiple.** T1 10 -> 12 (HARTY's outro then
re-enters on Jones bar 28 instead of 26); August T1 and T8 also grew by 2 bars. Change: `:2557`
(round-up is disabled in landmark mode) and the `(8,4,7,6,5,3,2,1)` length list at `:2046`.

**Would any rule reintroduce a D15 defect?** D1 (landmark beating a section): no, tier order kept.
D2/D2b (silent abandonment): no, but R1 needs its own "not needed" reason or D2b's field becomes
ambiguous again. D15's motivating cases: T1 (undershoot) still loops to break_1 (R3). T3 ("should
have fired") becomes a re-anchor (R2) - matches V5 Sam, not the V3 by-eye intent. **T2 was "confirmed
correct by Sam" on V3** (the 56-bar loop to break_2); R1 removes it, as Sam did in V5. That is a
direct conflict between his by-eye V3 call and his by-ear V5 edit (question 1).

## 5. Testing

**A. Re-score against Sam's 10 (in-sample).** Build a small replay (pattern: `Tools/d15_outro_loop_replay.py`):
load `22.09.26 .../_Stem Analysis`, run `align_pair` + `plan_fill_or_cut` in mix order, compare to a
frozen truth table built from section 2 (loop Y/N + bars, E, overlap, entry and swap in
outgoing-source bars, +-1 bar). Baseline must first reproduce V5 exactly (verified today).

| T | Sam (out loop / overlap) | V5 current | R1-R4 | R1-R5 |
|---|---|---|---|---|
| 1 | Y 12b / 46 | Y 10 / 44 ok | Y 10 / 44 ok | ok (exact with R6) |
| 2 | N / 32 | Y 24 / 56 x | N / 32 ok | N / 32, entry ok |
| 3 | N / 32 (swap 148) | Y 5 / 32 x | N / 32 swap 148 ok | ok |
| 4 | Y ~7b / 39 | N / 24 x | Y 8 / 32 (loop ok, overlap x) | Y 8 / 40 ok |
| 5 | N / 37 | Y 8 / 52 x | Y 4 / 48 x | x |
| 6 | N / 64 | N / 48 (loop ok, overlap x) | N / 48 (loop ok, overlap x) | N / 64 ok |
| 7 | N / 32 | Y 15 / 48 x | N / 33 ok (entry/swap x) | same |
| 8 | N / 32 (swap 184) | N / 40 (loop ok, overlap x) | N / 32 swap 184 ok | ok |
| 9 | N / 33 | Y 8 / 40 x | N / 32 ok | ok |
| 10 | Y 8 / 32 | Y 8 / 32 ok | Y 8 / 32 ok | ok |
| **Loop decision** | | **4/10** | **9/10** | **9/10** |
| **Overlap within 2 bars** | | **3/10** | **7/10** | **9/10** |

This is fitted on the same data - expect it to look good. It shows only that the rules are
consistent with these 10, not that they generalise.

**B. Held-out (already computable, weak).** August 15.09.26, current code, loop Y/N vs Sam's final
file: D15 4-5/11, R1+R3 5-6/11. Weak because Sam moved entries on 6 of 11 there, so current-code E
is not the geometry he judged. Framed per pipeline-proposed loop instead (12 loops across both mixes:
Sam kept 5, removed 7), R1+R2+R3 reproduce 10/12 (misses: T5 here, August T1) vs D15 5/12.
Next real test: a fresh corrected mix built with the new policy, judged blind against D15.

**C. Corpus replay (magnitude, no ground truth).** `PYTHONPATH=Source python Tools/d15_outro_loop_replay.py
--out before.json`, apply one rule, `--out after.json`, `--diff before.json after.json`; read every
changed row (D9/D15 discipline). Expected from today's read-only replay of the 353 aligned pairs:
R1 removes 58 of 215 loops; a 12-bar reach cap removes up to 119 (overlap with R1 still to measure);
the 26 `align_raise` and 1 `raise` rows must not change, and no new raise may appear. R1, R3, R4 must
leave `Tests/test_alignment_baseline.py` byte-identical (alignment untouched); R2 will not, by design.
Ship one rule per commit so each diff is attributable.

## 6. Risks and questions only Sam can answer

1. **T2:** on V3 you okayed the long Jones loop to Detlef's break_2; on V5 you cut it to 32 bars.
   When the outgoing already finishes on one of the incoming's section lines, is "don't loop" the rule?
2. **HARTY:** its tail loop is built from HARTY's intro drums (bars 1-2) and you kept and lengthened it;
   in August you removed Tommy Farrow's intro-drum tail loop. Is intro-sourced looping fine when it
   sounds right, or should it be banned?
3. **T5 Jewel Kid -> Zaro:** you left Jewel Kid only ~6 bars after the swap, with Zaro's drop half a
   bar off our grid. Deliberate short exit, or is our Jewel Kid grid out by two beats?
4. **T3/T8:** you slid the incoming onto a neighbouring section line instead of looping. Should that
   always be tried before any loop, and how far is too far to slide (we saw 5 and 8 bars)?
5. **Openers:** T1 here and T1 in August both kept 30-34 bars after the swap; everything else went to
   ~16. Is the first transition meant to run longer, or was that track-specific?
