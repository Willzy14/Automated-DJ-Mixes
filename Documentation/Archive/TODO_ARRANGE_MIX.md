# TODO — `/arrange-mix` skill + Mix Patterns Library

Picking up from 2026-05-20 session. Sections-detection pipeline is LOCKED IN. Now building the ARRANGEMENT phase.

## Sam's decisions confirmed this session

| Question | Sam's answer |
|---|---|
| Library location | `Documentation/Mix Patterns Library/` inside the Automated DJ Mixes repo (NOT Dropbox/Sam Wills/Mix Patterns Library) |
| Similarity matching | BPM + track arrangement (section structure) as primary signals |
| Learn from rejections | Yes — when Sam overrides Claude's proposal, record BOTH Claude's pick AND Sam's correction so Claude learns "don't pick X, prefer Y in this situation" |
| Auto-detect learning | Yes — every `/arrange-mix` invocation checks for Sam-edited versions newer than Claude's last proposal, runs LEARN step before proposing |
| Mode | Auto-propose (Claude does the work; Sam corrects; loop continues) |

## What V20 teaches us (the principle, restated)

The chops are the **lineup points**. Each transition has 2-3 "moments" where chops must align:

1. **Entry moment** — incoming `intro START` chop aligns with a chop on the outgoing
2. **Bass swap moment** (when possible) — a chop on both tracks lands at the same arr-beat, giving natural bass swap without automation
3. **Exit moment** — outgoing ends at a chop on the incoming

**Loops are mechanical glue.** Where a section's native length is shorter than the gap between two alignment moments, loop within that section to fill.

V20 has 9 transitions worth of this pattern, used as the initial training data.

## Pickup plan for tomorrow

### Step 1: Create the Mix Patterns Library skeleton

Location: `Documentation/Mix Patterns Library/`

Files:
- `MIX_PATTERNS.md` — human-readable Sam-style rules + Sam's direct edits ("always loop kick stinger at end of techno tracks", etc.)
- `pair_history.jsonl` — one machine-readable line per learned transition (initial 9 from V20)
- `genre_priors.json` — derived stats (computed as library grows; can start empty)
- `README.md` — structure docs

### Step 2: Extract V20's 9 transitions as initial training data

For each V20 pair, parse:
- Project name (Black Book x Defected V2)
- Outgoing + incoming track names
- BPM (out + in)
- Section structure (out + in — labelled chop list)
- Alignment chops used (entry, bass_swap, exit — may be null)
- Loops applied (outgoing tail, incoming intro)
- Position in mix (e.g. 1-of-9, 5-of-9)
- Mark as `source: "sam_v20_initial"` so we know these are baseline

Pair example (V20 Crusy → Sapian):
```json
{
  "project": "Black Book x Defected V2",
  "version_source": "Sections V20",
  "pair_index": 9,
  "pair_position": "9 of 9 (penultimate transition)",
  "bpm_out": 129.2,
  "bpm_in": 129.2,
  "out_structure": ["intro_1", "drop_1", "fill_1", "drop_2", "break_1", "drop_3", "fill_2", "drop_4", "break_2", "drop_5", "fill_3", "drop_6", "break_3", "drop_7", "outro_1"],
  "in_structure": ["intro_1", "drop_1", "break_1", "drop_2", "fill_1", "drop_3", "break_2", "drop_4", "fill_2", "drop_5", "fill_3", "drop_6"],
  "alignment": {
    "entry": "out.drop_7 START = in.intro_1 START",
    "bass_swap": "out.outro_1 START = in.drop_1 START",
    "exit": null
  },
  "loops": {"outgoing": null, "incoming": null},
  "source": "sam_v20_initial",
  "timestamp": "2026-05-20"
}
```

### Step 3: Build the `/arrange-mix` skill

Skill file: `~/.claude/commands/arrange-mix.md` (+ mirrors to Codex Brain, Antigravity Brain).

Auto-fire triggers: user mentions arrangement, mix construction, loop extension, `arrange_sections.py`, `apply_loops.py`, paths under `Sections V*.als` AFTER chops are locked.

#### Mode A: PROPOSE

1. Load locked Sections V\<N\>.als (output of `/section-detection`)
2. Load `Mix Patterns Library/pair_history.jsonl`
3. For each pair:
   - Find similar pairs in history (BPM ±2 + section structure shape match)
   - If matches exist with confidence: apply their alignment pattern
   - Else: fall back to defaults (entry = outgoing's last drop START, exit = incoming's first significant chop, bass swap = chop coincidence if any)
4. Compute required loop extensions to fill gaps
5. Generate `Sections V\<N+1\>.als` with positions + loops applied
6. Output `ARRANGEMENT_V<N+1>.md` showing per-pair decisions for Sam to review

#### Mode B: LEARN (auto-detect)

On every invocation, first:
1. Check: is there a `Sections V<M>.als` newer than the last Claude proposal?
2. If yes (Sam edited): diff the proposal vs Sam's edit
3. For each pair where alignment changed:
   - Record Claude's pick + Sam's correction as a new entry in `pair_history.jsonl`
   - Mark `source: "sam_correction"`, include `claude_proposed` and `sam_chosen` fields
4. Append to `MIX_PATTERNS.md` if a high-confidence rule emerges (same correction repeated 3+ times)

### Step 4: Build the arrangement + loop application code

Already have:
- `Source/arrange_sections.py` — repositions tracks by shifting all clips (used for V19)
- `Source/apply_section_corrections.py` — patches specific chop boundaries

New code needed:
- `Source/apply_loops.py` — applies loop extensions to a Sections .als
  - Takes `LOOPS_V<N>.json` specifying per-track loop additions
  - For outgoing tail loop: append N copies of a 1-bar source range as new AudioClips after the last natural clip
  - For incoming intro extension: insert N copies of a source range at the START, shift all other clips later
  - Reuses XML patching approach from `apply_section_corrections.py`

- `Source/match_patterns.py` — given a pair (out structure + in structure + BPMs), query `pair_history.jsonl` for similar past pairs
  - Similarity score: BPM match (weight 0.3) + structure shape match (weight 0.7)
  - Returns top-N similar pairs with their alignments

- `Source/propose_arrangement.py` — orchestrator for Mode A
  - For each pair: call match_patterns, decide alignment chops, compute loops, output ARRANGEMENT JSON

- `Source/learn_from_correction.py` — Mode B
  - Diff two .als files at the per-track level
  - Extract alignment changes
  - Append to pair_history.jsonl

### Step 5: Test end-to-end

1. Start from `Sections V18.als` (Sam's chops, no arrangement)
2. Run `/arrange-mix` → should produce `Sections V21.als` proposed
3. Compare to V20 — how close did Claude's V20-pattern-driven proposal get?
4. Document the gap as initial "things to improve"

### Step 6: Skill documentation + brain mirroring

Standard:
- `~/.claude/commands/arrange-mix.md` (canonical)
- `Dropbox/Sam Wills/Codex Brain/commands/arrange-mix.md`
- `Dropbox/Sam Wills/Antigravity Brain/commands/arrange-mix.md`
- Add `/arrange-mix` to "Auto-Fire Skills" section in CLAUDE.md / AGENTS.md / GEMINI.md
- Add to project's `.github/copilot-instructions.md`

### Step 7: Update Documentation/AI_CONTEXT.md

Add Key Decision: "Mix Patterns Library architecture — cross-project learning via Documentation/Mix Patterns Library/, BPM + structure similarity matching, learns from rejections, auto-detects Sam corrections on every skill invocation."

Update "Current State" to reflect /arrange-mix skill operational.

## Open implementation questions to figure out tomorrow

1. **How does the similarity score actually work?** "Structure shape match" needs a concrete metric. Maybe normalised section-type sequence (drop count, break count, fill positions) + outro length?

2. **What's the rejection-learning encoding?** When Sam moves entry from drop_5 to drop_7, the next pair-history entry stores both. But what TRIGGERS the rule to be applied next time? Maybe weight = (times same correction was made) − (times Claude was right first try).

3. **How to handle pairs with NO matching history yet?** Fall back to natural-fill-alignment defaults from `arrange_sections.py`. Mark these proposals as "low confidence" in the ARRANGEMENT report so Sam knows to check.

4. **When does a pattern get promoted from pair_history to MIX_PATTERNS.md?** Suggest: when 3+ corrections converge on the same rule.

## Files to NOT touch tomorrow

- `Source/automated_dj_mixes/phrase_viz.py` — sections detection LOCKED IN. Don't tune.
- `Source/automated_dj_mixes/orchestrator.py` `--sections-layout` mode — also locked.
- `Source/sections_blind_viz.py` — 8-quarter default is locked.
- `Source/apply_section_corrections.py` — works, don't touch.
- The `/section-detection` skill.

Tomorrow is purely about ARRANGEMENT — new skill, new code, new library. Sections are done.
