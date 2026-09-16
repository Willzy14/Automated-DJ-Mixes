# Teaching Mixes Cards - Index (2026-09-16)

A case-study library distilled from the 20 real finished mixes in `Teaching Mixes/` -
**for Claude to read and reason over directly, never for the automated pipeline.**
Sam's own framing: "this is not for the bots... this is for you as an AI looking for
several different answers for the same transition and distilling which one might
work best." Burn list C7 Step 1 (same session) built the formula version of "learn
from past mixes" and it lost to a trivial baseline - this is deliberately a different
thing.

Built by `Source/extract_teaching_mix_cards.py`. One `.md` file per mix, one card per
real transition found in that mix's arrangement. **This file's own numbers were wrong
twice on the way to here** - see Revision history at the bottom. Read the numbers on
this page, not any number quoted from memory of an earlier draft.

## Two real automation mechanisms, confirmed against actual files

**Mechanism 1 - the zone bus (5 files).** Individual song tracks carry ONLY the
arrangement; the actual mix move (fader ride, filter/EQ sweep) lives on shared
BUS/RETURN tracks named things like "A-Zone 62 DJ EQ" / "B-Zone 62 DJ EQ", fed by each
track's own sends, alternating roughly A/B/A/B (real exceptions exist - e.g. two edit
layers of one song briefly sharing a zone). Uses Ableton's modern
`<AutomationEnvelope>` mechanism. Confirmed on the newer-schema files (Live 10.1.25,
12.3.2) specifically.

**Mechanism 2 - direct track automation (14 files).** No shared bus at all - each song
track carries its OWN Mixer Volume, and/or its own `FilterEQ3` bass-gain (`GainLo`)
and/or `AutoFilter` cutoff-frequency automation, drawn directly onto the track via
Ableton's OLDER per-parameter `<ArrangerAutomation>` mechanism. This is the real
technique data for the 2015-era mixes, found only after a peer review caught that an
earlier version of this library had wrongly reported these files as "mixed live,
nothing written."

Zero overlap between the two mechanisms in the real corpus - a file uses one or the
other, never both. **19 of 20 files now have real, extracted automation. Only 1 file
(Gbox Side 3) has neither** - its real automation curves exist but are all on
parameters this library doesn't treat as a mix move (send levels, dry/wet, tempo).

## Coverage - the real numbers, both mechanisms

| | |
|---|---|
| Files processed | 20/20, zero crashes |
| Files with zone-bus automation (Mechanism 1) | 5 of 20 |
| Files with direct-track automation (Mechanism 2) | 14 of 20 |
| Files with real automation of SOME kind extracted into cards | **19 of 20** |
| Files with no extractable mix-move automation | 1 of 20 (Gbox Side 3 - has real curves, none on a classified parameter) |
| Real transitions found across all 20 files | 269 |
| Real automation points extracted, Mechanism 1 (zone-bus) | 14,398 |
| Real automation points extracted, Mechanism 2 (direct-track: Volume + GainLo + Cutoff only) | 1,212 |

**`GainLo` and `Cutoff` are MORE precise than the zone-bus macro case** - they're
positively identified device parameters (FilterEQ3's actual bass-shelf gain,
AutoFilter's actual cutoff frequency), not an unresolved macro knob. `Cutoff` is
reported as its raw 20-135 value (lower = more filtered/muffled, the classic DJ
filter-down move) - not yet converted to Hz, honestly labelled as such. **`GainLo`'s dB
conversion is CONFIRMED, not just inferred** (round 3: MiniMax flagged it as
plausible-but-unverified; a Claude subagent then queried Ableton's own Live manual and
found EQ Three's documented gain range is -infinite dB to +6dB per band - the real
file's own automation range converts to +6.02dB, matching almost exactly). `GainLo`'s
dB values can be trusted with the same confidence as the zone-bus Volume fader's.

## Rich-data files (worth reading first)

Zone-bus mechanism:
- `Defected In The House - Ibiza 2026 (CD 2 Of 2) [Defected].md` - 16 transitions, 993 automation points
- `Defected In The House - Ibiza 2026 (CD 3 Of 3) [Defected].md` - 21 transitions, 1005 automation points
- `Glitterbox Dance 4 Love Disc 1 [CB Edits] SW V2.md` - 26 transitions, 3730 automation points
- `Where Love Lives Side 2 (SW V1).md` - 16 transitions, 4691 automation points
- `WLL Side 3 [CB V1] SW V2.md` - 18 transitions, 3979 automation points

Direct-track mechanism (14 files) - every other file except `Gbox Side 3 Final Mixed SW
V1.md`, which has real curves on none of the classified parameters.

## Reading a card

Each transition card reports: outgoing/incoming track names, which zone each is
routed to if any (or "UNRESOLVED"), the real overlap window in bars (from actual clip
geometry), zone-bus automation (channel fader in dB, filter/EQ macro 0-64) where
present, direct-track automation (channel fader in dB, bass EQ in dB, filter cutoff
raw units) where present, any possible marker (locator) nearby with its meaning
explicitly unconfirmed, and a flag when both sides genuinely share one zone (no
crossfade expected - likely an edit/layer, not a fader move).

## Known limitations (real, not fixed this pass)

- The filter/EQ macro's exact downstream EQ band/frequency (zone-bus mechanism only)
  is not resolved - only that it's `MacroControls.0` on the zone's rack, MIDI-CC-mapped
  (a live-played knob), not which band(s) it drives.
- `Cutoff` (direct-track mechanism) is not converted to Hz - reported as its raw
  20-135 value, direction (lower = more filtered) confirmed, exact curve not resolved.
- Locator markers exist in 2 files (15 each) but their meaning (transition marker vs.
  something else) is unconfirmed - reported as "possible", never asserted.
- `_summarize_curve`'s snap/dip/spike classification is a heuristic, not exact - always
  cross-check against the real automation points for anything load-bearing. It can
  report the same point twice (once as a SNAP, once as a dip/spike) when they coincide
  - accurate, just noisy prose.
- `_track_zone` breaks send-value ties by file order, not by any semantic signal.
- Gbox Side 3's real automation (8 curves, 4,896 points) is entirely on parameters not
  currently classified as a mix move - not re-examined for whether any of those ARE
  actually relevant (e.g. a Send ride used as the actual crossfade mechanism there,
  which this pass would currently ignore).

## Peer review (2026-09-16)

**Round 1 - MiniMax + a Claude subagent (standing in for Codex, capped until
2026-09-19), independent.** Both confirmed the code sound. Real findings adopted:
`_ableton_version` renamed `_ableton_creator` (it returns Creator, not a parsed
version); `_summarize_curve`'s contradictory "gradual flat to X" fixed to "holds at
X"; the zero-automation test strengthened to check real underlying data, not just
rendered text. Pinned real-corpus regression test added. Full suite 857 -> 870.

**Round 2 (same day) - the Claude subagent's ground-truth spot-check found two real
bugs and one major false claim, all confirmed directly before fixing.** (1)
`build_card`'s same-zone check fired on `None == None`, fabricating a "direct
edit/layer" claim on 188 of 269 cards (70% of the whole library) - confirmed by exact
count, fixed, two new tests proved to fail against the pre-fix code. (2) `_track_zone`
trusted `TrackSendHolder`'s own `Id="N"` attribute as a 0-based position - confirmed
false against the real Defected files, mis-resolving the first two transitions of both
rich files; fixed to resolve by order of appearance. (3) The "12 of 20 mixed live,
nothing written" claim was flatly wrong - real automation exists in all 15 non-rich
files via the older ArrangerAutomation mechanism, just unextracted at the time. Full
suite 870 -> 872.

**Round 3 (same day, Sam's direction: "keep going - build the extraction now") - built
the Mechanism 2 extractor** (`_track_direct_automation`, `TrackDirectAutomation`) that
Round 2 identified as missing. Classifies real curves by their actual parameter name
(`Volume`, `GainLo`, `Cutoff`), converts Volume and GainLo to real dB via the same
curve as the zone-bus mechanism, reports Cutoff honestly as unconverted raw units.
Result: coverage rose from 5/20 to 19/20 files with real extracted automation. 4 new
tests (2 unit, 2 real-file). Pinned corpus test extended to cover both mechanisms and
their (confirmed zero) overlap. Full suite 872 -> 875. All 20 cards regenerated.

**Round 3 review (MiniMax + a Claude subagent, both independent): SOUND, one honesty
finding adopted.** MiniMax (no execution access this dispatch, said so plainly rather
than guessing) confirmed the regex/classification logic sound by static read and
flagged that `GainLo`'s dB conversion was stated with more confidence than had been
verified - a real, fair finding. The Claude subagent then independently re-ran the
extractor against the real corpus (confirmed all coverage numbers exactly: 5/14/19/1
files, 269 transitions, 1,212/14,398 points), reconstructed 5 real card entries from
raw XML by hand (all matched), and - going further than asked - queried Ableton's own
Live manual directly and found EQ Three's real documented gain range (-infinite dB to
+6dB), which the file's own automation range matches almost exactly (+6.02dB
computed). This turned MiniMax's caution into a genuine confirmation rather than
leaving it as an open question. VERDICT: SOUND from the subagent; the one adopted
finding (add the confirmation/caveat to the docstring, done) came from MiniMax.

## Revision history (why the numbers above moved twice)

1. First draft: "5 of 20 files have automation, the other 12 (+3 Group-Track) were
   mixed live, nothing captured." **Wrong** - only the "5 via zone-bus" half was true.
2. Round 2 correction: "5 of 20 extracted, but 15 more files confirmed to have real
   unextracted automation (492 curves, 26,822 raw points) via a different mechanism -
   a real follow-on, not built yet."
3. Round 3 (this version): the follow-on got built. **19 of 20 files now have real,
   extracted automation.** The 492/26,822 raw-block figures from round 2 counted every
   ArrangerAutomation curve regardless of parameter (including irrelevant ones like
   Send/DryWet/Tempo); the 1,212-point Mechanism 2 figure above is the real, classified,
   musically-relevant subset actually in the cards.
