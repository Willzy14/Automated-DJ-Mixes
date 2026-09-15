VERDICT: SOUND

I'm comfortable closing D10 and D11. Reasoning per file, then the FOUND UNASKED list.

## D10 — `_fix_sample_ref_paths` escape + absolute path

**Escape is complete and correct for XML attribute values delimited by `"`.**
`html.escape(v, quote=False)` handles `&` `<` `>`; the trailing `.replace('"', "&quot;").replace("'", "&apos;")` handles the quote characters. Order matters: `html.escape` runs first, so any literal text that already looks like an entity (`&quot;`, `&apos;`, `&amp;`) gets its `&` escaped to `&amp;` before the `.replace` sees it — round-trips a pre-escaped entity correctly.

Idempotency: walking the test case through twice, the second pass reads `&amp;` → unescape to `&` → lookup → `html.escape(quote=False).replace(...)` writes `&amp;` back. Same on a third pass. The existing `test_idempotent_running_twice_matches_running_once` covers the no-special-chars case; the new ampersand test happens to verify the same property because its `ET.fromstring(fixed)` is invariant under repeated escape.

Path-character coverage: I walked the new code with `&`, `<`, `>`, `"`, `'`, and the six-char literal `&apos;test.wav` as a synthetic edge — all round-trip correctly. The only character classes that can break XML attributes are exactly the five the new code now handles. Apostrophes deliberately stay as `&apos;` to match every untouched FileRef the codebase locates by string match elsewhere — the comment in the code is accurate.

**`absolute()` over `resolve()` is the right call.** `Path.absolute()` resolves against `os.getcwd()` without following symlinks/junctions; `Path.resolve()` follows them and would swap the Dropbox junction for its target on these machines, which is the failure the brief is steering away from. The new test `test_relative_audio_dir_still_writes_an_absolute_path_value` reproduces the bug shape (relative `audio_dir`, `monkeypatch.chdir(tmp_path)`, `Path("Audio")` as the audio dir) and asserts both `Path(written).is_absolute()` and `Path(written).samefile(tmp_path / "Audio" / "Track One.wav")`. That's the right test.

The only coupling worth flagging: `audio_dir` is computed in `main()` via `_find_audio_dir(als_path)` and consumed later in the same `main()` by `_fix_sample_ref_paths(lines, output_path, audio_dir)`. Both `os.path.relpath(found, output_path.parent)` (for `new_rel`) and `found.absolute()` (for `new_abs`) resolve against whatever CWD is at the moment of the second call. Nothing in `main()` calls `os.chdir()` between them, and the `/mix` runner sets CWD once and leaves it. Not a bug today, but if a future caller ever chdirs mid-`main()`, both `new_rel` and `new_abs` would silently desynchronise from each other and from the file actually on disk. A small defensive `Path.cwd()` capture at the top of `main()` would harden this; out of scope for the burn-list tick.

## D11 — removed `"Audio" in name` substring filters

The three sites are correctly carved:

- **`validate_hints_vs_sections.py`** keeps `not raw_track_name or not secs`. A genuine empty template track (no clips) still falls out via `not secs`. The new full pipeline confirms 46 of 46 hint rows agree on the 12-track real project.
- **`extract_sections_als.py`** the JSON write happens before the print loop (re-read of `main()` confirms it), so removing the substring filter only re-enables console printing of "Tommy Farrow - Falling (New Audio 27.07.26 Extended MIx) 24 Bit MASTER" — exactly the intended fix.
- **`transition_review_viz.py`** keeps `if clips`. `parse_sections_als` already enforces `if clips: result[track_name] = ...`, so the `if clips` in the dict comprehension is belt-and-suspenders (see FOUND UNASKED), but it does keep the same fail-safe semantics the brief relies on. Re-rendering the real project now draws all 11 transitions including the Pat Premier → Arielle Free leg.

Off-path tools (no files staged for me, but I grepped the staged tree for cross-references and got zero hits in any of the staged files): `diff_sections.py`, `sections_blind_viz.py`, `sections_compare_viz.py`, `validate_sections_review.py`, `learn_from_correction.py` are not imported or invoked by any of the staged source. Consistent with the brief's "ad-hoc or retired" claim. The compound condition in `learn_from_correction.py` (`"Audio" in name and "-" not in name`) does pass real "Artist - Title" names because they always carry the dash, so leaving it alone is defensible — though it's brittle to the unusual real track titled "Audio Something" with no dash.

## FOUND UNASKED

1. **Dead check + stale comment in `extract_sections_als.py`.** `parse_sections_als` already does `if not track_name: continue` at the top of its per-track loop and `if clips: result[track_name] = ...` before writing to the result dict. With those two upstream filters, `data.items()` in `main()` can never yield a falsy `track_name`. The new `if not track_name: continue  # skip empty template tracks` line is unreachable, and the comment now reads as a fossil from when the same line also caught `"Audio" in track_name` template tracks. Not a correctness issue; consider dropping both.

2. **Redundant `if clips` in `transition_review_viz.py`.** Same shape — `parse_sections_als` already filters `if clips`, so the comprehension's `if clips` never removes anything new. Harmless and arguably worth keeping as a defensive guard against a future `parse_sections_als` relaxing its contract.

3. **CWD-coupling for the absolute path fix (D10).** `audio_dir` is relative; `os.path.relpath(found, output_path.parent)` and `found.absolute()` both resolve against whatever CWD is when `_fix_sample_ref_paths` is called. Today nothing in `main()` chdirs, but a future caller that does will silently desync `new_rel`, `new_abs`, and the on-disk reality. Cheap fix: capture `cwd = Path.cwd()` at the top of `main()` and pass it through. Out of scope for this tick.

4. **Round-trip on literal-`&apos;` filenames.** If a filename literally contains the six characters `&apos;` on disk, `html.unescape(value)` in the read path will decode that as the entity (yielding a single `'`), so the file is looked up under the wrong name and falls through the per-clip fail-safe. Pre-existing behaviour unchanged by this diff; only matters if anyone ever names a file with a literal HTML-entity substring.

5. **`learn_from_correction.py`'s compound filter is a coincidence, not a contract.** Works for "Artist - Title" because real names always have a dash, but a real track titled "Audio Podcast" with no dash would be silently filtered. Same pre-existing brittleness — flagged, not blocking.

6. **No reference in the staged source to any of the off-path tools.** `grep` for `diff_sections|sections_blind_viz|sections_compare_viz|validate_sections_review|learn_from_correction` across all five staged files returns zero hits. Consistent with the "off the /mix path" claim; nothing in the staged code would import them.

7. **`_find_audio_dir` comment vs call-site minor mismatch.** Docstring says "Find the project's Audio/ folder above *als_path*." The function walks up from `als_path.parent`, which is "above als_path", so it's accurate — but the call site is `_find_audio_dir(als_path)` and the helper is called before `_fix_sample_ref_paths` reads `output_path.parent`. Not a bug; just worth noting the Audio/ search anchors on the input ALS depth while the relative-path recomputation anchors on the output ALS depth. Today's pipeline passes both to similar depths; only matters if a future caller ever uses a different input/output depth on purpose.


===MINIMAX-ASK-DONE exit=0 session=pi nonce=52b1da14507a40019d5e858167058fe8===
