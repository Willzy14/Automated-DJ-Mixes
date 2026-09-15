# Tomorrow — Continuing from V12

## Where we left off

**V12 is generated and Sam is driving home with it.** Most transitions work cleanly with base-to-base alignment on phrase boundaries. The pipeline now has:

- Drop-confirmation kick detection
- Bass section detection (off-beat energy sampling)
- Phrase-aware break detection (16-bar grid scan, returns break_start AND break_end)
- Three alignment strategies: `bass_to_bass`, `tail_into_break`, `end_to_end`
- Phrase-grid snap (32-bar boundaries) for all swap points
- Multi-envelope merge per (track, param) so middle tracks get proper outgoing fades
- Master at -6dB
- Volume on Utility Gain, mixer fader free for manual tweaks
- Tempo automation across the mix

## Known issues going in

1. **Sapian transition in V12 is wrong** — Sapian has no bass detection. The fallback strategy picked `tail_into_break` but the result wasn't right. Need to diagnose what happened.
2. **0.5-beat drift on off-grid bass entries** — when incoming's bass_start is at a non-bar-aligned beat (e.g. 64.48 beats into audio), the phrase-snapped swap is ~0.5 beats off the actual audio bass entry. Fix: snap warp markers to round bass_start to nearest 4-beat multiple in clip time.
3. **Bass detection fails on tracks with very even bass energy** — Sapian, Detlef, Harry Romero return None. Threshold tuning may help, but the deeper fix is to use a different detection method for these (look for energy contrast between intro and main section, not absolute threshold).
4. **Phrase-aware break detection is brand new** — not yet validated against Sam's teaching mix ground truth. Need to run it against ADE Side 3 transitions and compare.

## Priorities

### Priority 1: Fix the Sapian-like cases
When `bass_to_bass` fails (no bass detection) and `tail_into_break` doesn't produce a clean result, what's the right strategy? Options:
- Detect outgoing's outro structure (beats-only vs with melody) and align outro END to incoming's first kick
- Use a smaller phrase boundary (16 bars instead of 32) for short overlaps
- Loop the outgoing's last 8-16 bars to extend it to a phrase boundary

### Priority 2: Warp anchor snap (kills the 0.5-beat drift)
When the bass_start or break_start sec value converts to non-integer beats, add an extra warp marker that maps the audio time to a clean 4-beat-aligned clip beat. Slight audio stretch (sub-1%), inaudible, fixes the drift.

Files: `warping.py` (add a third warp marker at bass_start), `als_generator.py` (write the extra marker)

### Priority 3: Validate break detection against teaching mixes
Use the existing `Source/extract_mix_points.py` and `Source/extract_inline_automation.py` to find where Sam actually placed breaks in ADE Side 3. Compare to `_detect_first_break_phrase_aware` output on the source audio files (available in `G:\Mix CD' Projects\Defected In The House - Amsterdam\ADE Side 3 Project\Samples\Imported`).

### Priority 4: Loop intro/outro when needed
Sam's "loop one side or the other" technique. When the natural alignment math leaves a gap (e.g., 8 bars off from a phrase boundary), loop part of either:
- Outgoing's last drum section (extend its tail)
- Incoming's intro (extend its filtered build-up)

This requires:
- Setting `LoopOn=true` on the clip XML
- `LoopStart` and `LoopEnd` defining the loop region in clip beats
- `CurrentEnd` extending past the audio duration
- Decision logic for which side to loop (whichever has cleaner material)

## Out of scope for now

- Clip fragmentation / micro-edits (76% of Sam's teaching mix clips are <16 beats — V2 territory)
- Smoother tempo automation ("1 BPM rise over 2 tracks" — Sam said later)
- Mastering integration (Sam said "another day")
- Acapella/vocal chop overlays
- Filter sweep skill (long_filter_blend exists but is opt-in)

## Where to read more

- `Documentation/MIXING_PATTERNS.md` — aggregate analysis of 20 teaching mixes
- `Documentation/AI_CONTEXT.md` — full context including Key Decisions
- `.github/ai-activity-log.md` — chronological session log
- `Source/automated_dj_mixes/skills/` — the 5 transition skills
- `Source/automated_dj_mixes/analysis.py` — bass + break + kick detection
- `Source/automated_dj_mixes/orchestrator.py` — strategy selector + phrase snap

## Quick re-run

```powershell
$env:PYTHONPATH = "C:\Users\Carillon\Wired Masters Dropbox\Sam Wills\0.1---GIT HUB---\Automated DJ Mixes\Source"
python -m automated_dj_mixes.orchestrator `
  --input "Test Project\New Test Mix\Audio" `
  --output "Test Project\New Test Mix\Output"
```
