"""Test _resolve_bpm_downbeat_stats in Source/stem_detector.py (burn list D5
follow-up, 2026-09-22).

detect(wav, project) falls back to this when called without explicit
bpm/downbeat (the standalone --write-hints CLI path). Before this fix the
fallback only ever read a "Blind_V*" folder from the amplitude blind-viz
pipeline, RETIRED 2026-06-10 - nothing writes that folder any more, so
--write-hints always printed "[skip] no stats" for every track and wrote an
empty hints file, even on a project that had JUST finished a full stem-grid
analysis pass. Found running --write-hints live on a real new mix build
(22.09.26 Tech House Core Sample) - exactly the gap burn list D5's own
2026-09-15 note flagged as unverified and left unconfirmed.

The fix: read bpm from this track's own already-written
SECTIONS_STEM_*.json (the real source of truth in the owned-stem-grid
architecture), with downbeat always 0.0 (this pipeline's own convention).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

from stem_detector import _resolve_bpm_downbeat_stats  # noqa: E402


def _write_sections_stem_json(project: Path, wav_stem: str, bpm: float) -> None:
    stem_dir = project / "_Stem Analysis"
    stem_dir.mkdir(parents=True, exist_ok=True)
    (stem_dir / f"SECTIONS_STEM_{wav_stem}.json").write_text(
        json.dumps({"track": wav_stem, "bpm": bpm, "n_bars": 100,
                    "sections": [], "signals": {}}),
        encoding="utf-8")


def test_reads_bpm_from_the_real_cached_sections_stem_json(tmp_path):
    """The exact real-world case: a track has already been through Phase 1a
    (stem-sections), so its SECTIONS_STEM_*.json exists with a real bpm, but
    no Blind_V folder was ever created (the stem pipeline never writes one)."""
    _write_sections_stem_json(tmp_path, "Some Track", 128.04)
    stats = _resolve_bpm_downbeat_stats(tmp_path, "Some Track")
    assert stats is not None
    assert stats["bpm"] == 128.04
    # downbeat is not carried by the cache - the caller defaults it to 0.0
    # via stats.get("first_downbeat_sec", 0.0), matching this pipeline's own
    # "stem bar 0 == the track's downbeat" convention.
    assert "first_downbeat_sec" not in stats


def test_no_cache_and_no_blind_folder_returns_none():
    """A fresh project with neither artifact must return None (the caller's
    own "[skip] no stats" path), not silently fabricate a BPM."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        stats = _resolve_bpm_downbeat_stats(Path(td), "Nonexistent Track")
    assert stats is None


def test_malformed_cache_falls_through_rather_than_crashing(tmp_path, capsys):
    """A SECTIONS_STEM_*.json that exists but is missing 'bpm' (or is not
    valid JSON) must not raise - it falls through to the Blind_V check (which
    also fails here) and returns None, same as no cache at all. It must also
    NOT look identical to "no cache exists" on stdout (MiniMax review,
    2026-09-22) - a silent look-alike is what hid the original bug."""
    stem_dir = tmp_path / "_Stem Analysis"
    stem_dir.mkdir(parents=True)
    (stem_dir / "SECTIONS_STEM_Broken Track.json").write_text(
        json.dumps({"track": "Broken Track", "n_bars": 100}), encoding="utf-8")
    stats = _resolve_bpm_downbeat_stats(tmp_path, "Broken Track")
    assert stats is None
    assert "malformed" in capsys.readouterr().out


def test_falls_back_to_blind_v_when_no_cache_exists(tmp_path, monkeypatch):
    """An old project that genuinely still has a retired-pipeline Blind_V
    folder (never re-run since the stem pipeline landed) must still resolve
    via that path - the fix adds a preferred source, it doesn't remove the
    old one."""
    import stem_detector as SD

    def fake_load_stats(blind_dir, wav_stem):
        return {"bpm": 124.0, "first_downbeat_sec": 0.5, "sections": [{"x": 1}]}

    monkeypatch.setattr(SD, "_load_stats", fake_load_stats)
    blind_dir = tmp_path / "Sections Review" / "Blind_V1"
    blind_dir.mkdir(parents=True)
    stats = SD._resolve_bpm_downbeat_stats(tmp_path, "Legacy Track")
    assert stats == {"bpm": 124.0, "first_downbeat_sec": 0.5, "sections": [{"x": 1}]}


def test_cache_is_preferred_over_a_stale_blind_v_folder(tmp_path, monkeypatch):
    """When BOTH exist (a project analysed under the old pipeline, then
    re-run under the new one), the current stem-grid cache wins - it's the
    real, current source of truth; Blind_V is a retired leftover."""
    import stem_detector as SD

    _write_sections_stem_json(tmp_path, "Both Sources Track", 130.0)
    blind_dir = tmp_path / "Sections Review" / "Blind_V1"
    blind_dir.mkdir(parents=True)
    monkeypatch.setattr(SD, "_load_stats",
                        lambda *a, **k: {"bpm": 999.0, "sections": []})
    stats = SD._resolve_bpm_downbeat_stats(tmp_path, "Both Sources Track")
    assert stats == {"bpm": 130.0}
