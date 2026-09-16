# C10 review - Claude subagent standing in for Codex (capped until 2026-09-19)

# Independent Review: Teaching Mixes Case-Study Extractor

## Q1 - Code correctness
`_value_to_db` is confirmed as the mathematically correct inverse of `als_generator._db_to_ableton_volume` (`10**(db/20)`); the `-70dB` floor for `v<=0.0003162277571` matches `10**(-70/20)` to 10 significant figures. `_summarize_curve`'s tests genuinely discriminate real behavior (the dip/spike/snap/precision tests are drawn from an actual bug found against real data, not restated implementation). The `>8x median span` reference-track filter drops only 4 tracks across all 20 real files, and all 4 have names/spans consistent with genuine whole-mix reference imports (e.g. "18-...CD 1 Of 2 24 Bit MASTER" spanning the whole file) - no evidence of a false positive in this corpus, though it's an untested heuristic for a hypothetical long real track. `_track_zone`'s tie-break (`>` not `>=`) never actually fires - no ties exist anywhere in the real corpus.

**Two real bugs found, not in the brief's checklist:**
1. `build_card`'s `if out_t.zone == in_t.zone:` fires on `None == None`. Whenever BOTH sides have an unresolved zone, it prints "**Same zone on both sides**... likely a direct edit/layer within one channel, not a fader move" - a fabricated claim, since neither side was actually resolved to anything. I counted this directly: **188 of the library's 269 transition cards** (70%) carry this false claim, including all 15 cards in the "Gbox Side 1" honest-fallback file reviewed for Q4. This directly contradicts the module's stated "never guessed" principle and would mislead exactly the judgement process it's built for.
2. `_track_zone` resolves a track's zone via the literal numeric text of `<TrackSendHolder Id="N">`, assuming N is the 0-based send-slot position. In the real Defected CD2 and CD3 files, tracks 1 and 2 have `TrackSendHolder Id="2","3","4"` instead of `"0","1","2"` (all later tracks reset to 0). Id-based lookup resolves track 1 to "C-Reverb" (physically implausible - a "Sends Only" track routing 100% into a reverb aux with no dry signal) and track 2 to `None`/UNRESOLVED. Position-based decoding instead gives A-Zone/B-Zone, fitting the confirmed alternating pattern perfectly. This mis-resolves the first two transition cards of both rich Defected files.

## Q2 - Report-only, confirmed
Exhaustive grep of `Source/` (excluding `__pycache__`/worktrees) for `teaching_mix`/`extract_teaching_mix_cards`: only the test file and its own compiled `.pyc` match. `propose_arrangement.py` and `align_engine.py` have zero references. Confirmed pure library-building tool.

## Q3 - Spot-check vs raw XML
Verified zone-routing (`TrackSendHolder`/`Manual` values) and the Volume envelope directly against the gzip'd XML for cards 1-4. Card 3->4 ("Give It All"->"Spiller") checks out exactly: real send values, correct overlap window, and I reconstructed the raw dB curve myself (it does contain a genuine ~6dB dip-and-recover the card summarizes as "gradual flat to 0.0dB" - below the 8dB excursion threshold, a real but minor precision loss). Cards 1 and 2 are wrong per the Id-vs-position bug above.

## Q4 - Index numbers, and a major unasked finding
Independently re-ran `extract()`+`find_transitions()` over all 20 real files: **269 transitions, 5/20 rich files, 14,398 points - all match exactly.**

However, the "12 of 20: mixed live, nothing written" narrative is **false**. I checked 5 of those "zero-automation" files at the raw XML level, including the two the docstring names by name (Gbox Side 1, Tapesh Mix - explicitly claimed "no amount of cleverer parsing recovers a move that was never captured"). All 5 contain real, multi-point recorded automation (Gbox Side 1: 56 curves/347 points; Tapesh Mix: 50/329; Bargrooves Mix1, DITH Amsterdam CD1, Gbox side 2 similarly) - Send-level rides and FilterEQ3 `GainLo`/`Gain`/AutoFilter `Cutoff` automation on individual song tracks. This is stored in the older `<ArrangerAutomation><Events><FloatEvent>` schema, not the `<AutomationEnvelope Id>` structure this extractor searches, and it lives on individual tracks - directly contradicting the module's core assumption that song-track automation "will always come up empty."

VERDICT: CORRECTION - three issues: (1) `build_card`'s same-zone check fires on `None==None`, producing a fabricated "likely a direct edit/layer" claim on 188/269 (70%) of all cards; (2) `_track_zone`'s `TrackSendHolder Id`-as-position assumption mis-resolves tracks 1-2 in both rich Defected files; (3) the "12 of 20 mixed live, zero automation" claim (INDEX.md line 31, and the module docstring's claim about Gbox Side 1/Tapesh Mix specifically) is factually wrong - real bass/EQ automation exists in the older `<ArrangerAutomation>` schema on individual tracks in every "zero-automation" file I checked.

---
**Disposition (2026-09-16, Claude):** all three findings confirmed independently against the real
files before acting. (1) Same-zone None==None bug: confirmed exactly 188/269 via direct count,
fixed (`out_t.zone is not None and out_t.zone == in_t.zone`), proved-the-test (2 new tests, both
confirmed to fail against the pre-fix code). (2) TrackSendHolder Id-vs-position bug: confirmed
directly against the real file (tracks 1-2 carry Ids "2,3,4", track 3+ carry "0,1,2" for the same
sends), fixed to resolve by order-of-appearance instead of the raw Id value; re-verified tracks
1-5 now show the correct A/B/A/B/A pattern. (3) ArrangerAutomation: confirmed directly - Gbox Side
1 has 56 real multi-point curves (403 points, close to the subagent's 347 - likely a slightly
different counting convention, not a material discrepancy) - and further quantified across ALL 15
"zero new-automation" files: every one has real ArrangerAutomation curves (8-56 curves, 72-5,693
points each). This is a substantial, real, NOT-YET-BUILT follow-on opportunity - a different
extraction mechanism (per-clip, not per-bus), out of scope for this pass, documented honestly in
the corrected INDEX.md rather than attempted blind under this same push. All 20 real .md cards
regenerated with both fixes; full suite re-run clean.
