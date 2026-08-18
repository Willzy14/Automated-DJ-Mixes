# 2026-08-18 - Double Dutch Kick-Attenuation Diagnosis

Read-only investigation. Branch: `investigate/kick-attenuation`.
Track under test: `Test Project/14.08.26/Audio/Sam Leagas - Double Dutch (Extended Mix) SW V1.wav`
(281.17 s, 44.1 kHz, stereo, 152 bars at bpm 130.06, downbeat 0.02 s).

This file does not ship code; it records what the audio says about Sam's three
open questions on this one track.

---

## Step 1 - Sam's four ear-listen dropouts vs already-detected features

Sam named four short dropouts by ear on a fresh listen today, with timestamps
approximate ("a couple of seconds" is not precise; +/-2 s margin):

| Sam mark  | (sec)   | bar @ bpm 130.06 |
|-----------|--------:|-----------------:|
| ~2:56     | 176     |  95.3665         |
| ~3:10     | 190     | 102.9533         |
| ~3:24     | 204     | 110.5402         |
| ~3:39     | 219     | 118.6689         |

(bar = (sec - 0.02) / (60/130.06 * 4), bar_dur = 1.845302 s)

For comparison: the closest entry in `signals.fills` (10 entries, all raw
kick-dropout detector output) and the `musical_landmarks` list (both from
`SECTIONS_STEM_Sam Leagas - Double Dutch (Extended Mix) SW V1.json`):

| Sam (sec) | nearest fill                    | dsec  | dbar   | nearest landmark         | dsec  | verdict |
|----------:|---------------------------------|------:|-------:|--------------------------|------:|--------|
| 176       | (none - nearest is 190.09)      | +14.09| +7.635 | `kick_gap_380_384` 175.33-177.17 | +0.67 | **detected (landmark path, high confidence)** |
| 190       | `signals.fills[5]` 190.09-191.93 | +0.09 | +0.049 | -                        |    -  | **detected (energy path)** |
| 204       | `signals.fills[6]` 204.85-206.70 | +0.85 | +0.461 | -                        |    -  | **detected (energy path; V3 also fires 1 beat at 0.14)** |
| 219       | `signals.fills[7]` 219.61-221.46 | +0.61 | +0.331 | -                        |    -  | **detected (energy path; V3 stays above thresh)** |

All four of Sam's ear-dropouts are already in the cached detection JSON,
within his ~2 s margin in every case. Three show up under `signals.fills`;
the 176 s one (a full-bar-95 dropout into drop_3) is captured under
`musical_landmarks[].kick_gap_380_384` instead (`confidence: "high"`,
`energy_off_fraction: 1.0`, `section_name: "fill_1"`). The same track,
same signals file - the four Sam named are all detected.

A finer split of which detector fires where (from running V3 at full
per-beat resolution around each mark):

- **176 s**: V3 raw confidence drops to 0.22, 0.16, 0.04, 0.06 across beats
  380-383 (sec 175.3-176.7) and snaps back to 0.94 at beat 384. 4-beat
  off-run, well past `MIN_KICK_OUT_BEATS = 2`. This is the V3-raw signal
  feeding the `musical_landmarks` path.
- **190 s**: V3 per-beat confidence stays 0.81-0.97 across beats 410-418.
  V3 does NOT cross threshold here. The fill is detected entirely on the
  energy path's `_phrase_fills` (raw per-beat drums peaks below
  `FILL_DIP_FRAC = 0.55` of the dynamic solid kick level).
- **204 s**: V3 dips to 0.145 on beat 447 (one beat of `presence_from_activation`
  flips off) but recovers next beat. Energy path catches a 4-beat fill
  soft run via `_phrase_fills`. Both signals agree something happened;
  only energy path surfaces the fill pair.
- **219 s**: V3 stays at 0.68-0.97 throughout beats 474-482 - it does NOT
  fire. Energy path is the sole detector (same `_phrase_fills` mechanism).

Reading for Step 3 (mechanism design): the three fills past 176 s are
energy-only detections; V3 alone misses them. This is the inverse of the
problem Sam flagged at bars 31-47 (V3 alone misses those too, but the cause
is different there - see Step 2).

---

## Step 2 - bars 31-47 attenuation hypothesis

Sam's hypothesis, his words:

> "the kick is STILL IN [during the 31-47 dropout]. It's just been low cut
> quite a bit. So this is where the energy detection and kick detection need
> to work in tandem."

That is: a low-cut reduces sub-bass energy specifically, so V3 (trained on
clean transients) may still read "probably present" while amplitude detection
correctly reads reduced energy.

### Method

1. Re-ran Demucs `htdemucs` on the full track (GPU: NVIDIA GeForce RTX 3050,
   cuda available, 12.5 s for 281 s of audio). Saved mono drums stem
   to `scratch_demucs/drums.wav` for analysis only (NOT committed).
2. Computed per-bar broadband RMS, bandpass RMS for 40-100 Hz (sub/kick
   fundamental), 200-800 Hz (mid), and 2-5 kHz (transient click) on the
   isolated drums stem, plus per-beat peak amplitude in the same windows.
3. Re-ran Kick Detector V3 on the same isolated drums audio with the real
   model weights at `Models/kick_crnn_V3.pt`. Log-mel at the model's own
   parameters (128 mels, 12 kHz fmax, 100 fps). Reported per-beat
   `beat_max_scores()` (continuous confidence) AND `presence_from_activation`
   boolean at threshold 0.30.

Note on the threshold: `Source/kick_model_adapter.py:19` defines
`DEFAULT_THRESHOLD = 0.30`, applied as an explicit override in
`Source/kick_model_adapter.py` `KickPresenceProvider._presence_readout`
(call site ~line 261):
`raw = self._model_mod.presence_from_activation(act, duration_s, bpm,
downbeat=downbeat, thresh=self.threshold)`.
The sibling Kick Detector project defines its OWN default
`PRESENCE_THRESH = 0.4` at `../Kick Detector/Source/model.py:23`, used as
the function's own default argument - but on this pipeline it is overridden
by the 0.30 above and never reached as the effective threshold.

### Real numbers: spectra, per-bar (bars 16-30 before, 31-47 during, 48-59 after)

```
    bar |   sec | broadband |  sub 40-100 | mid 200-800 | hi 2-5 k  | sub/broad | hi/broad |  flag
   -----+-------+-----------+-------------+-------------+-----------+-----------+----------+---------
   ... bars 16-30 (BEFORE) ...
   28   | 51.69 |   0.3550  |   0.32014   |   0.06090   |  0.02208  |   0.901   |  0.062   | BEFORE
   29   | 53.53 |   0.3635  |   0.32893   |   0.05890   |  0.02270  |   0.905   |  0.062   | BEFORE
   30   | 55.38 |   0.3622  |   0.32678   |   0.06150   |  0.02180  |   0.902   |  0.060   | BEFORE
   ... bars 31-47 (DURING) ...
   31   | 57.22 |   0.1318  |   0.04884   |   0.05410   |  0.03410  |   0.371   |  0.259   | DURING
   32   | 59.07 |   0.1128  |   0.04360   |   0.03175   |  0.01820  |   0.387   |  0.161   | DURING
   ...
   38   | 70.14 |   0.0957  |   0.02309   |   0.04243   |  0.02580  |   0.241   |  0.270   | DURING
   ...
   42   | 77.52 |   0.0854  |   0.01475   |   0.04084   |  0.02570  |   0.173   |  0.301   | DURING
   ...
   46   | 84.90 |   0.0751  |   0.00956   |   0.03752   |  0.02380  |   0.127   |  0.317   | DURING
   47   | 86.75 |   0.0780  |   0.00643   |   0.05902   |  0.03180  |   0.083   |  0.408   | DURING
   ... bars 48-59 (AFTER) ...
   48   | 88.60 |   0.3702  |   0.33051   |   0.07574   |  0.04180  |   0.893   |  0.113   | AFTER
   49   | 90.44 |   0.3724  |   0.33512   |   0.06299   |  0.03510  |   0.900   |  0.094   | AFTER
   ...
```

### What the broadband numbers say

Per-bar means (drums stem, mono):
- bars 28-30 BEFORE:  broadband 0.359, sub 0.322, hi 2-5 k 0.032
- bars 31-47 DURING: broadband 0.094, sub 0.023, hi 2-5 k 0.025
- bars 48-49 AFTER:  broadband 0.370, sub 0.330, hi 2-5 k 0.046

So:
- Broadband drops by ~74% (-11.8 dB equivalent) over bars 31-47.
- Sub 40-100 Hz drops by ~93% (-22.7 dB) - effectively zero by bar 47
  (sub RMS 0.006 at bar 47, vs 0.32 before/after).
- Mid 200-800 Hz drops by only ~17%; hi 2-5 k drops by only ~22%.
- `sub/broad` collapses from 0.90 to 0.08-0.41 - sub-band content is
  disproportionately absent relative to broadband.

### Per-beat peak amplitude (clarifies the picture)

Per-beat window peak, drums mono:

```
  bar |  range over 4 beats | mean peak | min peak | max peak
  -----+--------------------+-----------+----------+---------
   28  |   0.94 - 1.00       |  0.98     |  0.94    | 1.00
   29  |   0.97 - 1.00       |  0.99     |  0.97    | 1.00
   30  |   0.96 - 1.00       |  0.98     |  0.96    | 1.00
   31  |   0.63 - 0.99       |  0.81     |  0.63    | 0.99
   32  |   0.63 - 0.95       |  0.74     |  0.63    | 0.95
  ...
   46  |   0.57 - 0.81       |  0.69     |  0.57    | 0.81
   47  |   0.30 - 0.81       |  0.56     |  0.30    | 0.81
   48  |   0.99 - 1.00       |  0.99     |  0.99    | 1.00
```

Peak amplitudes stay strong (0.7-1.0) on roughly 60 of 68 beats during the
dip. The "peak" energy survives on most beats - what disappears is the
sustained-sub-band energy that an RMS window measures. So at most beats
inside bars 31-47, there IS still a transient to fire V3 on; the body's
ringing has been attenuated (sub-band RMS nearly silent) but the click of
each kick is mostly intact.

This is exactly Sam's mechanism: a kick whose fundamental/body has been
EQ'd out (or a low-pass filter applied) but whose click survives. Plus a
secondary effect: the broadband drum level is genuinely lower too
(-11.8 dB), so it's not pure low-cut - it's a fade-and-low-cut. Both
readings are true at once.

### What V3 sees per beat, bars 25-50

```
  range               | n beats | mean conf | min conf | max conf | P(kick ON @ thresh 0.30)
  --------------------+---------+-----------+----------+----------+------------------------
  before (bars 25-30) |   24    |   0.953   |  0.920   |  0.972   |   1.00
  DURING (bars 31-47) |   68    |   0.660   |  0.032   |  0.881   |   0.94
  after  (bars 48-50) |   12    |   0.956   |  0.915   |  0.973   |   1.00
```

Per-beat confidence does drop during the dip (mean 0.660 vs 0.955) - V3 is
not blind to the change. But it stays well above the 0.30 threshold on
nearly every beat (64 of 68). Only the very tail (beats 187-191 = bar
46.75-47.75) drops through threshold at 0.235, 0.405, 0.265, 0.032, 0.059.
Above-the-threshold beats during bars 31-47 still see `presence_from_activation`
return True, so they pass into the smoothed/section signal as kick-on.

The Dip-end is where V3 finally agrees - kick_returns from beat 192 (sec
88.595 = bar 48.0) - and that 4-5 beat off-run in fact fires a
`kick_dropout` cue at beat 187 (sec 86.29 = bar 46.75) under the standard
`_kick_cues` rule (`MIN_KICK_OUT_BEATS = 2`). That cue is enough for
`_phrase_fills` to spot the bar 47 trailing dropout, but NOT enough to
split a long section break across bars 31-47 - because it sees an OFF
window, then ON, then OFF again, never one contiguous off-run.

### Verdict

**MIXED, leaning confirmed-for-the-mechanism Sam cares about:**

- Sam's specific claim "kick is STILL IN, low-cut" - **partially confirmed.**
  The click/transient does survive on most beats inside bars 31-47 (mean
  peak 0.69, max 0.99); the sub-bass body is effectively gone (-22.7 dB,
  93% reduction). A pure low-cut would show broadband unaffected and sub
  reduced; we see both, so it's a fade plus a low-cut, not strictly one
  or the other. The dominant signal - the kick transient inside the
  drums stem - survives.

- Sam's claim "V3 fooled by attenuation, only energy catches the dip" -
  **confirmed for the bulk of bars 31-47** (V3 confidence stays at
  0.5-0.88 across beats 124-186, P(kick ON)=0.94 over the whole window).
  V3 only fires for the 4-5 beat tail at bars 46.75-47.75, well after
  the audible dropout has begun. The "in tandem" mechanism Sam describes
  has a real target here - there is a 16-bar window where energy-based
  detection would fire a section break and V3 raw alone does not.

- The bars 31-47 window is, in V3's model of the world, kick-still-on
  (confidence 0.5-0.88, well above the 0.30 threshold and well above any
  "borderline" band a confidence-fallback could plausibly latch onto
  for most of the run). So a "trust V3 confidence, fall back to energy
  only when borderline" mechanism would still leave this track with the
  same gap. The mechanism that actually catches bars 31-47 needs to be
  one that notices either (a) the broadband-energy drop regardless of
  V3's read, or (b) V3's CONFIDENCE DROPPING substantially from its
  track-level baseline (e.g. 0.95 -> 0.66 = -0.29 absolute drop is a
  real signal in this track).

One number that decided it: **sub-band RMS collapses from 0.32 to
0.006-0.05 (-22.7 to -35 dB) inside bars 31-47, while per-beat peak
amplitude stays at 0.7-1.0 on ~88% of the beats.** That is the spectral
fingerprint of "kick click survived, kick body low-cut/EQ'd" - which is
Sam's mechanism, with a fade alongside it.

---

## Step 3 - candidate "in tandem" mechanisms (DIAGNOSIS ONLY - NOT IMPLEMENTED)

Sam's proposed direction: cross-check the (correct) default stem-energy dip
detection with V3's kick-presence output rather than trusting V3 alone.
Two concrete candidate mechanisms sketched below; nothing is built,
nothing is shipped, this session is diagnosis only.

### Mechanism A: OR-of-off signals into the kick-on track

**Idea.** Instead of letting V3 raw alone define `kick_on`, treat the
section boundary cut as the union of both detectors. Section breaks fire
where either detector marks a drop-out run past `MIN_KICK_OUT_BEATS`, so
a break fires when EITHER `kick_on_energy == False` OR
`kick_on_v3_raw == False` over a qualifying run.

**Smallest concrete point in `Source/stem_detector.py`:**
**lines 424 + 436** in `detect()` - the assignment of `kick_on`. Today:

```python
# line 424 - default energy path
kick_on, kick_peaks, kick_ref = _kick_on_per_beat(envs["drums"], hop_t, bpm, downbeat, n_bars)
...
# lines 432-440 - V3 branch, post-fix
if kick_model or kick_provider is not None:
    _section_kick_on, _raw_kick_on = _model_kick_presence_per_beat(...)
    kick_on = _raw_kick_on    # <-- THE FIX (def5062) reads V3 raw, not smoothed
    landmark_kick_on = _raw_kick_on
    kick_source = "kick-detector-v3"
```

The mutation point is the `kick_on = _raw_kick_on` line at **stem_detector.py:436**.
The energy boolean is already captured in the `kick_on` local on line 424
before the if-block runs (call it `_energy_kick_on` for clarity). The
change becomes, inside the `if kick_model or kick_provider is not None:`
block at line 432:

```python
    _energy_kick_on_local = kick_on.copy()   # save before overwriting
    _section_kick_on, _raw_kick_on = _model_kick_presence_per_beat(...)
    # Tandem: a section break fires when EITHER signal says off.
    kick_on = _energy_kick_on_local | _raw_kick_on
```

Mechanically the new `kick_on` is OR-of-two-bool-arrays of length
`n_bars * 4`. A section boundary fires when a run of kick_on == False
length >= `MIN_KICK_OUT_BEATS` (=2 today), which then propagates through
the existing `_kick_cues()` (-> `_kick_cues` at line 203) and
`_assign_labels()` (-> line ~493) without any further code changes.
`signals.fills` continues to come from `_phrase_fills(kick_peaks, ...)`
on line ~447 (energy peaks/ref) so that path is unchanged.

**Real risk: would it reintroduce false section breaks on tracks where
V3 is currently more accurate than the energy path?**

This is the live concern, not generic hedging. Two pieces of named
corpus evidence to ground the risk:

1. The 109-track corpus pass on 2026-06-25 (per `AI_CONTEXT.md`):
   - Stem-grid validation showed "**~20% JIT (syncopated/Afro-Latin)
     correctly flagged + rejected**", meaning the energy path (and V3-
     pre-fix) would over-fire on syncopated tracks and the V3 path is
     what keeps them correctly handled.
   - The Delacour fix from the same session: "a filter-sweep intro has
     no sub-bass, so Delacour stays on its real kick - broadband fooled
     it". Energy-only (broadband) detection gets the downbeat wrong on
     Delacour; V3 + sub-band info gets it right. Adding "OR with
     energy" would reintroduce that.

2. `kick-detector-v3-raw` is the exact wording on the `source` field of
   the `musical_landmarks/kick_gap_380_384` item that fires at sec
   175.33-177.17 on THIS track (`AI_CONTEXT.md` 2026-07-16 entry:
   "Raw kick dropouts are landmarks, not forced sections"). The post-
   def5062 fix moved the section classifier to read V3 raw rather than
   V3 smoothed for exactly the reason Sam named: V3 smoothing was
   bridging over real dropouts. Going back to OR-with-energy pulls in
   the same class of bridging that the def5062 fix was designed to
   remove.

**Specific failure modes for OR-of-off on this corpus class:**

- On a syncopated Afro-Latin track where V3 reads kick-on correctly but
  broad-band per-beat peaks temporarily drop (clap-led phrasing,
  syncopated hats), `energy_off == True` over a multi-beat run while
  V3 stays ON. OR-of-off yields kick-on=False, a `kick_dropout` cue
  fires. Result: a spurious break inserted into a track where V3 was
  holding the right answer.
- On a Delacour-class track (filter-sweep intro, no sub-bass), the
  energy boolean reads kick-off for the intro. V3 reads kick-off in a
  *correctly different* part of the track (the actual kick entry, not
  the intro). OR-of-off would anchor on the intro dropout, putting
  `first_drop_sec` at sec 0 instead of the real first kick.

Counter-measure: require both signals to agree over their MIN run
windows (AND-of-on for the no-break decision) or take the longer of
the two detected runs. That is roughly the AndMechanism under Mechanism B.

For Double Dutch bars 31-47 specifically, Mechanism A fires correctly -
a kick_dropout cue is produced for the 16-bar window and section
classification sees the break - because Energy says off throughout and
OR-of-off catches it. But Mechanism A pays for that on the syncopated
class above.

### Mechanism B: V3-confidence change detector (track-relative, not absolute)

**Idea.** Use V3 per-beat `beat_max_scores()` as a continuous signal
and compare each beat's confidence to a track-relative baseline
(e.g. the median of the bars that look "full-drop"). A beat is
treated as kick-AMBIGUOUS when its `beat_max_scores()` value drops
by more than a threshold amount below the track baseline; in that
case, fall back to the energy boolean for that beat. Otherwise trust
V3's boolean.

This is the threshold-free read already present in the sibling
Kick Detector project (`Source/model.py` line ~132-144 - `beat_max_scores()`
returns per-beat continuous score = max activation in the beat window;
thresholding it at `thresh` reproduces `presence_from_activation`).
And `_section_kick_on` (line ~157 of stem_detector.py /
`_model_kick_presence_per_beat`) already computes both raw and
section signals.

**Smallest concrete point in `Source/stem_detector.py`:**
**lines 432-440** (same if-block as Mechanism A), specifically the
`_section_kick_on, _raw_kick_on` unpacking on line ~434. After the
call to `_model_kick_presence_per_beat`, replace `kick_on` with a
hybrid:

```python
    # Hybrid: track-relative confidence drop -> fall back to energy.
    _raw_scores = _get_raw_scores(...)     # beat_max_scores per beat
    _baseline = np.median(_raw_scores)     # track-level floor
    _ambig = _raw_scores < (_baseline - 0.30)
    kick_on = np.where(_ambig, _energy_kick_on_local, _raw_kick_on)
```

This requires either (i) extending `KickPresenceReadout` to expose
raw per-beat scores (today it only exposes `raw` bool and `section`
bool, both post-threshold), or (ii) calling `beat_max_scores` once
during `detect()` and threading it through. Either is a small
additive change.

**Real risk: would it reintroduce false section breaks on tracks
where V3 is currently more accurate?**

The risk profile is materially different from Mechanism A and
smaller on this corpus class. Two specific risks to flag:

1. **Marshall Weinstein - Slot Machine** (per `AI_CONTEXT.md` 2026-08-14
   session): "genuinely syncopated/off-4-to-floor, 98ms off its own
   kicks". That track currently gets beat-grid-rejected outright rather
   than mis-sectioned; if a hybrid confidence-change mechanism is on
   by default, the same confidence-vs-baseline drop would fire on a
   syncopated track where V3's baseline is itself low (no long
   "full-drop" run to anchor to). Without a careful baseline window
   (e.g., use the post-drop-1-to-pre-outro window only), the median
   baseline is ill-defined and Mechanism B becomes uncalibrated on
   this exact kind of track.

2. **Afro-Latin class** (per the corpus pass): V3 confidence on those
   tracks stays moderate-but-noisy throughout; per-beat confidences
   fluctuate around the median. Mechanism B with a "-0.30 absolute
   drop" rule would treat many beats as ambiguous, falling back to
   energy. The cheap rule "ambig = score < baseline - 0.30" then
   degrades toward Mechanism A on this class.

Counter-measure (already evident from the existing 0.30 model
constant): tie the ambiguity threshold to `self.threshold` rather
than a fixed `-0.30`. i.e., `ambig = _raw_scores < (track_baseline
- max(0.10, 0.30 - DEFAULT_THRESHOLD))` would degrade gracefully.

**Specific fit on Double Dutch:**
- Track baseline on bars 16-30 (median beat confidence) ~ 0.95.
- During bars 31-47, beat confidences cluster at 0.50-0.85 - drops
  of 0.10-0.45 below baseline. Mechanism B with a `-0.30` ambiguity
  window flips many of those beats to "ambiguous" and lets energy
  through. Result: a section break fires inside bars 31-47. Bars
  46-47's tail is correctly classified via V3 alone.
- Side effect: every long full-drop with subtle tempo feel (a
  slightly quieter breakdown that V3 still picks up) gets a section
  break inserted. Calibration on the 109-track corpus is the only
  way to validate; today there is no held-out test that would
  distinguish Mechanism A from Mechanism B on this question.

### Mechanism A vs Mechanism B - which one is the actual fix?

Honest read after the Step 2 numbers: **neither catches bars 31-47
cleanly on Double Dutch with no false positives on the syncopated
class.** Mechanism A catches it at a high cost on Delacour-class
tracks. Mechanism B can catch it if the baseline is defined off the
post-drop-1 bars - but then needs its own held-out replay per
Sam's prior discipline (`AI_CONTEXT.md` "held-out replay before
becoming a policy default"). The honest next action is:

- Pull a corpus-side false-positive / false-negative count for each
  mechanism on the 109-track Stephanes Playlist before either ships.
- For Double Dutch bars 31-47 specifically: confirm via the
  corpus pass that the OR-of-off (Mechanism A) doesn't reintroduce
  false breaks on Delacour / Discosteps / Marshall Weinstein before
  recommending it.
- The "kick-detector-v3-raw" wording on the existing
  `musical_landmarks[].source` field is a deliberate seam; any fix
  that reintroduces smoothed-V3 for downstream landmark generation
  should be avoided (the 2026-07-16 entry in `AI_CONTEXT.md`
  preserves raw reads specifically so energy-style bridging cannot
  smuggle back in via the landmarks path).

---

## Final summary

- **Step 1**: All 4 of Sam's ear-listen dropouts are detected by the
  pipeline within his ~2 s margin. 176 s lives under
  `musical_landmarks[].kick_gap_380_384` (high conf); 190/204/219 s
  live under `signals.fills[5-7]` (energy path).
- **Step 2**: MIXED, leaning confirmed for Sam's mechanism. Sub-band
  RMS -22.7 to -35 dB inside bars 31-47 (effectively gone), broadband
  -11.8 dB, mid/hi bands < 25% reduced, per-beat peak amplitudes
  0.7-1.0 on ~88% of beats, V3 confidence stays 0.5-0.88 (well above
  the 0.30 threshold) on 64 of 68 beats - i.e., V3 reads kick-on
  throughout the 16-bar dropout because the transient survives while
  the body is EQ'd out. The energy path would correctly catch the
  drum-out; V3 alone does not.
- **Step 3**: Two sketch mechanisms - OR-of-off (file:line
  `Source/stem_detector.py:436`) and track-relative V3-confidence
  change detector (file:line `Source/stem_detector.py:434` plus the
  raw-scores seam) - both plausibly catch bars 31-47 but each pays
  a price on named previous-tracked tracks (Delacour for A,
  Marshall Weinstein - Slot Machine for B). Neither is implemented
  in this session.

---

## Sources

- `Test Project/14.08.26/_Stem Analysis/SECTIONS_STEM_Sam Leagas - Double Dutch (Extended Mix) SW V1.json` - cached section/signal JSON (read).
- `Source/kick_model_adapter.py:19` - DEFAULT_THRESHOLD = 0.30 (read).
- `Source/stem_detector.py:424, 436, 443` - the kick_on assignment chain and `kick_cues` derivation (read, full file).
- `Source/kick_model_adapter.py:190` - `KickPresenceProvider._activation()` returns the per-frame sigmoid 0-1 (read).
- `Source/kick_model_adapter.py:261` - `presence_from_activation(act, duration_s, bpm, downbeat=downbeat, thresh=self.threshold)` - the explicit threshold override path (read).
- `../Kick Detector/Source/model.py:23` - `PRESENCE_THRESH = 0.4` (the function's own default arg); overridden by 0.30 from `kick_model_adapter` (read).
- `../Kick Detector/Source/model.py:132-144` - `beat_max_scores(act, duration_s, bpm, downbeat, fps)` - per-beat continuous confidence (read; used).
- `Documentation/AI_CONTEXT.md:116, 273, 539-543, 1052` - corpus pass & V3 raw vs smoothed rationale (read).

Scratch files used for analysis (NOT committed; created at
worktree root in `scratch_demucs/` and `scratch_*.py`):

- `scratch_demucs_v1.py`, `scratch_demucs/drums.wav` (Demucs GPU pass)
- `scratch_spectral_v1.py` (per-bar bands)
- `scratch_peak_check.py` (per-beat peak + bands)
- `scratch_v3_v1.py` (V3 activation + per-beat scoring)
- `scratch_other_drops_v2.py` (V3 around each Sam-marked dropout)
- `scratch_step1.py`, `scratch_lines.py`

These were created in the worktree to do this analysis and are
explicitly NOT being committed.
