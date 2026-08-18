# Bass-out isolated ablation (2026-08-18)

Invocation: `PYTHONPATH=Source python Tests/ablation_bassout_isolated.py`

Corpus: `Test Project/14.08.26/_Stem Analysis` (20 tracks, 380 ordered pairs).

Flag measured: `CueConfig.emit_bass_out = True`. All other flags stay False (isolates bassout, unlike the earlier combined run).

Sanity: BEFORE matches `baseline_alignments.json` on pinned fields? **YES** (0 mismatches).


## Headline

- Pairs changed (any pinned field): **4 / 380**
  - flipped raise -> ok: **0**
  - flipped ok -> raise: **0**
  - both same status, other pinned field(s) differ (repositioned): **4**
- Pairs whose AFTER `handoff_kind` contains `'bass_out'` and BEFORE didn't: **0**
- Key acceptance (changed AND ok-after AND outgoing bass_out_bar != None AND not bass_out_is_end): **0 of 4 = 0.00%** within 2.0 bars of the outgoing's real bass_out_bar.
  - Excluded from the proximity denominator:
    - AFTER status = raise: **0**
    - outgoing bass_out_bar is None: **0**
    - outgoing bass_out_is_end (could not fire as Tier-1 cue): **0**

## One-line interpretation
0/4 = 0.00% does NOT clear 50% -- the bass_out cue does not move most eligible pairs to the real bass_out bar. Wiring it is not a clean win.

## Proximity distances (denominator set)

| out | in | abs(handoff - bass_out) (bars) | within 2.0 |
| --- | --- | --- | --- |
| Alaia & Gallo - Pushin' From The Walls 16 Bit MASTER | BUTCH & Santos -  Come Get Up 24 Bit MASTER | 8.001 | N |
| Alaia & Gallo - Pushin' From The Walls 16 Bit MASTER | Nic Fanciulli & Butch - I Want You (Extended Mix) 24 Bit MASTER AMENDED | 8.001 | N |
| Alaia & Gallo - Pushin' From The Walls 16 Bit MASTER | Nic Fanciulli - Vente (Extended Mix) 24 Bit MASTER | 8.001 | N |
| Alaia & Gallo - Pushin' From The Walls 16 Bit MASTER | Switch Disco - You Are All I Need (Extended Mix) SW V2 24 Bit MASTER | 6.999 | N |