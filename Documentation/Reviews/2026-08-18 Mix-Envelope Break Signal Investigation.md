# 2026-08-18 - Mix-Envelope Break Signal Investigation

Read-only diagnostic. Branch: `investigate/mix-envelope-signal`.
Track under test: `Test Project/14.08.26/Audio/Sam Leagas - Double Dutch (Extended Mix) SW V1.wav`
(281.17 s, 44.1 kHz, stereo, 152 bars at bpm 130.06, downbeat 0.02 s).

Sam's hypothesis: the detector already computes a full-mix RMS envelope
per bar (`mix_norm`), and a 16-bar full-mix energy dip is plainly visible
to a human on the whole-track waveform at bars 31-47 (matching the
drums-stem sub-bass collapse Sam already documented). The question is
whether `mix_norm` ever reaches the section-classification decision as
an *independent* break signal, or only as a *gate* on a section that is
already classified as a drop.

This file records what the cached envelopes and the source code actually
show. No code under `Source/`, `Test Project/`, or `Documentation/` is
modified. Scratch analysis script is at `scratch_mix_envelope.py` (worktree
root, not committed).

---

## 1. Is `envs["mix"]` / `mix_norm` genuinely the full-mix envelope?

**Yes -- genuine full-mix, not a stem, not a stem sum.**

Source path, with the exact line numbers found in this worktree:

- `Source/stem_section_probe.py:67-110` is `def _separate_envelopes(wav_path, cache_dir, hop_sec=0.1)`.
  - `Source/stem_section_probe.py:79` loads the original WAV:
    `data, sr = sf.read(str(wav_path), always_2d=True)      # [n, ch]`
  - `Source/stem_section_probe.py:80` reshapes to `[ch, n]`:
    `wav = data.T.astype(np.float32)                         # [ch, n]`
  - The Demucs `apply_model(...)` call at `Source/stem_section_probe.py:91`
    writes the four stem tensors to `out: [src, ch, n]`, and only those four
    (drums, bass, other, vocals) are envelope-binned in the comprehension
    at `Source/stem_section_probe.py:106`:
    `envs = {name: _env(out[i].mean(0).cpu().numpy()) for i, name in enumerate(src_names)}`
  - The `mix` key is added on the *next* line and is taken from the original
    loaded wav, NOT from `out`:
    `Source/stem_section_probe.py:107`:
    `envs["mix"] = _env(wav.mean(0))   # original-track envelope, for the top panel`

- The two consumers of this dict that matter for the classification decision
  are both in `Source/stem_detector.py`:
  - `Source/stem_detector.py:397`:
    `envs, hop_t = _separate_envelopes(wav, project / "_Stem Analysis")`
  - `Source/stem_detector.py:408-409`:
    ```
    mix_norm = _per_bar(envs["mix"], hop_t, downbeat, sec_per_bar, n_bars)
    mix_norm = mix_norm / (mix_norm.max() + 1e-9)
    ```
  - `mix_norm` is then passed straight into `_assign_labels(...)` as its
    fourth positional argument at `Source/stem_detector.py:486`:
    `_assign_labels(sections, kick_on_bar, presence["bass"], mix_norm, outro_start)`

The prior diagnosis (`Documentation/Reviews/2026-08-18 Double Dutch
Kick-Attenuation Diagnosis.md`) already used this same `mix` key; the
cached `.npz` is the on-disk form of `envs` produced at
`Source/stem_section_probe.py:110` (`np.savez_compressed(cache, hop_t=..., **envs)`).
For the run that produced this file, `envs["mix"]` is the original full-mix
RMS envelope -- a mono reduction of the raw loaded wav, identical to what
the DETECT PNG top panel later plots.

---

## 2. Real before / during / after numbers

The script `scratch_mix_envelope.py` (worktree root, throwaway) reproduces
`_per_bar` verbatim from `Source/stem_detector.py:75-80` and applies it to
`envs["mix"]` and `envs["drums"]` from the cached
`__stemenv.npz` (hop_t=0.1 s, 2811 hops covering 281.1 s). Downbeat 0.02 s,
sec_per_bar 1.8453023528, n_bars 152, exactly as the prior diagnosis.

### Per-bar mix envelope, bars 16-60

```
  bar |   sec | mix_raw_rms | mix_norm(0-1) | drums_raw_rms
  ----+-------+-------------+---------------+---------------
   16 |  29.54|    0.40435  |     0.8964    |    0.29279
   17 |  31.39|    0.40359  |     0.8948    |    0.27255
   18 |  33.24|    0.40594  |     0.9000    |    0.29176
   19 |  35.08|    0.39797  |     0.8823    |    0.27309
   20 |  36.93|    0.40346  |     0.8945    |    0.29118
   21 |  38.77|    0.40306  |     0.8936    |    0.27439
   22 |  40.62|    0.40343  |     0.8944    |    0.28718
   23 |  42.46|    0.17262  |     0.3827    |    0.09887      <-- bar-23 single-bar fill, raw kick-off
   24 |  44.31|    0.40686  |     0.9020    |    0.28524
   25 |  46.15|    0.40870  |     0.9061    |    0.28899
   26 |  48.00|    0.39970  |     0.8861    |    0.27207
   27 |  49.84|    0.40690  |     0.9021    |    0.29097
   28 |  51.69|    0.40372  |     0.8950    |    0.26695
   29 |  53.53|    0.40917  |     0.9071    |    0.29170
   30 |  55.38|    0.39812  |     0.8826    |    0.27184
   31 |  57.22|    0.17463  |     0.3871    |    0.10111      <-- DIP STARTS
   32 |  59.07|    0.17356  |     0.3848    |    0.07817
   33 |  60.91|    0.14530  |     0.3221    |    0.07717
   34 |  62.76|    0.13286  |     0.2946    |    0.07605
   35 |  64.61|    0.13668  |     0.3030    |    0.07257
   36 |  66.45|    0.15211  |     0.3372    |    0.07457
   37 |  68.30|    0.12831  |     0.2845    |    0.06435
   38 |  70.14|    0.11771  |     0.2610    |    0.06956      <-- min mix_norm in dip
   39 |  71.99|    0.12468  |     0.2764    |    0.06114
   40 |  73.83|    0.14104  |     0.3127    |    0.06576
   41 |  75.68|    0.14566  |     0.3229    |    0.05720
   42 |  77.52|    0.14692  |     0.3257    |    0.06275
   43 |  79.37|    0.15024  |     0.3331    |    0.05933
   44 |  81.21|    0.15586  |     0.3455    |    0.05818
   45 |  83.06|    0.16043  |     0.3557    |    0.05552
   46 |  84.90|    0.16249  |     0.3602    |    0.05307
   47 |  86.75|    0.15578  |     0.3454    |    0.06488
   48 |  88.59|    0.41460  |     0.9192    |    0.29091      <-- DIP ENDS
   49 |  90.44|    0.43823  |     0.9715    |    0.30238
   50 |  92.29|    0.43229  |     0.9584    |    0.28287
   51 |  94.13|    0.43330  |     0.9606    |    0.30129
   52 |  95.98|    0.42753  |     0.9478    |    0.28991
   53 |  97.82|    0.43767  |     0.9703    |    0.30573
   54 |  99.67|    0.43002  |     0.9534    |    0.28184
   55 | 101.51|    0.17887  |     0.3965    |    0.08037      <-- bar-55 single-bar fill
   56 | 103.36|    0.41862  |     0.9281    |    0.30179
   57 | 105.20|    0.44323  |     0.9826    |    0.32530
   58 | 107.05|    0.43531  |     0.9651    |    0.31672
   59 | 108.89|    0.43572  |     0.9660    |    0.30774
   60 | 110.74|    0.43538  |     0.9652    |    0.31928
```

The single-bar dips at bar 23 and bar 55 are the existing fill regions
the detector already catches via `_phrase_fills`; they are shown here so
the 16-bar dip at bars 31-47 can be sized against them.

### Before / during / after summary (mix envelope only)

- bars 28-30 (BEFORE): mean mix_raw_rms = **0.40644**, mean mix_norm = **0.9011**
- bars 31-47 (DURING): mean mix_raw_rms = **0.14678**, mean mix_norm = **0.3254**
- bars 48-49 (AFTER):  mean mix_raw_rms = **0.42641**, mean mix_norm = **0.9454**
- **% drop BEFORE->DURING (raw)**: **-63.9%**
- **% drop BEFORE->DURING (normalized)**: **-63.9%** (same, by construction:
  normalization is `mix_norm = mix_norm / mix_norm.max()` so the ratio is
  preserved for any constant-magnitude gap; here the denominator `mix_norm.max()`
  is set by the track's fullest drop, well above bars 28-30 and 48-49)
- **min mix_norm inside bars 31-47**: **0.2610 at bar 38**
- **percentile of bars-31-47 mean across the full mix_norm distribution**:
  **11.2%** (only 11% of bars on the track have lower mix_norm than the
  bars-31-47 mean)
- **full-track mix_norm distribution**: min 0.2183, max 1.0000, median 0.8792,
  mean 0.6825

### Drums cross-check (shape only, not absolute)

The prior report's broadband drums numbers were 0.359-0.372 (before),
0.094 (during), 0.370 (after) on a *freshly separated* drums stem with a
bandpass filter applied -- a different normalization. The whole-stem
`envs["drums"]` from the cached `.npz` (no bandpass) gives the
corresponding bars 28-30 / 31-47 / 48-49 means as 0.2793 / 0.0679 / 0.2966
(absolute values differ because there's no bandpass and it's a different
demucs run, but the SHAPE is the same):

- during/before ratio = 0.243 (cached whole-stem drums)
- during/before ratio = 0.257 (prior report's bandpassed broadband drums)
- during/after ratio = 0.229 (cached whole-stem drums)
- during/after ratio = 0.254 (prior report's bandpassed broadband drums)

Same dip, same bar range, same depth. The dip in the mix envelope and the
dip in the drums envelope are co-located at bars 31-47, exactly as the
prior report found at the sub-band level.

---

## 3. Where does `ef` / `mix_norm` get used in the classifier?

**It is used ONLY as a gate inside `is_drop()`; it does NOT appear
anywhere in the break/fill branch as an independent signal.** The mix
envelope is computed for every candidate section via `stat(s)` at
`Source/stem_detector.py:277` but the value is only consulted at
`Source/stem_detector.py:301` (`elif is_drop(s):`), which delegates to
`is_drop()` at `Source/stem_detector.py:285` where `ef >= drop_thr` is the
gate. The break/fill branch at `Source/stem_detector.py:303` reads
`bf < 0.4 or kf < 0.4` and is silent on `ef`.

Verbatim, with the line numbers as found in this worktree
(`Source/stem_detector.py:268-309`):

```python
268  def _assign_labels(sections, kick_on_bar, bass_pres, mix_norm, outro_start):
       """Label by song position: intro before the first drop; outro from the lead
       drop-off near the end; drop/break/build in the body. A bass / no-bass split
       inside the intro stays INTRO. Every track is guaranteed an intro and outro."""
272      def stat(s):
273          s0, s1 = s["start_bar"], s["end_bar"]
274          kf = kick_on_bar[s0:s1].mean() if s1 > s0 else 0.0
275          bf = bass_pres[s0:s1].mean() if s1 > s0 else 0.0
276          ef = mix_norm[s0:s1].mean() if s1 > s0 else 0.0
277          return kf, bf, ef
278
279      n = len(sections)
280      full = max((stat(s)[2] for s in sections), default=1.0)
281      drop_thr = DROP_REL * full
282
283      def is_drop(s):
284          kf, bf, ef = stat(s)
285          return kf > 0.6 and bf > 0.5 and ef >= drop_thr
286
287      first_drop = next((i for i in range(n) if is_drop(sections[i])), None)
288
289      for i, s in enumerate(sections):
290          kf, bf, ef = stat(s)
291          if outro_start is not None and s["start_bar"] >= outro_start:
292              label = "outro"
293          elif first_drop is None:
294              label = "intro" if i < n / 2 else "outro"
295          elif i < first_drop:
               # Pre-drop is intro -- EXCEPT a long kick drop-out, which is a 'first
               # break' (the drums all come out) even with no bass before it. ...
296              is_long = (s["end_bar"] - s["start_bar"]) > FILL_MAX_BARS
297              label = "break" if (kf < 0.4 and is_long) else "intro"
298          elif is_drop(s):
               # uses is_drop() -> kf > 0.6 and bf > 0.5 and ef >= drop_thr
301              label = "drop"
303          elif bf < 0.4 or kf < 0.4:
               # kick/bass out: short = fill, long = break (Sam's 6-bar rule of thumb)
               # (no reference to ef / mix_norm)
304              label = "fill" if (s["end_bar"] - s["start_bar"]) <= FILL_MAX_BARS else "break"
305          else:
306              label = "build"
307          s["label"] = label
```

The `ef` variable is unpacked at line 290 alongside `kf` and `bf`, but in
the only branch that matters for this investigation (the break/fill
branch at line 303), the condition is `bf < 0.4 or kf < 0.4` -- it
tests kick and bass presence and nothing else. The mix envelope is
discarded for break/fill decisions. The `DROP_REL` constant is at
`Source/stem_detector.py:64` with the comment "a drop must reach this
fraction of the track's FULLEST section energy" -- so the *only* way
`ef` enters a label is as a *confirmation* that a section that is
otherwise kick-and-bass-on (a candidate drop) actually has full mix
energy. A 16-bar section that is fully kick-and-bass-OFF, like bars
31-47 of this track, never reaches `is_drop()` in the first place (it
short-circuits to the `elif bf < 0.4 or kf < 0.4` branch), so the
`ef >= drop_thr` gate at line 285 is never evaluated for it.

`ef` / `mix_norm` is therefore a gate-on-drop-confirmation, not an
independent break-detection signal.

---

## 4. What does the DETECT PNG actually draw on the mix-envelope panel?

Code-level facts only, from `Source/stem_detector.py:612-700` (`_visualize`),
no visual verdict rendered.

- The top panel is `axes[0]`, drawn with a 2.6 height ratio (relative to 1.0
  for the four stem panels) at `Source/stem_detector.py:626`:
  `fig, axes = plt.subplots(len(order) + 1, 1, figsize=(20, 10), sharex=True,
   gridspec_kw={"height_ratios": [2.6] + [1] * len(order)})`.
- The mix envelope is drawn at `Source/stem_detector.py:649-650` as a
  *normalized-by-track-max* (NOT the `mix_norm` that the classifier uses --
  this is its own per-track `mix / (mix.max() + 1e-9)`, computed at line
  649 from `envs["mix"]` truncated to `L`):
  `axes[0].fill_between(t, mix / (mix.max() + 1e-9), color="#222", alpha=0.22, zorder=1)`.
  Color `#222` (near-black), `alpha=0.22` (very transparent), zorder 1.
- Y-axis is `axes[0].set_ylim(0, 1.05)` and `axes[0].set_ylabel("TRACK", ...)`
  at `Source/stem_detector.py:651-652`. Title at line 653.
- Then the panel is overlaid, in draw order, with:
  - Section color spans via `overlays(axes[0], label_sections=True)` at
    `Source/stem_detector.py:654`, which calls the inner `overlays` defined
    at `Source/stem_detector.py:627-647`. Inside `overlays`:
    - 4-bar phrase lines (`Source/stem_detector.py:629`): light grey
      vertical lines `color="#d2d2d2"`, `lw=0.3`, `alpha=0.6`, zorder 0.
    - Section color spans (`Source/stem_detector.py:631`):
      `ax.axvspan(sec["start_sec"], sec["end_sec"], color=_seccol(sec["label"]),
       alpha=0.15, lw=0, zorder=1)` -- alpha 0.15 (very transparent),
      color from `SECTION_COLORS` at `Source/stem_section_probe.py:38-41`
      (intro blue, build amber, drop red-orange, break purple, fill grey,
      outro green). The drop region is the one covering bars 16-44 in
      this track.
    - Section start vertical lines (`Source/stem_detector.py:632`):
      `ax.axvline(sec["start_sec"], color="k", lw=0.7, alpha=0.5, zorder=2)`.
    - Section label text (only on `axes[0]` because `label_sections=True`
      is passed at line 654): label + bar count, white-bg bbox at
      `Source/stem_detector.py:634-638`.
    - Kick OUT cues (`Source/stem_detector.py:640`): gold `#d4a017` dashed
      lines, lw 1.0, alpha 0.9, zorder 3.
    - Kick IN cues (`Source/stem_detector.py:642`): teal `#17a2b8` dotted
      lines, lw 1.0, alpha 0.8, zorder 3.
    - Major cues (`Source/stem_detector.py:644`): magenta `#d6006d` dashed
      lines, lw 1.7, alpha 0.95, zorder 4.
    - Bass IN / OUT (`Source/stem_detector.py:646-647`): deep blue `#1f4e9e`
      solid lines, lw 2.4, alpha 0.95, zorder 5.
  - Loop windows (`Source/stem_detector.py:655-656`): thin green `#2e9e5b`
    strip at the panel's bottom edge, `ymin=0.0, ymax=0.03`, alpha 0.95,
    zorder 6.
  - Vocal regions (`Source/stem_detector.py:657-658`): thin purple `#8e44ad`
    strip at the panel's top edge, `ymin=0.95, ymax=1.0`, alpha 0.75,
    zorder 6.
  - Fills (`Source/stem_detector.py:660-663`): orange `#ff8c00` full-height
    spans, alpha 0.6, zorder 6, with a vertical "fill" text label at the
    centre, fontsize 6.
  - Musical landmarks (`Source/stem_detector.py:665-671`): high strip
    `ymin=0.82, ymax=0.94`, color `#c2185b` (pre-drop) or `#6a1b9a`
    (kick gap), alpha 0.8, zorder 7, with a "pre-drop N b" / "kick gap N b"
    white-text label at the centre, fontsize 6. For this track two
    landmarks exist -- the bar-47 trailing drop and the bar-95-96
    pre-drop_3 kick gap (per the cached SECTIONS_STEM json).
  - Major cue text (`Source/stem_detector.py:672-673`): magenta `"~1:00 in"`
    / `"~1:00 to end"` text labels above the panel, fontsize 7.
  - Bass IN / OUT text labels (`Source/stem_detector.py:674-677`): blue
    `"BASS IN"` / `"BASS OUT"` text labels above the panel, fontsize 8.
  - Every-16-bar numeric bar markers along the bottom of the panel
    (`Source/stem_detector.py:678-680`): white-bbox black text
    `"0"`, `"16"`, `"32"`, ... fontsize 9.
- For this track the kick CUES list is empty (`signals.kick_cues: []` in
  the cached JSON) and the kick IN/OUT overlays therefore draw nothing.
  The two pre-drop kick-gap landmarks (bar 47 trailing, bar 95-96) are
  the only high-strip draws in that color. The fill_1 region (bars 92-96)
  is the only orange fill span inside the section, with the "fill_1" text
  label centered on it. The 16-bar `drop_1` section color span (intro-blue
  no, drop red-orange at alpha 0.15) covers bars 16-44 and therefore
  visually spans the entire dip.

**Visual check (Claude, direct inspection of the rendered PNG,
`Test Project/14.08.26/_Stem Analysis/DETECT_Sam Leagas - Double Dutch
(Extended Mix) SW V1.png`):** the dip IS visible in the TRACK panel to
the eye -- the grey `#222`/alpha-0.22 fill visibly compresses from a
~0.6-1.0 envelope down to a ~0.15-0.4 band across bars 31-47/48, a
clearly distinguishable shelf against the bars immediately before (16-30)
and after (48+). So the raw signal a human would need is present in the
picture. But nothing in the current rendering FLAGS it as notable: the
dip sits entirely inside the alpha-0.15 red-orange "drop" background
span that covers bars 16-44 (`drop_1`) and the amber "build" span at
44-48 -- the same colour wash the strong bars 16-30 also get -- so
visually the dip reads as "a slightly quieter patch of the same drop
section," not as a distinct event. Contrast this with the fill_1 region
at bars 92-96, which the picture DOES call out explicitly (opaque
orange alpha-0.6 full-height span plus a "fill" text label): bars
31-47 gets no equivalent callout -- no break-purple background, no
orange fill span, no text label, no landmark strip. A careful viewer
scanning the TRACK panel bar-by-bar could still notice the amplitude
shelf, but the picture actively frames the region as an ordinary part
of the drop rather than drawing attention to it, which matches Sam's
point: the information is rendered, but only as raw amplitude, not as
a flagged/labelled signal -- exactly the "computed but not used" gap
this whole investigation is about.

---

## 5. Decisive answer

**Qualified YES.** A standalone "mix_norm dips below threshold X for N+
consecutive bars" check, if it existed alongside the existing
kick/bass-presence logic rather than only as a gate inside `is_drop()`,
would have caught bars 31-47 on this track -- cleanly -- and is
distinguishable from the other per-bar mix_norm dips on the same track
as long as the run-length check is set to a value that excludes the
single-bar fills and the trailing outro tail.

The real numbers backing the yes:

- `mix_norm[31:47].mean() = 0.3254` (the value `ef` would have for a
  candidate section spanning bars 31-47; matches `stat(s)[2]` at
  `Source/stem_detector.py:277`).
- The `drop_thr` value for this track is
  `DROP_REL * full = 0.85 * max(ef across SECTIONS_STEM sections) =
  0.85 * 0.7827 = 0.6653`
  (`DROP_REL = 0.85` at `Source/stem_detector.py:64`; `full` from the
  per-section ef table below).
- `ef[31:47] = 0.3254` is **below** `drop_thr = 0.6653` by a factor of
  2.04. If bars 31-47 were ever evaluated through `is_drop()`, the
  `ef >= drop_thr` gate at `Source/stem_detector.py:285` would fire and
  the section would be demoted from "drop" to "build" / "fill" / "break"
  on the energy signal alone.
- More directly, the bars-31-47 mean sits at the **11.2th percentile**
  of the full-track mix_norm distribution (full-track min 0.2183, max
  1.0000, median 0.8792, mean 0.6825). Only ~11% of bars on this track
  are quieter than the bars-31-47 mean. It is the most sustained
  low-energy region on the track, by a wide margin.
- A standalone `mix_norm < 0.40` for 12+ consecutive bars (the cleanest
  threshold from the sweep in section 2) trips exactly once on this
  track, at the trailing edge of the bar-31-47 dip. The single-bar
  fills at bar 23 and bar 55 are also below 0.40 but are 1 bar wide
  and are correctly excluded by the 12-bar run-length requirement. No
  other sustained low-energy region on the track trips the same rule.

The qualifications are:

1. **Threshold sensitivity**: any threshold in the 0.40-0.50 range with
   min_run >= 12 bars catches bars 31-47 and nothing else on this track.
   A tighter threshold (e.g. 0.60-0.80) starts to pick up trailing
   outro-tail regions near bars 146-151 (mix_norm drops there too as the
   track ends, but those are also already detected as a separate issue
   via the `outro_start` lead-drop logic in `Source/stem_detector.py:498`
   onwards and don't need the energy check). The cleanest single
   threshold for THIS track is 0.40 / 12-bar; a 0.50 / 16-bar rule is
   equally clean. Tuning would need a corpus-side replay before becoming
   a default.
2. **Min-run requirement is mandatory**: without it, even threshold
   0.90 / 1-bar catches 50+ bar-ranges on this track (every "between
   drops" lull). The 12-bar minimum is what makes this a break signal
   and not a phrase-amplitude signal.
3. **Boundary framing**: the standalone rule identifies the dip but
   doesn't, by itself, decide whether bars 16-22 and bars 48-152 should
   be one section with a hole in the middle or whether the bar-31-47
   region should be carved out as its own break section. That boundary
   decision is currently a job for the existing `_snap_merge` /
   `_assign_labels` flow and would need a small extension to consume a
   "sustained low mix_norm run" cue -- not a full rewrite, but more than
   just adding the rule.
4. **No false-positive cost on this corpus class that I can verify
   without re-running detection**: the prior diagnosis
   (`Documentation/Reviews/2026-08-18 Double Dutch Kick-Attenuation
   Diagnosis.md` Step 3) names the syncopated class and Delacour-class
   false-positive risks for any "OR of energy and V3" mechanism; the
   risk profile for a *standalone* mix_norm rule is different (no
   kick-detection interference at all), and I cannot validate that on
   one track. The standalone check would need a 109-track corpus sweep
   like the one the prior diagnosis called for, before becoming a
   default.

For this track specifically the mechanism is sharp: the bars 31-47
region is the only sustained, contiguous, low-mix-energy region on the
track, the magnitude of the drop (-63.9% from bars 28-30, -65.6% from
bars 48-49) is well outside the dynamic range of any other single
section, and a 12+ bar min-run check on a ~0.40-0.50 threshold catches
it without ambiguity.

---

## Sources

- `Test Project/14.08.26/_Stem Analysis/Sam Leagas - Double Dutch (Extended Mix) SW V1__stemenv.npz`
  (read; keys: `hop_t`, `drums`, `bass`, `other`, `vocals`, `mix`).
- `Test Project/14.08.26/_Stem Analysis/SECTIONS_STEM_Sam Leagas - Double Dutch (Extended Mix) SW V1.json`
  (read; full `sections` and `signals` used to compute per-section `ef`).
- `Source/stem_detector.py:64, 75-80, 268-309, 397, 408-409, 486, 612-700`
  (read; full file).
- `Source/stem_section_probe.py:38-41, 44-45, 67-110` (read; full file
  checked for the `envs["mix"] = _env(wav.mean(0))` line and the
  `_seccol` color table).
- `Documentation/Reviews/2026-08-18 Double Dutch Kick-Attenuation
  Diagnosis.md` (read; sub-band / broadband / per-beat peak numbers used
  for the drums cross-check; the `kick_on` / V3 context used to
  understand why V3 alone doesn't fire on this track).

Scratch (worktree root, NOT committed):
- `scratch_mix_envelope.py` -- the per-bar reproduction and the rule
  sweep in section 5.
