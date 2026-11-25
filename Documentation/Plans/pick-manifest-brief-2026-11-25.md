# Brief: pick manifest for Tools/playlist_builder.py (2026-11-25)

Builder: Sonnet subagent, in the locked worktree `D:\Worktrees\dj-pick-manifest` on branch `work/pick-manifest`. Write only inside that worktree. Reviewers after you: an Opus executing reviewer and MiniMax. Commit locally, never push. No em dashes anywhere (comments, output, commit message): use a comma, colon, brackets or a plain hyphen.

## Why

On 2026-11-24 the first full /mix run got a tracklist from `Tools/playlist_builder.py --preset any`. That preset ranks by stream percentile and gives every slot a different genre, so it returned the most streamed records across eight styles. Sam: "such a mish mash ... a credible cool mix". He also asked for 12 tracks and got 10 with no word said. The fix is that the picker tells the truth about what it did before any stem run, and a "credible" request cannot use `--preset any`.

## What to build (all in `Tools/playlist_builder.py`, tests in `Tests/test_playlist_builder.py`)

1. **First line of every `build` report: requested n against built n.** `built 10 of 12 requested` plus the reason when short (pool too small, max-per-artist, arc could not fill). Also to stderr when short. Today only a pool-size warning exists.
2. **A manifest block printed before the track rows**, from data already in the rows and the pool:
   - the active filters (label, artist, year range, min streams, heat range, preset, arc, max-per-artist, n) as given
   - the SQL-level pool size (rows fetched), then the pool size after `prepare()` with the count dropped by each reason (not a playlist track, genre not on the ladder, tempo outside the band, year filter)
   - genre spread and era spread of the picked tracks (counts by genre, by year band) and of the pool, so a mish mash is visible as a flat spread against a narrow pool
   - heat curve and the number of distinct genres picked
3. **Repeats against earlier mixes.** Read the track list of earlier mixes and flag any pick that was in one, named with the mix. Find where earlier mixes are recorded first (look at `D:\Claude Workspace\Automated DJ Mixes\Projects\*\Audio\` and the hub's `Listening Sets\`; the hub also has the Projects folders under the work folders). Match with the existing `norm()` on artist and title. Print the COVERAGE COUNT of this check (how many earlier mixes were readable). If none are readable, say `repeats: not checked (no earlier mix list found)`, never print "none".
4. **Beat-grid gate result per pick, from what already exists only.** The gate runs on drum stems, so it cannot be run per pick before separation. Find what is cached from earlier runs (per-track grid and `grid_vs_kick_ms` caches, `SECTIONS_STEM_*.json`, or the Audio Analysis Toolkit cache) and print per pick: `grid: PASS 4.1 ms (cached)`, `grid: FAIL 127 ms (cached)` or `grid: unknown (never analysed)`. Print the COVERAGE COUNT (`grid known for 3 of 10 picks`). Never print an unknown as a pass. A pick with a cached FAIL is called out in the manifest summary line. Do not add a new beat-grid detector.
5. **`--credible` flag.** With `--credible`, `--preset any` is refused by `ap.error` (exit 2) with a one line reason; the default preset stays `any` for everything else so nothing existing breaks. Without `--credible` and with `--preset any`, print one line in the manifest: `preset any ranks by stream percentile across all genres; use --credible --preset cool for a coherent set`.
6. **Do not turn any complaint into a filter.** Do not add a bit depth, year or format filter (Sam: bit depth does not matter, use all years).

## The four things every brief states

1. **What exists at hook time.** The manifest runs inside `build`, after `fetch` and `prepare`, before `--resolve`. Rows come from Neon or `--rows`. Master file paths exist only when `--resolve` is given. Earlier mix folders and caches are files on disk and may be absent on another machine: every read of them is wrapped so a missing folder gives `not checked`, never a crash.
2. **Coverage counts to print and to put in your report:** how many credits in a real rows set have `genre`, `bpm`, `musical_key` and `release_date`; how many earlier mixes you could read; how many picks have a cached grid result. Use real data: the rows are fetched with `python Tools/playlist_builder.py build --label ...` against the read-only Neon role (it reads the credentials file itself; never print that URL or file). Stay offline for the unit tests.
3. **Verdict matrix.** This feature returns a manifest, not PASS or FAIL, but the grid line is a verdict. List in your report the expected grid line for at least six real picks (cached PASS, cached FAIL, unknown) with where each came from.
4. **Adversarial inputs to test:** empty pool; pool smaller than n; n of 1; all picks one genre; a title with only punctuation; a remix and an original of the same song (different versions, not repeats); a cache file that is corrupt JSON or half written; an earlier mix folder with unreadable names; `--credible` with `--preset any`, `cool`, `cult`, `hits`; rows missing `release_date` or `genre`; unicode artist names; Windows paths with spaces.

## Rules

- Match the existing style of the file (compact, no new framework). Do not change pick selection (`build()`, `preset_score`, `ARCS`, `HEAT`). Output of the existing track rows and the final lines stays byte for byte as is unless a test must change; say so in your report.
- Read-only: only SELECT is ever sent. Never print credentials.
- Tests: extend `Tests/test_playlist_builder.py` (59 tests today, all must still pass). Stub the Neon reader as the file does. Run `python -m pytest Tests/test_playlist_builder.py -q` and then the full suite from the worktree.
- Update the `Tools/playlist_builder.py` paragraph in `Documentation/TOOLBOX.md` (it still says "no tests" and "not hooked to resolve_masters").
- Report: files changed, test counts, the coverage counts, the real manifest printed for a real run (`--label` or a year range of your choice, `--credible --preset cool --n 12`), anything you could not verify. Say what you did not check.
