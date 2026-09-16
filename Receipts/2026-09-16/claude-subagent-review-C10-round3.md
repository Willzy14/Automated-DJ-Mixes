# C10 round 3 review - Claude subagent standing in for Codex (capped until 2026-09-19)

# Round 3 Review — TrackDirectAutomation / `_track_direct_automation`

I read the brief, then verified everything against real files myself (not the brief's or code's claims) - direct gzip+regex reads, running the actual module, and an Ableton manual lookup. Full test suite: **18/18 passed**.

**Q1 - Correctness.** The FloatEvent regex is right. Confirmed directly: Mechanism 2's `<FloatEvent Time="X" Value="Y" />` genuinely carries no `Id=` (checked a real GainLo block in Gbox Side 1), while Mechanism 1's carries `Id="N"` (checked a real AutomationEnvelope block in the Defected CD2 file) - the two regexes cannot cross-match each other's events even if scopes overlapped.

The doubled single-point check (`events_xml.count("<FloatEvent") <= 1` then `len(points) <= 1`) is **redundant in practice, not a bug**: raw tag count equals regex-matched pair count exactly in both real files I checked (1899=1899, 1429=1429), including FloatEvents with extra `CurveControl*` attributes the regex isn't anchored past. Ableton writes a sentinel anchor point at `Time="-63072000"` for nearly every automated parameter (1496 of 2200 blocks in Gbox Side 1 are *only* this anchor) - the filter correctly excludes these as static. One minor gap: a block with exactly 2 FloatEvents where both share the same value (anchor + one duplicate) passes the filter as "automation" though nothing moves - but `_summarize_curve` reports it honestly as "holds," and I confirmed across all 14 direct-rich files this pattern never accounts for a file's *only* data, so it doesn't affect any headline number.

Cross-contamination: **not happening**. I picked track 4 in Gbox Side 1, which has Volume+GainLo+Cutoff simultaneously, and matched the raw XML's own wrapping tags (`<Volume>`, `<GainLo>`, `<Cutoff>`) byte-for-byte against the classified buckets - exact match. Classification is exact-string equality, and I found a real near-miss (Utility device's `<Gain>` tag, value -7.5, in Tapesh Mix) that correctly does *not* get classified as `GainLo`.

**Q2 - GainLo dB conversion.** **The brief's own reference figure is wrong, but the code is right anyway - stronger evidence than the brief proposed.** I queried Ableton's Live manual directly: EQ Three's (FilterEQ3's) documented gain range is **"-infinite dB to +6dB"** per band - the ±15dB figure belongs to *Channel EQ's* shelf filters, a different device. The file's own `MidiControllerRange Max="1.99526238"` converts via the code's `20*log10(v)` to **+6.02dB**, matching Ableton's documented +6dB max almost exactly (1.99526238 ≈ 10^(6/20)). That's real independent confirmation the curve is correct, not an inference from "plausible-looking numbers."

**Q3 - Ground truth.** Reconstructed 5 real entries from raw XML independently of the module and matched the actual card text exactly: Gbox Side 1 track 4's fader (-17.0dB→0.1dB, 0.1dB→-22.0dB), bass EQ (-14.6dB→-0.1dB, -0.1dB→-17.7dB) including a raw-cutoff SNAP+spike sequence reconstructed line-for-line; Tapesh Mix tracks 1/2 bass EQ (0.0dB→-3.0dB, -5.1dB→0.0dB). All matched.

**Q4 - Coverage math.** Independently re-ran `extract()`+`find_transitions()` myself: zone_rich=5 (same 5 files), direct_rich=14 (same 14 files), disjoint, union=19, Gbox Side 3 is the sole exception, transitions=269 - all exact. Also independently recomputed via raw regex (bypassing the module entirely): point totals 1,212 (Mechanism 2) and 14,398 (Mechanism 1), and the docstring's "492 curves/26,822 points across 15 files" - all matched exactly.

Nothing found unasked that changes the verdict.

VERDICT: SOUND

---
**Disposition (2026-09-16, Claude):** the manual lookup result was independently corroborated -
it directly resolves MiniMax's honesty flag on the GainLo dB curve into an actual confirmation
(computed +6.02dB vs Ableton's documented +6dB max, near-exact match). Updated the module
docstring and INDEX.md to state this as CONFIRMED rather than "inferred, unverified." No code
changes needed - round 3's build is sound as written. This closes out C10 for this session:
19 of 20 files now have real, extracted, reviewed automation data across two confirmed
mechanisms.
