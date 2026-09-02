# 2026-09-02 — escape-boundary sweep across the /mix ALS consumers

Two shipped bugs came from the same class: track names read from ALS XML
carry XML escapes (`&amp;`, `&apos;`, `&#39;`); names from WAV filenames,
hint keys, and human displays do not. A consumer that crosses that
boundary without unescaping silently mismatches and looks like a MISSING
or SKIPPED record. Both bugs shipped:

- Bug 1 (fixed 2026-06-10): `transition_review_viz` flagged every
  track-with-`&` as MISSING in the WAV path lookup.
- Bug 2 (fixed 2026-09-02, commit b8dd05b): `validate_hints_vs_sections`
  skipped every track-with-`&` because sections JSON keys are escaped and
  hint keys are not — 12 of 40 gate checks vanished traceless.

This sweep audits every consumer of ALS-derived track/clip names in
`Source/` (plus `Tests/` where relevant) for the same class.

## Method

For each consumer I traced where its track names come from, where they
are matched/compared, and whether any comparison crosses an escape
boundary (ALS XML / sections JSON / arrangement-report JSON  →  WAV
filename / hint key / human display). The boundary check is the rule:
*both sides must be unescaped, or both must remain escaped, or the
consumer must unescape one side at the crossing*. Consumers that
compare escaped-to-escaped (e.g. section-JSON vs section-JSON, ALS vs
ALS) are CORRECT as-is — adding unescape on one side would BREAK them.

I did not change a single consumer that already handles the boundary.
The only modifications in this sweep are the two functional fixes in
sibling commits (V1_baseline dual-write + provisional-BPM display);
the sweep itself is the audit table below.

## Audit table

Legend:
- **Source** — where the consumer's track names come from.
- **Compare to** — what the names are matched against (the boundary).
- **Boundary** — does the comparison cross an escape boundary?
- **Handled?** — does the consumer handle the boundary correctly?
- **Risk** — if not handled, what would break.

| File | Source | Compare to | Boundary? | Handled? | Risk if not |
|------|--------|------------|-----------|----------|-------------|
| `Source/extract_sections_als.py` | ALS XML (EffectiveName) | (writes only) | n/a — producer | n/a | n/a |
| `Source/automated_dj_mixes/als_generator.py` | input list | (writes XML) | n/a — producer | n/a | n/a |
| `Source/automated_dj_mixes/warp_contract.py` | ALS XML | expected_names (ALS-derived) | no — both escaped | yes (unescape on both sides, L60, L69) | none |
| `Source/align_engine.py` | ALS XML + arrangement report | stem JSON keys (unescaped) | YES — crosses | yes (`_resolve_stem_key` L1535, L2205) | none |
| `Source/analyze_correction_diff.py` | ALS XML | (comparison internal) | no — both sides via `_normalise_name` (L69, L74) | yes | none |
| `Source/apply_automation.py` | ALS XML / sections JSON | WAV filenames (unescaped) | YES — crosses | yes (`_wav_for_track` unescape, L1114) | tests pin both `&amp;` and `&#39;` (test_wav_track_matching.py) |
| `Source/apply_loops.py` | input track names (could be either side) | ALS XML track names | YES — crosses (when called from other modules) | yes (`_normalise` decodes entities, L100-102) | none |
| `Source/apply_section_corrections.py` | input literal names | ALS XML | YES — crosses | yes (`_entity_variants` tries both forms, L43-58) | none |
| `Source/arrange_sections.py` | input literal names | ALS XML | YES — crosses | yes (escaped-apostrophe fallback at L121) | none |
| `Source/build_ab_comparison.py` | orchestrator of subprocesses | n/a | n/a | n/a | none |
| `Source/diff_sections.py` | sections JSON | sections JSON | no — both escaped | yes (escaped-apostrophe variant L150) | none |
| `Source/fit_grids_from_ticks.py` | arrangement report + ALS markers | WAV filename stem | YES — crosses | yes (unescape on both sides L40, L42) | none |
| `Source/isolate_sections_tracks.py` | input literal names | ALS XML / sections JSON | YES — crosses | yes (`_normalise` from apply_loops, L28-30) | none |
| `Source/learn_from_correction.py` | sections JSON | ALS XML track names + automation keys | no — both escaped via `_normalise` (no unescape in normaliser, but matching is consistent) | yes (consistent escaped-to-escaped, L178) | none |
| `Source/loop_review_viz.py` | arrangement report | WAV filename | YES — crosses | yes (`display_track = html.unescape(track)`, L235) | none |
| `Source/materialize_section_details.py` | input track_names list | ALS XML / sections JSON | YES — crosses (track_names input) | yes (unescape for `SECTIONS_STEM_*` filename L133; `_normalise` for XML match via apply_loops) | none |
| `Source/mix_predict.py` | ALS XML | (internal ALS-only processing) | no — no cross | n/a | n/a |
| `Source/probe_als_arrangement.py` | ALS XML | (display only) | no — no cross | n/a | n/a |
| `Source/probe_als_warp.py` | ALS XML (FileRef path) | WAV filenames | YES — crosses | yes (`wavs.get(name) or wavs.get(html.unescape(name))`, L101) | none |
| `Source/probe_grid_vs_ableton.py` | ALS XML | WAV filenames | YES — crosses | yes (`wavs.get(name) or wavs.get(html.unescape(name))`, L37) | none |
| `Source/probe_onset_lag.py` | ALS XML | WAV filenames | YES — crosses | yes (`wavs.get(name) or wavs.get(html.unescape(name))`, L75) | none |
| `Source/propose_arrangement.py` | sections JSON | MIK DB (filesystem) + hint keys (JSON) | YES — crosses | yes (`clean = html.unescape(t.name)` for WAV path L1096-1100; `_hint_for` unescape L1043-1044) | tests pin both cases (test_codex_blocker_fixes.py Fix 1) |
| `Source/render_check.py` | ALS XML | arrangement report (loops) | no — both use escaped forms but no name-based compare between them | n/a | n/a |
| `Source/sections_blind_viz.py` | sections JSON | WAV filenames + report BPMs | YES — crosses | yes (`clean_name` from `_normalise` on both report and WAV path, L608-612) | none |
| `Source/sections_compare_viz.py` | sections JSON (V8) | sections JSON (V7) + WAV filenames + analyses | YES — crosses (V8 vs WAV; escaped-vs-unescaped V8 vs V7) | yes (`candidates` with unescape L259; WAV path L275) | none |
| `Source/transition_review_viz.py` | ALS XML (arranged) or sections JSON | WAV filenames + arrangement report | YES — crosses | yes (`html.unescape(name) + ".wav"`, L417; `html.unescape(t["name"])` for bpm_lookup L399; L441-443 for transitions) | tests pin both pin classes in b8dd05b era |
| `Source/validate_als.py` | ALS XML | expected_transitions from arrangement report | YES — crosses | yes (`_normalise_name` unescapes on both sides, L91-92) | tests pin via test_validate_als_expectations.py |
| `Source/validate_hints_vs_sections.py` | sections JSON | hint keys + arrangement report BPMs + stem JSON BPMs | YES — crosses (the bug b8dd05b fixed) | yes (b8dd05b: `html.unescape(raw_track_name)` then 3-candidate hint lookup, L146-156) | tests pin the &amp; case (test_validate_hints_vs_sections.py) |
| `Source/validate_mix_plan_als.py` | ALS XML | mix plan track names | YES — crosses | yes (`_track_name` unescapes L46; plan names unescaped at L128, L339) | none |
| `Source/validate_sections_review.py` | sections JSON | MD headings (human display) | YES — crosses | yes (`_normalise` decodes on both sides, L162-167) | none |
| `Source/verify_grid_bar_parity.py` | ALS XML (FileRef path) | grid_overrides.json keys (WAV filenames) | no — both are unescaped WAV filenames | yes (consistent unescaped-to-unescaped, L43) | none |
| `Source/automated_dj_mixes/mik_reader.py` | audio_path (filesystem) | MIK DB by filename | no — filesystem match | n/a | n/a |

## Findings

- **No new broken crossings.** Every consumer that crosses an escape
  boundary already does the right thing — unescape at the boundary, or
  try both forms, or compare only within an escape-consistent domain.
- **Two escape-consistent consumers were tempting to "fix" but are
  correct as-is.** `Source/diff_sections.py` (V1 sections vs V2 sections,
  both escaped) and `Source/learn_from_correction.py` (sections JSON vs
  ALS automation keys, both escaped). Patching these with an unescape
  on one side would silently BREAK the matching.
- **b8dd05b was the right fix.** The validate_hints_vs_sections gate
  had exactly the bug class I expected — sections JSON escaped, hint
  keys unescaped, no unescape at the crossing. The fix (unescape the
  raw_track_name once before the 3-candidate lookup) is the canonical
  pattern.
- **Existing test coverage.** `test_validate_hints_vs_sections.py`
  pins the &amp; case for the b8dd05b fix.
  `test_wav_track_matching.py` pins both &amp; AND &#39; for
  apply_automation._wav_for_track. `test_codex_blocker_fixes.py` pins
  the propose_arrangement._hint_for unescape.

## Verdict

No code patches required for the sweep itself. The b8dd05b fix plus the
two sibling commits in this cluster (V1_baseline dual-write,
librosa-fallback BPM display) cover the mechanical defects the 02.09.26
audit surfaced.

## References

- b8dd05b: `fix(gates): four defects caught live by the 02.09.26 end-to-end audit run`
- 2026-06-10: `transition_review_viz` "MISSING" fix (b6720b2, recorded in
  Documentation/AI_CONTEXT.md L150).
- 2026-09-02 audit: `Documentation/Reviews/2026-09-02 Mix Skill End-To-End Audit.md` (P1-5).