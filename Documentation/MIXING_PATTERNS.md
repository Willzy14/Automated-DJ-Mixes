# Mixing Patterns - Legacy Extract From Unapproved Teaching Mixes

**Status 2026-07-15:** keep this as historical background only. Sam did not recognise the
`Teaching Mixes/` folder as the intended source of truth for this learning phase. Do not train
new arrangement or section priors from these numbers unless Sam explicitly re-approves that
folder. The approved starting corpus is the finished mix CD ALS project list in
`Documentation/Mix Patterns Library/README.md`.

Aggregate analysis of 20 professional DJ mixes (4,249 clips, 184 transitions).

## Transition Length

- Median: 25 bars, Average: 32 bars
- Distribution:
  - 4-8 bars: 16% (quick cuts)
  - 9-16 bars: 14% (short blends)
  - 17-32 bars: 40% (standard blend — sweet spot)
  - 33-64 bars: 22% (long blend)
  - 65+ bars: 9% (epic blend)
- Range: 2-171 bars — every transition is different

## Clip Fragmentation

- 76% of all clips are micro-edits (<16 beats)
- Average 10 clips per song, up to 200 in heavily edited mixes
- Songs are chopped into: main body + percussion loops + stabs + fills
- Gbox and Bargrooves projects show heaviest editing

## Intro Trimming (via LoopStart)

- 84% of all clips have LoopStart > 0 (trimmed)
- Incoming tracks at transitions trimmed by avg 41 bars
- Common trim amounts: 2-4 bars (micro-trims), 16 bars, 32 bars (intro skips)
- Purpose: skip minimal kick-only opening bars, start where energy matches

## Automation

- 4 main parameters on a control track:
  - LP filter frequency (range 0-64)
  - HP filter frequency (range 0-64)
  - Volume/gain A (range 0-1)
  - Volume/gain B (range 0-1)
- Sam's note: use 2 nodes with a sweeping curve, not many individual points
- Tempo automation present in some mixes (2-5 BPM range across full mix)

## Implications for Pipeline

1. Transition length should be variable, not fixed 32 bars
2. Incoming tracks should be trimmed to skip minimal intros
3. Automation needs both LP and HP filter sweeps + volume
4. Micro-editing / clip fragmentation is V2+ (requires section detection)
5. Tempo automation is V2+ (requires BPM trajectory planning)
