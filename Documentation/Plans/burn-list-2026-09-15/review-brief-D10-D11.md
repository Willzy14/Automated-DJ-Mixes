VERDICT FORMAT: the FIRST line of your reply must be exactly one of:
VERDICT: SOUND
VERDICT: CORRECTION
VERDICT: DROP

CONSUMED-BY: Claude, deciding whether burn list items D10 and D11 (Automated DJ Mixes, Documentation/BURN_LIST.md) can be checked off and committed. SOUND lets them close; CORRECTION gets applied before any commit; DROP reverts the change.

This brief may be wrong. If the framing below is mistaken, say so plainly.

## What changed (uncommitted - see review.diff)

Three bug fixes, all found on 2026-09-15 while building a real 12-track mix through the /mix pipeline.

1. D10 - Source/apply_automation.py, function _fix_sample_ref_paths (added the day before as burn list A6, commit f312241). It html.unescape()s each FileRef's RelativePath and Path values to look the file up under the project's Audio/ folder, then wrote the new values back escaping ONLY apostrophes. A filename containing an ampersand ("RUZE & Chesster - Another Night ... .wav") was written as a bare & - invalid XML - and the in-process ALS validation refused the whole set.
   Fix: html.escape(value, quote=False), then '"' -> '&quot;' and "'" -> '&apos;'. Apostrophes stay as &apos; to match every FileRef the function leaves untouched (the codebase locates filenames by matching that form).
   Second defect in the same function: new_abs = found.as_posix() was a RELATIVE path whenever audio_dir was relative, and the /mix pipeline runs from the repo root with relative project paths. The Path attribute is meant to be absolute.
   Fix: found.absolute().as_posix() - deliberately NOT resolve(), which would follow a Windows junction (the Dropbox tree is reached through junctions on these machines).
   Two regression tests added at the end of Tests/test_sample_ref_paths.py.

2. D11 - Source/validate_hints_vs_sections.py and Source/extract_sections_als.py skipped any track whose name contains the substring "Audio" (meant to skip empty template tracks). A real title, "Tommy Farrow - Falling (New Audio 27.07.26 Extended MIx) 24 Bit MASTER", tripped it: the Phase 1 hint gate failed with "no matching sections track", and the extraction summary printed 11 of 12 tracks.
   Fix: remove the substring test. validate_hints_vs_sections keeps its `not secs` (no clips) check. In extract_sections_als the filter was only ever on the console-summary loop; the JSON is written before that loop.

3. D11, same class - Source/transition_review_viz.py built its arranged-ALS section map with `if clips and "Audio" not in name`, which dropped the same track from the Phase 4 per-transition review (10 transitions rendered instead of 11; Pat Premier was drawn straight into Arielle Free).
   Fix: remove the substring test, keep `if clips`.

## Evidence so far (Claude's own - not independent)

- The ampersand case run against HEAD's apply_automation.py: XML parse error and a relative Path. Against the fix: parses, absolute Path.
- Tests/test_sample_ref_paths.py: 13 passed. All 17 test files that reference apply_automation, ALS path values, validate_hints_vs_sections or extract_sections_als: 267 passed.
- Real project: the rebuilt final ALS passed validate_als (--expected-tracks 12 --require-devices --arrangement-report) and validate_mix_plan_als (89 checks). Hint gate re-run: 46 of 46 rows agree.
- NOT unit-tested: the transition_review_viz.py change (it is inline in main()). It is verified only by re-rendering the real project.

## What to check

- Is the escaping complete and correct for XML attribute values delimited by double quotes? Can a second pass double-escape (the tests assert idempotency)? Is there any path character this still gets wrong?
- Is absolute() right here versus resolve()? Can absolute() give a wrong path if the process working directory differs from the one audio_dir was computed against? (_find_audio_dir walks upward from the output path.)
- For the removed "Audio" filters: can a genuinely empty template track now leak through and break something? The reasoning is that validate_hints_vs_sections keeps `not secs`, transition_review_viz keeps `if clips`, and extract_sections_als's filter only affected console printing. Is that reasoning actually right?
- Deliberately NOT changed: the same bare substring filter in diff_sections.py, sections_blind_viz.py, sections_compare_viz.py and validate_sections_review.py (ad-hoc or retired tools, believed to be off the /mix path), and learn_from_correction.py, which uses `"Audio" in name and "-" not in name` and so passes real "Artist - Title" names. Say if any of these is in fact on a live path.

## Standing decisions (do not reopen without new evidence)

- interim_v1 is the production transition policy (Sam, blind listen A5, 2026-09-14).
- Section detection and stems are the source of truth for chops; this change set touches no detection logic.
- Peers review; they do not edit the repository. Return findings as text.

FOUND UNASKED: report anything else wrong you notice in these files, clearly separated from the verdict on this change set.

## Staged files

- review.diff - the full uncommitted diff of the five changed code and test files
- apply_automation.py, test_sample_ref_paths.py, validate_hints_vs_sections.py, extract_sections_als.py, transition_review_viz.py - current versions, for context
