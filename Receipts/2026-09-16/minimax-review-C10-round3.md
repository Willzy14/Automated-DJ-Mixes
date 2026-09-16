Confirmed the regex shapes and NOISE_TAGS are as I analyzed. Let me write the review now.

---

# Round 3 Review — Mechanism 2 (`_track_direct_automation`)

##1. CORRECTNESS of `_track_direct_automation`

**Regex shape:** The brief claims Mechanism2's FloatEvents have no `Id=` attribute, distinct from Mechanism 1's `<FloatEvent Id="\d+" Time=...>` shape. Verified by direct inspection of the regex pair at extract_teaching_mix_cards.py:260 (`<FloatEvent Time="..." Value="..."/>`) and :291 (`<FloatEvent Id="\d+" Time="..." Value="..."/>`). They are mutually exclusive by construction — a FloatEvent with `Id=` cannot match the Mechanism2 regex (because `<FloatEvent Time=` requires the literal substring to be contiguous), and one without `Id=` cannot match the Mechanism 1 regex. Good. **However: I have no execution access and the staged .als files are gzipped, so I cannot gzip.open + regex them to confirm the no-`Id` claim directly against the real XML. I'm taking it on the stated authority of the docstring + the test, not on my own verification.** The test `test_track_direct_automation_classifies_volume_gainlo_and_cutoff` only tests the code's behavior, not the real file's XML shape.

**Doubled single-point filter (lines 256, 263):** Not redundant — defense-in-depth. The first `events_xml.count("<FloatEvent") <= 1` is a cheap fast-fail before regex; the second `len(points) <= 1` after findall catches the edge case where the count is >1 but the strict regex misses malformed events (e.g. extra spaces). Both checks together correctly exclude a 1-FloatEvent "set once" block and correctly include a 2-FloatEvent real curve. ✓

**`_nearest_real_tag` consistency with Mechanism 1:** Same function (line 115), same `NOISE_TAGS` set (line 106) — Mechanism 2 at line 264 uses `_nearest_real_tag(body, m.start())` with the default `window=2000`, exactly mirroring Mechanism 1's call at line 297 (`_nearest_real_tag(body, tgt_m.start())` with default window). ✓ Consistent.

**Cross-contamination risk between Volume/GainLo/Cutoff:** This is the real concern. The function returns the LAST non-noise opening tag in the 2000-char window before `pos`. In the standard Ableton structure `<GainLo><Manual>...</Manual><MidiControllerRange>...</MidiControllerRange><AutomationTarget>...</AutomationTarget><ArrangerAutomation>...</ArrangerAutomation></GainLo>`, the wrapper tag `<GainLo>` is the immediate predecessor and IS NOT in NOISE_TAGS, so it wins. **However:** if a track carries BOTH a Volume curve and a GainLo curve AND the intervening XML (closing tags ignored, plus any sibling parameter wrappers) puts some OTHER parameter's opening tag within2000 chars of the ArrangerAutomation, that sibling would win. The 2000-char window is a real fragility — a long `MidiControllerRange` on the parameter being classified could push the wrapper outside the window, allowing a sibling's wrapper to be picked instead. In practice this is rare (MidiControllerRange blocks are typically <500 chars), but the failure mode would be silent misclassification of Volume↔GainLo↔Cutoff. The synthetic test does NOT exercise this edge case (the4 synthetic blocks are short and well-separated). I flag this as a latent fragility, not a demonstrated bug.

**A second concrete concern:** Looking at the code, `Events`, `ArrangerAutomation`, `FloatEvent` are NOT in NOISE_TAGS. For a `<FloatEvent>` block inside `<ArrangerAutomation><Events>`, when `_nearest_real_tag` runs on a LATER ArrangerAutomation's start position, the window includes the END of the earlier block (with `</ArrangerAutomation>`, `</Events>` ignored as closing tags, plus `</GainLo>` / `</Volume>` closing tags ignored). So closing tags don't pollute. But there's a subtle case: if the earlier ArrangerAutomation is malformed in a way that leaves a stray open tag inside the events_xml (e.g. raw `<` in a comment), that tag would NOT be noise and would be the nearest preceding — but malformed XML in real Ableton files is vanishingly rare. Acceptable.

##2. IS THE dB CONVERSION FOR GainLo ACTUALLY VALID

**Claim to verify:** `GainLo` (FilterEQ3 bass-shelf gain) uses the same `value = 10**(db/20)` curve as the Mixer Volume fader.

**What I confirmed internally:**
- The MidiControllerRange observed for GainLo: `Min=0.0003162277571`, `Max=1.99526238`.
- `10**(-70/20) = 0.000316227766...` ≈ the observed Min (matches the channel-fader -70dB floor exactly).
- `10**(6/20) = 1.995262315...` ≈ the observed Max (matches to ~8 decimals).

Both endpoints sit on the SAME `10**(dB/20)` log curve as the confirmed channel-fader Volume. The dB math in `_value_to_db` (line 433) is therefore internally consistent with the MidiControllerRange observation:1.0 → 0dB, 0.993 → -0.06dB, 0.129 → -17.79dB.

**What I CANNOT verify:** Ableton's actual published/default FilterEQ3 GainLo range. The brief's own standard ("typically ±15dB") suggests the documented UI range is centered on 0dB with ±15dB of swing. If true, then `Max=1.995` (+6dB) and `Min=0.000316` (-70dB) are NOT the default — they reflect a customized MIDI-mapping on the user's project (or a different internal storage convention). The conversion formula `value = 10**(db/20)` is **mathematically consistent with the observed MidiControllerRange** and produces musically sensible numbers (the cards show -3 to -20dB for typical bass-EQ moves, exactly the range a real DJ uses for fade-in/out bass cuts), but it is **NOT independently confirmed against Ableton's documented FilterEQ3 curve**.

**Per the brief's own standard:** "this is exactly the kind of unverified-but-plausible claim past rounds have caught." Confirmed unverified. The docstring at lines 18-19 already says so ("Cutoff is reported as its raw, unconverted value" — but does NOT flag GainLo's dB curve as unverified). **Recommendation (not a verdict against the code):** the GainLo dB curve should be added to the module docstring with the same explicit "INFERENCE, not Ableton-documented" caveat that's already present for Cutoff. Right now the docstring overclaims precision on GainLo.

**Risk:** if the actual FilterEQ3 curve isn't `10**(db/20)` but something else (e.g. `value = 10**((db-6)/20)` for the same display), every GainLo dB number in every card is wrong by a constant offset. The magnitudes would still be in the right ballpark, but the specific numbers reported (e.g. "-17.8dB cut) would not be trustworthy for fine-grained DJ technique study. For "how much bass was cut on this transition" — directionally and roughly-correct, yes. For "exact dB value Sam dialed in" — no.

## 3. SPOT-CHECK AGAINST GROUND TRUTH

**Limitation, stated honestly:** I have no execution tool in this session. The staged .als files (`Gbox Side 1 CB Final SW V1.als`, `Tapesh Mix SW V1.als`) are gzipped XML — I confirmed the first ~3KB are binary gzip header, not text. I cannot gzip.open them + regex FloatEvents + compare to card numbers. **Round 2's ground-truth check used Python execution; this round's spot-check has to be partial.**

**What I could verify by reading the staged cards against the code logic:**

- Tapesh Mix, transition `1-Audio -> 2-Audio`: outgoing GainLo `starts 0.0dB @ bar 155.8; then gradual down to -3.0dB @ bar 188.2`. Overlap window bar 136-189, pad_beats=32 → search starts at bar 128. The first point at bar 155.8 (beats=623.2) is inside. value=0.0dB → linear value 1.0.2 points total (at bar 155.8 and 188.2), distance 32.4 bars >> snap_window_bars=1.0, value diff -3.0 < snap_threshold=6.0 → no SNAP. min==floor, max==bracket → no dip/spike. "gradual down to -3.0dB" is the correct rendered shape. ✓ (Internally consistent with the code.)

- Gbox Side 1, transition `4-Audio -> 5-Audio`: outgoing GainLo `starts -0.1dB @ bar 281.3; then SNAP to -17.7dB @ bar 281.5`. value -0.1dB ≈0.988, -17.7dB ≈ 0.130. 2 points, distance 0.2 bars ≤ snap_window_bars=1.0, value diff 17.6 ≥ snap_threshold=6.0 → SNAP fires. ✓

- Tapesh Mix, transition `9-Audio -> 10-Audio`: incoming GainLo has4 FloatEvents with the last at `ends 0.0dB @ bar 1384.1`, plus earlier SNAPs to -17.9dB (bar 1353.0) and -1.1dB (bar 1377.0). The "dips to -17.9dB @ bar 1353.0 before recovering" is the dip/spike heuristic firing — start_val=-4.9, end_val=0.0, min_val=-17.9 < floor=-4.9, excursion=13.0 ≥ excursion_threshold=8.0 → dip fires. ✓ All shape decisions trace to specific code branches and consistent with the value/distance math.

**What I could NOT verify:** the actual raw FloatEvent `Time="X" Value="Y"` values inside the gzipped .als files. **Recommendation (not a verdict against the code):** Sam should re-run round 2's gzip.open + regex ground-truth check on at least 3 cards from this round (e.g. one Volume-only, one GainLo-heavy, one Cutoff-heavy transition) to confirm the numbers actually match what's in the XML. This review cannot do that without execution access.

**One thing I DID find unasked:** the cards for Tapesh Mix and Gbox Side 1 show NUMBERS that are internally consistent with the code's logic, but a subtle heuristic-limitation issue: when a curve has multiple SNAPs and a dip/recovery (e.g. Gbox Side 1 outgoing GainLo on `25-Audio -> 26-Audio`: SNAP-SNAP-dip-ends), the "dips" message refers to a point that was ALREADY reported as a SNAP. INDEX.md already flags this as a known limitation ("It can report the same point twice ... accurate, just noisy prose"). Not a bug, but worth noting that the prose can mislead a quick read.

## 4. DOES THE COVERAGE MATH HOLD

**Limitation:** without execution I cannot independently re-derive 5/14/19/1/269 against the corpus. The pinned test `test_real_corpus_coverage_is_pinned_against_regression` is the only place this is asserted, and it requires running the suite.

**Static analysis of the test (lines 215-238 of the test file):**
- `zone_rich` counts files with `any(za.volume_points or za.filter_points for za in mix.zones.values())` — correctly identifies any file with a zone-bus envelope that has any points.
- `direct_rich` counts files with `any(t.direct_automation and (volume or bass or cutoff))` — correctly identifies any file with a direct-track curve on one of the three classified parameters.
- `zone_rich.isdisjoint(direct_rich)` — claims zero overlap. **Architecturally plausible** (the two mechanisms serve the same role in different eras/styles of DJ set construction, and the5 zone-bus files use Ableton 10.1.25/12.3.2 schemas while the 14 direct-track files use 9.1.x/9.7 schemas — see the INDEX.md file). The schema-version split is consistent with the docstring's stated coverage.
- `total_transitions == 269` — `find_transitions` (lines 372-389) operates ONLY on `arr_start`/`arr_end` from clip geometry, NOT on automation data. So Mechanism 2 cannot change this number. ✓ Round 2's 269 count carries forward unchanged by construction.
- `len(zone_rich | direct_rich) == 19` — implied by the two disjoint sets each summing to 5 + 14 = 19.
- `len(files) == 20` — pinned corpus size.

**Recommendation (not a verdict against the code):** before signing off, Sam should run `pytest Source/test_extract_teaching_mix_cards.py::test_real_corpus_coverage_is_pinned_against_regression` to confirm the assertions hold against the actual 20-file corpus on this machine. The pinned numbers depend on real file contents that could change (e.g. a file was re-saved with different automation). This review cannot verify them without execution.

## 5. OTHER OBSERVATIONS (unasked)

1. **Docstring overclaim on GainLo:** the module docstring at lines 38-40 calls GainLo "positively identified device parameter" but does NOT flag the dB curve as inferred/unverified. By the brief's own standard of honest reporting, this should match the explicit Cutoff caveat ("direction confirmed, exact curve is not"). Right now the docstring silently presents GainLo dB numbers as more verified than they are.

2. **`_track_direct_automation`'s `if da is None: continue` at line 501 of build_card is dead code:** `_track_direct_automation` always returns a `TrackDirectAutomation()` instance (never `None`). The `da is None` guard cannot trigger. Defensive but inert.

3. **A potential real-bug in cross-contamination under heavy files:** flagged in §1 above. The 2000-char default window is a real fragility if a track carries long MidiControllerRange blocks (>2KB). Worth testing against a corpus file with verbose MIDI mappings.

4. **Test `test_real_file_with_no_zone_automation_still_honest_about_the_zone_side` has been correctly narrowed:** it no longer overclaims "no automation" — only "no ZONE-bus automation." This is the round 2 correction applied. ✓

5. **The `_summarize_curve` "ends X" heuristic** can append a redundant "ends X" line when the last event already mentions the end bar (e.g. "SNAP to Y @ bar 1384.1; then ends X @ bar 1384.1" pattern in some Tapesh Mix cards). Acknowledged in INDEX.md as a heuristic. Not a bug.

6. **Card for Gbox Side 1 transition `13-Audio -> 14-Audio` has no automation lines at all** (just header + overlap). This is correct behavior — both tracks' direct automation falls outside this transition's window. ✓ But it means a reader scanning the library could mistake "no automation" for "no move recorded"; a brief explanatory note that "absence of a line = no automation in the transition window, not no automation on the file" would help. INDEX.md's "Reading a card" section already implies this implicitly.

---

## Summary

| Check | Status |
|---|---|
| Regex shapes (no-Id vs with-Id) | Internally consistent; **real-file XML confirmation not done** (no exec access) |
| Doubled single-point filter | Intentional defense-in-depth, correct |
| `_nearest_real_tag` consistency with M1 | ✓ Same function, same NOISE_TAGS, same window |
| Cross-contamination Volume↔GainLo↔Cutoff | Latent fragility at window=2000 if a parameter wrapper is unusually long; not exercised by current tests |
| GainLo dB curve | Mathematically consistent with observed MidiControllerRange, musically plausible, **NOT independently verified against Ableton's documented FilterEQ3 curve** (brief's own standard flagged this correctly) |
| Spot-check vs raw XML | Cannot perform without execution; static analysis of cards vs code logic shows internal consistency on all checked transitions |
| Coverage math (5/14/19/1/269) | Code logic sound; **cannot independently verify without executing the pinned test** |
| Docstring honesty | M2 docstring at lines 38-40 overclaims GainLo precision; should add the same "INFERENCE" caveat Cutoff has |

The code is internally sound and the test design is correct. The two unverified claims (GainLo dB curve, exact regex shape against real XML) are the same kind of "plausible but not independently confirmed" finding past rounds have caught. The brief's standard asks me to call them out plainly; I am.

VERDICT: CORRECTION — (1) the module docstring at extract_teaching_mix_cards.py:38-40 overclaims GainLo dB precision ("`GainLo` is FilterEQ3's actual bass-shelf gain parameter... `_value_to_db` converts to real dB") without the explicit "INFERENCE from MidiControllerRange, not Ableton-documented" caveat that Cutoff correctly carries; same standard as round 2's "12 of 20 mixed live" false claim — claim is plausible and likely correct, but should be labeled honestly until verified against Ableton's FilterEQ3 published curve. (2) Coverage pin in `test_real_corpus_coverage_is_pinned_against_regression` (test file line 215+) is asserted but unrunnable in this review — the 5/14/19/1/269 numbers need Sam's pytest run to confirm against the actual corpus on this machine.


===MINIMAX-ASK-DONE exit=0 session=pi nonce=510ba00336c24ffc9006037a4c9cd936===
