# Self-Similarity / Repetition Evaluation — Tier B Prototype

[Claude, 2026-08-20] Hands-on prototype for the "read the music better" menu, Tier B:
repetition. Read-only evaluation — no Source/ or Tests/ changes. Scratch scripts and raw
JSON results live in the session scratchpad:
`C:\Users\Carillon\AppData\Local\Temp\claude\C--Users-Carillon-Wired-Masters-Dropbox-Sam-Wills-0-1---GIT-HUB----Automated-DJ-Mixes\3226d5df-29b2-4c80-a16a-ed8ed4b6e472\scratchpad\`
(`ssm_proto.py`, `ssm_validate.py`, `ssm_refine.py`, `ssm_chroma_gap.py`;
`ssm_results.json`, `ssm_validation.json`, `ssm_refine.json`).

## TL;DR

Per-bar self-similarity from the CACHED features alone (stem envelopes + tiera band/width —
zero audio decode) is fast (0.03 s/track) and passes every validation worth passing:

- Repetition blocks line up with real structure: repeated bar pairs share a section label
  **87.1% of the time vs 42.8% chance** (4476/5139 pairs, 20 tracks).
- Foote novelty peaks off the same SSM hit section boundaries at **P=0.71 / R=0.70 vs 0.20
  random baseline** (+-2 bars).
- The drop~drop > drop~break invariant holds on **41/43** drop/break/drop triples.
- The loop-quality angle finds the V10 bad loop cold: **Come Get Up's tail-loop window ends
  in 4 beats of literal fade-to-silence (-30/-50/-63/-73 dB)** — 24% of its frames are
  silent. Every clean loop scores sil=0.00, worst-beat dip <= -4.7 dB. The V10 loop viz had
  claimed "no silent/dissipating flags"; this catches what that check missed.

Verdict: **wire in both** — (a) novelty as a boundary-confidence signal and (b) a
loop-window quality score. Chroma/MFCC fallback is NOT needed (tested on one track; gap
quantified below — it adds nothing on this corpus).

## 1. Method (cheap features only)

Per bar (grid = sections-JSON bpm + bar-0 anchor; grid error vs section end_secs < 0.01 s
on all 20 tracks):

- 5 stem envelopes (drums/bass/other/vocals/mix) x 4 quarter-bar means, in dB — 20 dims
- tiera_band_low/mid/high means in dB, tiera_width + tiera_lr_corr means — 5 dims
- z-score each of the 25 dims across the track's bars, cosine similarity -> SSM
- Repetition: off-diagonal stripe runs (3-wide median filter, threshold 0.60 z-cosine,
  min length 8 bars, min lag 4 bars); greedy dedupe keeps <= 12 representative block pairs
- Novelty: Foote checkerboard kernel (half-size 8 bars) along the SSM diagonal

Threshold sensitivity (mean bar coverage by any repetition block, 20 tracks):
thr 0.5 -> 0.98, 0.6 -> 0.94, 0.7 -> 0.87, 0.8 -> 0.70. Dance music is
repetition-saturated; 0.60 is a reasonable default, 0.7-0.8 if you want only the
strongest verbatim repeats.

## 2. 20-track summary — do repetition blocks line up with real structure?

Yes. Primary metric: **stripe label agreement** — for every detected repeated bar pair
(b, b+lag), do bars b and b+lag carry the same section label?

| Track | agree | chance | Foote P / R (rand R) |
|---|---|---|---|
| A Studio - SOS | 0.875 | 0.46 | 0.60 / 0.50 (0.12) |
| Alaia & Gallo - Pushin' From The Walls | 0.782 | 0.36 | 0.62 / 0.71 (0.19) |
| Andrea Oliva & Bensy - Nappp | 0.938 | 0.38 | 0.71 / 0.71 (0.21) |
| Andrea Oliva - Dancing | 0.929 | 0.45 | 0.80 / 0.67 (0.11) |
| BUTCH & Santos - Come Get Up | 0.997 | 0.52 | 0.78 / 0.88 (0.18) |
| Cevin Fisher & Harry Romero - That Sound | 0.830 | 0.28 | 0.56 / 0.38 (0.22) |
| Cevin Fisher - Emotions | 1.000 | 0.71 | 0.60 / 1.00 (0.13) |
| Christoph - Reachin | 0.967 | 0.37 | 0.56 / 0.71 (0.23) |
| Christoph - The Rise | 0.941 | 0.44 | 0.85 / 0.73 (0.25) |
| Doorly & Harry Choo Choo Romero - The Truth | 0.893 | 0.37 | 0.62 / 0.71 (0.20) |
| Fish Go Deep - The Cure & The Cause | 0.679 | 0.35 | 0.83 / 0.50 (0.16) |
| Harry Romero - Renegades | 0.611 | 0.47 | 0.78 / 1.00 (0.26) |
| Nic Fanciulli & Butch - I Want You | 1.000 | 0.49 | 0.90 / 0.90 (0.19) |
| Nic Fanciulli - Revoloution | 0.824 | 0.41 | 0.89 / 1.00 (0.27) |
| Nic Fanciulli - Vente | 0.680 | 0.47 | 0.88 / 0.58 (0.23) |
| Ritmo Da Rua (Harry Romero Remix) | 0.806 | 0.34 | 0.62 / 0.62 (0.22) |
| Sam Leagas - Bad Behaviours | 0.823 | 0.36 | 0.57 / 0.67 (0.23) |
| Sam Leagas - Double Dutch | 0.870 | 0.61 | 0.57 / 0.80 (0.23) |
| Soulsearcher - Feelin Love | 0.795 | 0.37 | 0.60 / 0.43 (0.15) |
| Switch Disco - You Are All I Need | 0.879 | 0.34 | 0.83 / 0.50 (0.19) |
| **Aggregate** | **0.871** | **0.43** | **0.71 / 0.70 (0.20)** |

Weakest agreement tracks (Renegades 0.61, Fish Go Deep 0.68, Vente 0.68) are the ones with
many short alternating sections — stripes legitimately span a drop+break unit that repeats
as a unit, which the per-bar label test counts as disagreement at the label switch.

A stricter test — "a section boundary inside a repeated block should recur at +lag in the
partner block (+-2 bars)" — passes only 27/92 (29%). That is mostly the section detector's
coarseness (variable drop lengths, fills absorbed into neighbours), not SSM noise; the
label-agreement number is the fair structural test.

## 3. The three validations

### (a) Boundary agreement

Foote novelty peaks (prominence > mean + 0.8 sd, min 4 bars apart) vs detector section
boundaries, +-2 bars: **precision 0.709, recall 0.701, random-recall baseline 0.198** —
3.5x chance. Good enough to use as a boundary-CONFIDENCE signal (agreeing peak = raise
confidence), not as a standalone boundary detector.

### (b) Double Dutch — is the 32-48 break detectably novel?

Yes, with an honest caveat. Region repeatedness (mean over bars of max similarity to any
bar outside the region):

intro 0.630 < **break_1 (32-48) 0.889** < drop_1 0.897 < drop_3 0.921 < drop_2 0.941

The break is less repeated than all three drops, but the margin over drop_1 is thin
(0.008). Reason: this break is the kick-ATTENUATED break (all four stems on — see
`2026-08-18 Double Dutch Kick-Attenuation Diagnosis.md`), so stem-energy features barely
separate it. Sliding 16-bar-window rank (intro excluded, 31 windows): windows overlapping
the break take **ranks 2, 4, 5, 6** of 31 least-repeated; rank 1 is bars 136-152, the
end-of-track strip-down. So: detectably the least-repeated interior region, yes — and a
kick-presence feature would sharpen it further.

### (c) drop~drop vs drop~break — the sanity invariant

**41/43 triples pass** across all 20 tracks (mean cross-block similarity: drop~drop
+0.43 avg, drop~break -0.30 avg — a wide gap). Showcase pair as briefed:

- **Cevin Fisher - Emotions** (intro/drop/break/drop): drop_1~drop_2 = **+0.270** vs
  drop~break = **-0.407**
- **Sam Leagas - Bad Behaviours** (intro/drop/break/drop/break/drop):
  d1/b1/d2 = **+0.133 vs -0.423**; d2/b2/d3 = **+0.401 vs -0.375**

The two failures are instructive, not alarming:

1. *That Sound* drop_4/break_2/drop_5: +0.640 vs +0.653 — a near-tie; that break is an
   energy-twin of its drops. Chroma does not rescue it (see section 5).
2. *Fish Go Deep* drop_3/break_2/drop_4: +0.180 vs +0.412 — drop_3 (drums,bass,vocals)
   and drop_4 (drums,bass,vocals,other) are genuinely different textures, and break_2
   shares drums+vocals with drop_3. The SSM is telling the truth; the "all drops are
   variants" assumption is the approximation here.

## 4. The loop-quality angle — the 6 V10 tail loops

Per loop window: beat-level self-consistency (mean pairwise cosine of beats inside the
window, whole-track z-context), min adjacent-beat similarity, silence fraction (frames
< track active-median - 25 dB), and worst-beat dip vs window median.

| Rank (worst first) | Track (window, reps) | selfsim | min adj | sil frac | worst dip |
|---|---|---|---|---|---|
| 1 (BAD) | **Come Get Up** beats 964-980 x1 | +0.772 | +0.605 | **0.24** | **-52.6 dB** |
| 2 | **Vente** beats 696-704 x6 | **+0.556** | **+0.331** | 0.03 | -8.2 dB |
| 3 | Nappp beats 660-672 x7 | +0.707 | +0.590 | 0.00 | -3.8 dB |
| 4 | Fish Go Deep beats 728-732 x8 | +0.725 | +0.538 | 0.00 | -0.8 dB |
| 5 | Bad Behaviours beats 580-608 x2 | +0.790 | +0.535 | 0.00 | -4.7 dB |
| 6 (clean) | Soulsearcher beats 644-672 x1 | +0.830 | +0.718 | 0.00 | -2.5 dB |

- **Come Get Up is the silent-gap loop.** Its per-beat mix energy: twelve beats around
  -20 dB, then **-30.1, -49.6, -63.2, -73.4 dB** — the final bar of the looped window is
  the track's fade-to-nothing. (Verified real: the envelope frames extend 0.7 s past the
  window end, so this is in-audio silence, not an analysis-window overrun.) Looping that
  window x1 inserts a bar of near-silence into the mix — Sam's "big silent area".
- **Vente is the second defect**: a 10 dB level cliff mid-window (beats 1-4 ~ -10.5 dB,
  beats 5-8 ~ -20 dB), caught by the SSM metrics (lowest selfsim 0.556, lowest min-adj
  0.331). Looped x6, that cliff repeats six times.
- The four clean loops: sil = 0.00, dip >= -4.7 dB, selfsim >= 0.707.

Honest note: cosine self-consistency ALONE does not isolate the silent loop (it ranks
mid-table — 12 of its 16 beats agree with each other, and z-cosine partially normalises
level away). The silence-fraction / worst-dip energy terms are what catch it. A production
loop-quality score needs BOTH terms: consistency (catches Vente) + absolute energy floor
(catches Come Get Up).

## 5. Chroma/MFCC gap check (one track, as briefed)

On the failing That Sound triple, per-bar chroma_cqt from the audio:

| Features | failing triple dd vs db | label agreement | cost/track |
|---|---|---|---|
| cheap (cached) | +0.640 vs +0.653 FAIL | 0.830 | **0.03 s** |
| chroma only | +0.983 vs +0.980 "pass" (saturated tie) | 0.715 | 4.1 s (decode 2.3 + cqt 1.8) |
| hybrid | +0.767 vs +0.774 FAIL | 0.708 | 4.1 s |

Chroma similarities saturate near 1.0 — loop-driven house holds one harmonic loop for the
whole track, so chroma DE-grades structural discrimination here (label agreement drops).
**Cached features are not too coarse; the audio fallback is unnecessary for this corpus.**

## 6. Verdict, costs, integration shape

**Verdict: (c) both.**

1. **Novelty signal for boundary confidence — yes.** P/R ~0.7 vs 0.2 chance for free
   (0.03 s/track). Use it to weight boundary confidence and as a tiebreaker when
   refine_segments hesitates between candidate boundaries — not as a standalone detector.
2. **Loop-window quality score — yes, highest value-per-line.** It flags the exact V10
   ear-complaint (Come Get Up) plus a second real defect (Vente) that shipped, and it
   directly answers the Hardening Tracker line on `apply_loops.py`: "loop windows never
   quality-checked against band energy". Score = beat-level self-consistency AND
   silence-fraction AND worst-beat dip vs window median; grade candidate windows before
   `apply_loops` commits, prefer the best-scoring window, hard-fail windows with
   sil > 0.05 or dip < -12 dB.
3. Bonus, nearly free: **section-variation awareness** — per-section-pair cross-similarity
   (the 41/43 table) labels drop_2 as a variant of drop_1 in the sections JSON.

**Costs:** 0.03 s/track on cached npz (features + SSM + stripes + novelty; 0.7 s for all
20). numpy only — no new dependencies, no audio decode, no GPU. Memory trivial
(~265x265 SSM max).

**Integration shape (CueConfig pattern):** a `RepetitionProbe` alongside
`stem_section_probe.py`'s Tier A features — consumes the existing npz + sections JSON,
emits per track: novelty curve (per bar), top repeated-block pairs, per-section
repeatedness + section-pair similarity matrix, and a `loop_quality(beat0, beat1)`
scorer. Cache as sibling keys in the npz (`tierb_novelty`, `tierb_blocks`) or a
`REPETITION_<track>.json`; expose knobs (`thr=0.60`, `min_block_bars=8`, `min_lag_bars=4`,
`silence_rel_db=-25`, `dip_fail_db=-12`) through the CueConfig-style dataclass so
`/section-detection` tuning sessions can sweep them.

**Limitations logged:** stem-energy features cannot separate a kick-attenuated break from
its drops (Double Dutch margin 0.008; That Sound tie) — a kick-presence per-bar feature
(Kick Detector V3 output is already cached) is the cheap sharpener, not chroma. The strict
boundary-transfer number (29%) says stripe EDGES should not be trusted to +-2 bars for
boundary placement; use Foote peaks for that.
