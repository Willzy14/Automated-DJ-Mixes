## Verdict: SOUND WITH TWO MINOR NITPICKS

The fix is correct. The cache-preference ordering is right, the downbeat=0.0 assumption is verifiable from the codebase, and the `align_engine.load_track()` seam is clean. Two small things to consider folding in before this is closed out — neither is a defect, both are improvements.

### 1. Cache-preference-over-Blind_V ordering: correct ✓

Your reasoning holds. `SECTIONS_STEM_*.json` is what this same `detect()` writes a phase earlier (line 1163: `(project / "_Stem Analysis" / f"SECTIONS_STEM_{wav.stem}.json").write_text(...)`), so it's a self-consistent source of truth. `Blind_V*` is from a retired pipeline whose writer was deleted 2026-06-10 — preferring the live cache over a stranded old artifact is exactly right. Test 5 (`test_cache_is_preferred_over_a_stale_blind_v_folder`) pins this.

### 2. The bare `except ... pass` masks a real corruption case (low severity, worth a print)

This is the one real concern. The fallthrough is defensible *as a control-flow choice* — `_resolve_bpm_downbeat_stats` correctly returns None in both "no cache" and "cache unreadable" cases, which is its contract. But the **only** user-visible signal for a corrupted cache is the same generic `[skip] no stats (bpm/downbeat)` print as for a missing cache. That's exactly the class of silent failure that hid the original bug for ~3 months — same shape, same root cause (look-alike messages for different underlying states).

Cheap, consistent with the existing `[skip]` print convention in `detect()`:

```python
if cached.exists():
    try:
        return {"bpm": json.loads(cached.read_text(encoding="utf-8"))["bpm"]}
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"  [warn] malformed cache {cached.name}: {type(exc).__name__}: {exc}; "
              f"falling through to legacy source")
        pass
```

Doesn't change behavior, doesn't change the test contract (the existing `test_malformed_cache_falls_through_rather_than_crashing` would still pass — add an `assert "malformed" not in capsys.readouterr().out` if you want to lock the new message in). I would do this.

### 3. The `stats and "sections" in stats` guard: works, but fragile against a third source

The fix as written is **correct and minimal**. The fragility is real but small: the diagnostic line at the bottom of `detect()` knows `stats` may have two shapes (cache → `{"bpm"}`, Blind_V → `{"bpm", "sections", ...}`) and encodes that as "look for the `"sections"` key". If a future third source lands in `_resolve_bpm_downbeat_stats` that happens to have a `"sections"` key with a different meaning, this guard silently mis-fires.

Cleanest fix: have the helper return provenance explicitly:

```python
def _resolve_bpm_downbeat_stats(project, wav_stem) -> tuple[dict | None, str]:
    ...
    return {"bpm": ...}, "cache"
    ...
    return _load_stats(...), "blind_v"
    ...
    return None, ""
```

Then `detect()` unpacks `(stats, stats_source)`, and the diagnostic line becomes:

```python
old_n = f"old {len(stats['sections']):2d} -> " if stats_source == "blind_v" else ""
```

Explicit, future-proof, and self-documenting. But this is a refactor on a fix that already works — the current shape check is fine to ship as-is. Defer to taste.

### 4. Other findings

**Downbeat=0.0 assumption: verified, not just asserted.** `Source/align_engine.py:2655` documents the same convention in its own docstring: `"stem bar 0 == the track's downbeat == the sections-JSON zero point, so no per-track offset correction is needed."` So `stats.get("first_downbeat_sec", 0.0)` is correct under the actual project convention — not just your reading of `mix.md`.

**`align_engine.load_track()` seam: clean.** `load_track` reads `bpm` directly from the cache (line 685: `bpm = d["bpm"]`) and derives `downbeat` independently from `secs[0]["start_sec"] - secs[0]["start_bar"] * spb`. Both are independent of `_resolve_bpm_downbeat_stats`'s new code path. The cache's `bpm` was already authoritative for `load_track`; this fix just gives the standalone `--write-hints` the same source. No seam risk.

**Test coverage: good, one stretch case worth considering.** The 5 tests cover cache-only, no-source, malformed-cache, Blind_V-only, and both-exist. I'd consider adding one for "cache exists but `bpm` is 0 / negative / non-numeric" — currently the function would silently return `{"bpm": 0.0}` and `detect()` would crash on `sec_per_bar = 4 * 60.0 / 0.0`. That's a defensive guard more than a current bug, but `KeyError` is in the except tuple while `TypeError` isn't, so `bpm: "not a number"` slips through. Not a blocker.

**`else: stats = None` is right.** Mirrors the original control flow exactly (the old code had `stats = None` at module level and only reassigned it in the Blind_V branch). Diagnostic print behaves identically to main @ pre-fix for orchestrator callers.

### Recommendation

Ship it. If you want a single follow-up: add the `[warn]` print from item 2 in the same commit, plus a 6th test that locks the warning message in. Items 3 and 4b are nice-to-haves, not blockers.


===MINIMAX-ASK-DONE exit=0 session=pi nonce=4d08dcc3f9b1475b9557e17a97c723a6===
