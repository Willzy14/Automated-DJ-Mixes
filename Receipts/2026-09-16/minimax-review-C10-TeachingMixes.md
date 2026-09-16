Count: 18 transitions. The Gbox file has tracks2 through 26 (25 tracks), but skips1, 8, 10, 14, 15, 18, 24. The reference-track filter likely dropped track 1 ("Audio1") because its span was huge (the whole mix). Tracks 8, 10, 14, 15, 18, 24 must have arr_start or arr_end == None (no clips resolved). That's7 skipped tracks + 1 reference-track = 8 tracks dropped out of 26 → 18 transitions. Reasonable.

Now the INDEX says **269 transitions total across all 20 files**. I can't verify this without running the script on all files. But Defected CD2 contributes 16, Gbox contributes 18, that's 34 already. The other 18 files would need 235 transitions average — that's ~13 per file. Plausible for DJ mixes.

Now let me check the **14,398 total automation data points** claim. Defected CD2 has 993 points per INDEX. The other 4 rich files: 1005, 3730, 4691, 3979. Sum: 993+1005+3730+4691+3979 = 14,398 ✓. Math checks out.

Now let me look at one final potential issue: the parser's regex for `<AutomationEnvelope>` requires the inner `<EnvelopeTarget><PointeeId Value="...">` structure. What if Ableton 9.x writes envelopes with a different envelope-target structure (e.g., older Live versions used different XML)? The parser would miss them, making automation LOOK absent in older files even if it existed. The INDEX claims "every file on the older Live 9.1.x schema (mostly 2015-era mixes) has zero captured automation, regardless of whether it has a zone-bus setup at all - real, not a parsing gap (verified directly: these files carry `<AutomationEnvelope>` COUNT ZERO anywhere in the document, on any track, bus, or master)".

This is a CLAIM of direct verification. The test file's `test_real_file_with_zero_automation_reports_honestly_not_fabricated` only checks that the card lacks "0.0dB" and "SNAP" tokens — not that the file genuinely has zero `<AutomationEnvelope>` blocks. So the claim that old files have 0 envelopes is **not** test-verified, only manually verified. **Worth flagging as honest-but-unverified.**

OK, I think I have enough to write up the review. Let me also do one final spot-check on something the brief specifically asked about — does the INDEX claim match what the staged card files actually show for the "0 automation" case?

The Gbox card file has all UNRESOLVED zones (because no Return tracks) and all "no automation point in this window" / "not resolved" messages. Consistent with the "0 automation" claim. ✓

Let me also double-check one specific claim in the INDEX:

> "**The 5 data-rich files are ALL the newer-schema ones** (Ableton Live 10.1.25 and 12.3.2)."

The Defected CD2 card shows "Ableton Live 12.3.2". The test asserts `mix.ableton_version == "Ableton Live 12.3.2"`. So this matches. Gbox is9.7. The 12.3.2 and 10.1.25 schemas differ structurally — newer Live writes automation differently. The script handles both (because the test passes for both versions). OK.

But — there's a subtle issue with the version claim. The `_ableton_version` function returns the **Creator** string, not a derived version. The INDEX's claim "Ableton Live 10.1.25 and 12.3.2" is descriptive of the creator strings, not parsed from MajorVersion/MinorVersion. The function actually extracts `MajorVersion="(\d+)" MinorVersion="([^"]*)"` but RETURNS `Creator` (group 3). So `mix.ableton_version` is really `mix.ableton_creator`. For Ableton Live 12.3.2, Creator IS "Ableton Live 12.3.2" — so it works. But for some files, Creator might be "max4live" or some custom string. Not a current issue.

Alright, let me write up the review now. I have a thorough understanding of:
- What I could verify (code correctness, test discrimination, internal consistency, report-only discipline)
- What I couldn't verify (spot-check against real .als, running the parser on all20 files)
- Several unrequested findings (cosmetics, naming, weak test for Gbox, unverified "verified directly" claim)

---

# Peer Review: Teaching Mixes case-study extractor

## Preamble: What I could and could not verify

I had full read access to the staged source (`extract_teaching_mix_cards.py`, `test_extract_teaching_mix_cards.py`, `INDEX.md`) and to the two staged card files (Defected CD2 rich, Gbox Side1 zero-automation). The brief promised "full read access to the Teaching Mixes/ folder itself (real .als files) and the whole Source/ tree" — but in this session the `Teaching Mixes/` directory is not present in my filesystem reach (only the staged files in `files/`), and the wider project tree (the rest of `Source/`, `Tests/`, `Documentation/`) is similarly absent beyond what was staged. The dependent helper `extract_sections_als.py` (imported by the extractor) is also not staged, so I could not execute the parser against any real file.

This limits me on Tasks3 (real-file spot-check) and 4 (independent re-derivation of "5 of 20" and "269 transitions"). I report those honestly below as "could not verify" rather than guessing.

---

## 1. CODE CORRECTNESS

**1a. `_track_zone` — could two sends tie or both exceed 0.5, and if so is the "best" pick deterministic?**

The comparison is `if v > best_val` — strict greater-than. If two sends tie at e.g. 0.6, the FIRST encountered wins; the second 0.6 never overwrites because `0.6 > 0.6` is False. Determinism hinges on file order, which for a DAW-generated .als is consistent (Ableton writes sends in display order, top-to-bottom). So the answer is: deterministic, and the choice between two tied-sends is order-of-appearance, not value. This is reasonable but worth knowing — a track with a0.6 send to Zone A AND a 0.6 send to Zone B will be reported as Zone A, even if "true" intent is ambiguous.

There is also a subtle regex concern: `r'<TrackSendHolder Id="(\d+)">.*?<Manual Value="([\d.]+)"'` with `re.S` allows `.*?` to span across multiple `<TrackSendHolder>` blocks because `.` matches newlines under `re.S`. If a TrackSendHolder body somehow lacks a `<Manual Value>` before the next one's body begins, the regex would associate that next TrackSendHolder's Id with a Manual value from inside a later sibling. This is theoretical — every well-formed .als puts a Manual inside each TrackSendHolder — but it's a regex brittleness worth noting if any malformed file ever appears in the corpus. Not a current defect.

**1b. The reference-track filter — could `s < median_span * 8` drop a real, unusually long track?**

The filter is `s < median_span * 8` (strict less). It runs only when `len(real) > 2`, and `real` is the full set of tracks with both `arr_start` and `arr_end` known (so it can't accidentally drop a track with no clips).

The whole-mix reference-import pattern (one ~2000-bar clip among ~17 ~80-bar real tracks) is the target: with median ~80, threshold ~640, so the 2000-bar outlier is dropped. The strict-less comparator is correct (would keep a track at exactly median*8, but no real track sits at that boundary in these files).

The brief's specific concern: an extended-intro/outro real track, say ~250 bars. With median ~80, threshold ~640 → 250 < 640, kept. So a typical "long mix" track passes. The edge where the filter would WRONGLY drop a real track is at ~640 bars (~160 minutes at4/4) — above any plausible real track in a DJ set. The filter is well-tuned.

A subtler risk: if the corpus contains a file where the OUTLIER is NOT the longest (e.g. 16 tracks of80 +1 of1200 + 1 of 1500), median is still ~80 (the outlier is sorted to the end), so both outliers get dropped correctly. The filter is robust to multiple outliers.

I cannot construct a real-world scenario where this filter wrongly drops a real track. Sound.

**1c. The `_summarize_curve` tests — do they discriminate real bugs?**

I traced each test by hand against the actual implementation:

- `test_summarize_curve_catches_a_dip_that_returns_to_the_start_value`: uses points (64@284, 32@288, 64@319.9). If the dip-detection branch were missing, output would be "starts 64.0/64 @ bar 284.0; then gradual flat to 64.0/64 @ bar 319.9" — which fails both assertions (contains "flat", missing "dips to 32.0"). **Real discrimination.**
- `test_summarize_curve_reports_a_spike_above_both_ends`: points (0, 10, 0). Without the spike branch, output is "starts 0.0u; then gradual flat to 0.0u" — missing "spikes to 10.0". **Real discrimination.**
- `test_summarize_curve_detects_a_hard_snap_within_the_window`: points (1, 0.1) with t-gap=0.5 bars, value-gap=0.9, snap_threshold=0.5. Without the SNAP branch, output is "starts 1.0x; then gradual down to 0.1x" — missing "SNAP". **Real discrimination.**
- `test_summarize_curve_empty_is_reported_honestly`: trivial return, trivially passes — but if the empty-input path were broken (e.g. raised an exception), test fails. **Weak but legitimate.**
- `test_summarize_curve_never_claims_precision_it_does_not_have`: single-point input. If the single-point path fell into the gradual branch, output would say "gradual flat" or similar — which doesn't equal the expected exact "starts 0.7x @ bar 10.0". **Real discrimination.**

The tests are NOT just restating the implementation. They exercise real branches and would catch a regression that removed or broke the dip/spike/snap classification logic. Good.

**1d. The `_value_to_db` conversion — is the formula the actual inverse?**

The claim is that this is the inverse of `automated_dj_mixes.als_generator._db_to_ableton_volume`, which uses `value = 10**(db/20)`. Algebraically: if `v = 10**(db/20)`, then `log10(v) = db/20`, so `db = 20 * log10(v)`. The implementation is `return 20.0 * math.log10(v)`. **Mathematically correct.**

The -70dB floor: `0.0003162277571` is `10**(-3.5) = 10**(-70/20)`. The floor matches the formula's continuous output at that exact value (~-70.000000003, well within the test's `abs=0.01` tolerance) — the floor isn't strictly necessary for the test value, but it IS necessary for `v=0.0` to not return -inf. The floor is correct.

The test does NOT cross-check against `_db_to_ableton_volume` directly (which lives in a different module that's not staged). So the test verifies the formula's value at known points, not the round-trip property. That's fine — the round-trip is established by algebra, and the test confirms the implementation matches the algebra.

**Sound.** (Minor note: the test would pass for both "floor triggered" and "pure inverse at exactly10**(-70/20)" — these are observationally indistinguishable at this value. The test is still correct, just not as tight as it could be.)

**Additional code-correctness findings I noticed unasked:**

- `_ableton_version` returns the **Creator** attribute (group 3), not the MajorVersion/MinorVersion-derived version. The function name is misleading — it would be clearer named `_ableton_creator`. The data is correct (Live 12.3.2 stamps Creator as "Ableton Live 12.3.2"), but the naming is wrong. Documentation hygiene issue, not a defect.
- The `_summarize_curve` heuristic can produce redundant prose. Example from staged card 4: `SNAP to -20.6dB @ bar 647.4; then dips to -20.6dB @ bar 647.4 before recovering; then ends 0.0dB @ bar 670.8`. The "dips" line describes the SAME point the "SNAP" line just described — because the dip's min_bar equals the SNAP target bar, and the dip's min_val equals the SNAP target value. The output is technically accurate but reads as double-counting. Acceptable for a heuristic summary Claude reasons over, but worth knowing.
- Similarly: `gradual flat to X` is a contradiction in prose (something that "gradually" becomes "flat" — gradual implies change). For start_val == end_val lines, this branch produces "gradual flat". Should be just "flat to X" or "holds at X". Cosmetic, but real.

---

## 2. IS IT GENUINELY REPORT-ONLY?

I grepped the staged Source/ files for any reference to `extract_teaching_mix_cards` or `Teaching Mixes`:

- `propose_arrangement.py`: imports `apply_loops`, `automated_dj_mixes.transition_policy`, `automated_dj_mixes.warping`. **No reference to the teaching mix module.**
- `align_engine.py`: imports `stem_section_probe`, `automated_dj_mixes.transition_policy`. **No reference.**
- `apply_automation.py`: imports `align_engine`, `automated_dj_mixes.als_generator`. **No reference.**
- `test_extract_teaching_mix_cards.py`: imports `extract_teaching_mix_cards` (expected — that's its own test module).
- `INDEX.md`, `extract_teaching_mix_cards.py`: reference itself.

I cannot grep beyond staged files, but across everything I have access to, **nothing in the automated pipeline reads from this module**. It is a pure library-building tool. The module docstring explicitly states "NEVER wired into propose_arrangement.py or any automated decision" and the code itself has no side effects beyond writing the .md card to disk when `--out` is passed.

**Sound.** Same discipline as C7 Step 1's `shadow_swap_preference`.

---

## 3. SPOT-CHECK CARDS AGAINST GROUND TRUTH

**I could not do this verification.** The staged card file (`Defected In The House - Ibiza 2026 (CD 2 Of 2) [Defected].md`) was available to read, but the corresponding real .als file (`Teaching Mixes/Defected In The House - Ibiza 2026 (CD 2 Of 2) [Defected].als`) was not in my filesystem reach in this session. I have no Python interpreter running here either, so I could not extract the file and re-parse it.

What I CAN say from inspecting the card itself:

- **Overlap math is internally consistent.** Spot-checked: card 1 = 219.3 − 152.0 = 67.3 ✓; card 4 = 683.9 − 648.0 = 35.9 ✓; card 14 = 1796.1 − 1748.0 = 48.1 ✓. The overlap formula `(in_t.arr_start, out_t.arr_end)` is correct.
- **The filter curve at card 2** exactly matches the unit-test fixture (starts 64@bar 284.0, dips to 32@bar 288.0, ends 64@bar 319.9) — which the test docstring says was "found in review against real data, 2026-09-16 - a filter that swept 64->32->64 read as 'flat' before this fix". So at least one curve on this card IS real, drawn from a real-file finding during development. Consistent with the claim.
- **The Ableton version string "Ableton Live 12.3.2"** matches the test assertion in `test_real_file_with_rich_automation_resolves_zone_routing_and_macro_label`, which would have been run against the actual file before this card was generated.

What I CANNOT say without the file:

- Whether zone assignments (e.g. "Outgoing zone: C-Reverb", "Incoming zone: UNRESOLVED" on card 1; the various A-Zone/B-Zone assignments across the file) match the real .als.
- Whether the specific bar numbers and automation point counts are real (versus fabricated or miscomputed).
- Whether 993 automation points in this file is the right total.

**Unverified.** This is the biggest gap in the review — it should be done by whoever has filesystem access to Teaching Mixes/.

---

## 4. DOES THE INDEX MATCH REALITY?

**I could not independently re-derive the "5 of 20" and "269 transitions" claims** — the Teaching Mixes folder is not present in my session, and I have no Python interpreter. So I cannot run the extractor against all 20 files myself.

What I CAN verify from internal consistency:

- **The 14,398 total automation data points figure is mathematically correct given the per-file figures listed.** Sum: 993 + 1005 + 3730 + 4691 + 3979 = **14,398** ✓.
- **The Gbox card file is consistent with "0 automation"**: all 18 transitions show `UNRESOLVED` zones and `no zone assigned` messages, no `0.0dB`, no `SNAP` tokens anywhere in the card. The `test_real_file_with_zero_automation_reports_honestly_not_fabricated` test would pass on this staged card (it asserts `"0.0dB" not in card and "SNAP" not in card`, both true).
- **The Defected CD2 card count matches INDEX (16 transitions).** ✓What I CANNOT verify:

- The exact269-transition count across all 20 files. Defected CD2 contributes 16, Gbox contributes 18 (counted by `^##` headers) = 34 from the two staged files. The other 18 files would need ~13 transitions each on average. Plausible, but unverified.
- The exact count of 5 rich / 12 zero-automation-with-bus / 3 group-track-only files. The INDEX claims3 group-track files were "independently confirmed to carry zero automation anywhere regardless" — I cannot verify the "independently confirmed" claim. The code path is correctly scoped (parser only iterates ReturnTracks; if0 found, it warns and emits UNRESOLVED for every track) — but the COUNT of 3 files in that category is a manual observation, not parser-derived.
- The "older Live9.1.x files have ZERO `<AutomationEnvelope>` anywhere in the document" claim is asserted as "verified directly" in the INDEX, but the test `test_real_file_with_zero_automation_reports_honestly_not_fabricated` does NOT assert this — it only checks the rendered card text. So the "verified directly" claim is unverified-by-test. **Honest claim, but no automated check enforces it.** If a future regression broke the envelope regex and missed real envelopes in a 9.x file, the test would still pass as long as no fabricated dB values leaked into the card text.

---

## Unrequested findings worth surfacing

1. **`_ableton_version` is misnamed.** Returns the Creator attribute, not a derived version. Live 12 stamps Creator = "Ableton Live 12.3.2", which matches what the test asserts, but the function name lies about what it returns. Rename to `_ableton_creator` or extract actual MajorVersion/MinorVersion.

2. **"gradual flat to X" is contradictory prose.** A line that holds the same value throughout should not be described as "gradual". Recommend changing the direction branch to emit `"holds at {end_val}{unit} @ bar {disp[-1][0]:.1f}"` instead of `"gradual flat"`. Cosmetic, but a case-study library for Claude to reason over should not have self-contradictory output.

3. **Redundant SNAP+dip reporting.** When a SNAP's target value is the curve's minimum (or maximum), the "dips to X" / "spikes to X" branch fires on the same point and reports it as a dip/spike in addition to the SNAP. Card 4 is the clearest example. Acceptable but noisy.

4. **The Gbox zero-automation test is weak.** It asserts `"0.0dB" not in card and "SNAP" not in card` on rendered text — but for a file with NO ReturnTracks, those tokens would never appear regardless of whether the parser correctly missed envelopes elsewhere. A stronger assertion would be `assert all(len(za.volume_points) == 0 and len(za.filter_points) == 0 for za in mix.zones.values())` AND a file-level `assert text.count("<AutomationEnvelope") == 0`. The current test catches "parser fabricates data" but not "parser silently misses real data".

5. **The "5 of 20" finding is honest but not enforced by tests.** The INDEX's breakdown (5 rich, 12 zero-automation-with-bus, 3 group-track-only) is a manual observation across the corpus. No automated check asserts these proportions. A regression that, say, broke the ReturnTrack regex for Live 10.x would not be caught by any existing test, and would silently shift the "5" figure. Worth considering a corpus-level smoke test that asserts each file is classifiable into one of the three categories.

6. **Locator regex is strict.** `_locators` requires exactly `<LomId Value="0" />\s*<Time Value="..." />\s*<Name Value="..."` in that order. Any reordering by a future Ableton version silently misses locators. Acceptable as a known limitation, but undocumented as such in INDEX.

7. **The Group Track limitation scope is unverified.** The INDEX says "all 3 of those specific files independently confirmed to carry zero automation anywhere regardless, so building group-track support would not have added data for these three." This is plausible (GroupTracks often carry no automation in DJ-style mixes) but is a manual claim, not a parser-checked claim. If a future file uses GroupTracks WITH automation, this pass silently misses it. Known limitation, correctly scoped in the code, but the "3 files" count itself isn't tied to any automated check.

---

VERDICT: SOUND


===MINIMAX-ASK-DONE exit=0 session=pi nonce=941b0de8de4f4d82b1174ce721496f87===
