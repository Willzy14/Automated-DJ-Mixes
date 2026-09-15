# Second review of D10 and D11 - Claude subagent (Sonnet), 2026-09-15

This review stood in for Codex, which hit its usage cap on the same brief (resets 2026-09-19 11:48).
It is the same brain as the author (Claude), so it is supporting evidence and does not count as the
independent review. The independent review is MiniMax's: `Receipts/2026-09-15/minimax-review-D10-D11.md`.

Brief: `review-brief-D10-D11.md`. Diff reviewed: `review-D10-D11.diff`. Transcribed from the
subagent's final report, with XML escapes from the notification wrapper decoded back to plain `&`.

---

VERDICT: SOUND

**Findings**

1. `Source/apply_automation.py:1337-1340` (escaping, brief item a) — Correct and complete. Order matters: `html.escape(value, quote=False)` runs first (encodes bare `&`/`<`/`>`), then `.replace('"', "&quot;")` and `.replace("'", "&apos;")` run on the already-escaped string — so the `&` inside the literal strings `"&quot;"`/`"&apos;"` is never re-escaped. Windows forbids `< > : " / \ | ? *` in filenames, so on these machines only `&` and `'` can realistically appear in a real audio filename, and both are now handled. Traced idempotency by hand (unescape→re-derive from `found`→re-escape always reaches the same fixed point) and confirmed with `test_idempotent_running_twice_matches_running_once` and the new `test_ampersand_in_filename_is_re_escaped_and_output_parses`, both passing. No character is mishandled.

2. `Source/apply_automation.py:1337`, `_find_audio_dir` (1203-1225), caller (1567, 1637) (brief item b) — `absolute()` is correct, not a bug risk. `audio_dir` is computed once at line 1567 and reused unchanged ~70 lines later at 1637 within the same `main()` call; `grep -r chdir Source/` returns zero hits anywhere in the pipeline, so CWD cannot drift between the two uses in this process. Traced `build_ab_comparison.py`'s subprocess path too — it passes `str(final)`/`str(arranged)` unmodified (no `cwd=` override, so it inherits the parent's CWD) and each side runs in its own single-shot subprocess, so the same single-computation/single-consumption invariant holds there too. `Path.absolute()` is also a documented no-op when already absolute, so the common already-absolute case (most existing tests) is unaffected. Verified with the new `test_relative_audio_dir_still_writes_an_absolute_path_value` (monkeypatches cwd, passes a genuinely relative `Path("Audio")`), which passes.

3. Removed `"Audio" in name` filters (brief item c) — Confirmed genuinely redundant, not load-bearing. `parse_sections_als` (`extract_sections_als.py:104-105`) only ever inserts a track into its result dict `if clips:` — an empty-clip track can never become a dict key in the first place, in any of the three call sites (`extract_sections_als.py`, `validate_hints_vs_sections.py` reading the JSON it wrote, `transition_review_viz.py` calling it directly). So `not secs`/`if clips` are the only checks that ever mattered; the substring filter was only ever catching real names like "New Audio 27.07.26". `extract_sections_als.py`'s filter is confirmed print-loop-only (JSON write at line 120-143 precedes it). Verified the "off the /mix path" claim: `sections_blind_viz.py`/`validate_sections_review.py` are explicitly marked retired in `mix.md:106-114`; `diff_sections.py` is referenced only by `Source/Archive/regress_section_detection.py`; `sections_compare_viz.py` has zero references anywhere in `Source/`. `learn_from_correction.py`'s `"Audio" in name and "-" not in name` correctly spares any "Artist - Title" name.

Ran the brief's targeted suite plus every test file referencing these four modules (17 files): **290 passed, 0 failed**, including all 13 tests in `test_sample_ref_paths.py`.

**FOUND UNASKED**

- No dedicated regression test exists for the D11 fix in `validate_hints_vs_sections.py` or `transition_review_viz.py` with a track name containing "Audio" as a substring — only the real-project re-run (46/46) verifies it. A future refactor could silently reintroduce this exact bug with nothing to catch it.
- `Documentation/BURN_LIST.md:1706` D11's "Evidence:" line cites only `validate_hints_vs_sections.py:142` and `extract_sections_als.py:148`, omitting `transition_review_viz.py:369`, which the diff and this brief both treat as part of the same D11 fix.

---

## What was done with the two FOUND UNASKED points

- Regression test added after this review: `Tests/test_validate_hints_vs_sections.py::test_track_with_audio_in_its_title_is_validated`. It is test-only; the reviewed production code is unchanged. `transition_review_viz.py`'s filter sits inline in `main()` and is still covered only by the real-project re-render (11 of 11 transitions drawn).
- D11's evidence line in `Documentation/BURN_LIST.md` now includes `Source/transition_review_viz.py:369`.
