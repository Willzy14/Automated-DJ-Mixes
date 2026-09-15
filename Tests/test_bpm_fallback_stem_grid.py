"""Tests for propose_arrangement.fill_missing_bpm_from_stem_grid (burn list C3).

t.bpm is populated ONLY from the MIK database in propose_arrangement.py -
confirmed directly (the only assignment to TrackInfo.bpm before this fix was
`t.bpm = mik.bpm`). --tempo-arc then hard-raises "tempo arc needs a certified
BPM for every track" even when the owned stem-grid detector already measured
and certified every track's BPM in the same run - the number just lives in
each track's own SECTIONS_STEM_*.json sibling, not the merged sections_path
file propose_arrangement reads tracks from.
"""
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

from propose_arrangement import TrackInfo, fill_missing_bpm_from_stem_grid  # noqa: E402


def _sections_json(tmp_path, track, bpm):
    (tmp_path / f"SECTIONS_STEM_{track}.json").write_text(
        json.dumps({"track": track, "bpm": bpm, "n_bars": 100, "sections": [],
                    "signals": {}}),
        encoding="utf-8",
    )


def test_fills_bpm_when_mik_left_it_empty(tmp_path):
    _sections_json(tmp_path, "Some Track", 128.04)
    t = TrackInfo(name="Some Track", sections=[], arr_start=0.0, arr_end=400.0)
    assert t.bpm is None

    filled = fill_missing_bpm_from_stem_grid([t], tmp_path)

    assert t.bpm == 128.04
    assert filled == {"Some Track": 128.04}


def test_mik_stays_authoritative_when_both_exist():
    """A track with t.bpm already set (from MIK) must NOT be overwritten,
    even if a different stem-grid BPM exists on disk - MIK wins on conflict,
    matching every other field the MIK-enrichment step sets."""
    t = TrackInfo(name="Some Track", sections=[], arr_start=0.0, arr_end=400.0,
                  bpm=129.5)
    # No stem_dir needed - fill_missing_bpm_from_stem_grid must skip tracks
    # that already have a bpm before it even looks at the filesystem.
    filled = fill_missing_bpm_from_stem_grid([t], Path("/does/not/exist"))

    assert t.bpm == 129.5
    assert filled == {}


def test_matches_a_track_whose_name_is_html_escaped(tmp_path):
    """Real escaping inconsistency this project already works around
    elsewhere (see test_propose_arrangement_mixplan.py): SECTIONS_STEM_*.json's
    own "track" field is unescaped ("There's"), while TrackInfo.name built
    from the merged sections JSON can carry the escaped form
    ("There&apos;s") - confirmed directly against the real Tech House
    Heldout project's HARTY track."""
    _sections_json(tmp_path, "HARTY - There's A Party", 129.2)
    t = TrackInfo(name="HARTY - There&apos;s A Party", sections=[],
                  arr_start=0.0, arr_end=400.0)

    filled = fill_missing_bpm_from_stem_grid([t], tmp_path)

    assert t.bpm == 129.2
    assert filled == {"HARTY - There&apos;s A Party": 129.2}


def test_no_stem_dir_leaves_bpm_none(tmp_path):
    """A missing _Stem Analysis dir (e.g. a legacy/non-owned-grid project)
    is a real "no data available" case, not an error - t.bpm stays None,
    same as it always did before this fix, so the existing hard-raise in
    --tempo-arc still fires for a genuinely uncertified track."""
    t = TrackInfo(name="Some Track", sections=[], arr_start=0.0, arr_end=400.0)

    filled = fill_missing_bpm_from_stem_grid([t], tmp_path / "does_not_exist")

    assert t.bpm is None
    assert filled == {}


def test_track_absent_from_stem_dir_leaves_bpm_none(tmp_path):
    """The stem dir exists and has OTHER tracks, but not this one - stays
    None rather than picking up an unrelated track's BPM."""
    _sections_json(tmp_path, "A Different Track", 140.0)
    t = TrackInfo(name="Some Track", sections=[], arr_start=0.0, arr_end=400.0)

    filled = fill_missing_bpm_from_stem_grid([t], tmp_path)

    assert t.bpm is None
    assert filled == {}


def test_malformed_json_in_stem_dir_is_skipped_not_fatal(tmp_path):
    """A corrupt/partial SECTIONS_STEM_*.json (e.g. a run interrupted
    mid-write) must not crash the fallback for every OTHER track."""
    (tmp_path / "SECTIONS_STEM_Broken.json").write_text("{not valid json",
                                                         encoding="utf-8")
    _sections_json(tmp_path, "Good Track", 122.0)
    good = TrackInfo(name="Good Track", sections=[], arr_start=0.0, arr_end=400.0)
    broken = TrackInfo(name="Broken", sections=[], arr_start=0.0, arr_end=400.0)

    filled = fill_missing_bpm_from_stem_grid([good, broken], tmp_path)

    assert good.bpm == 122.0
    assert broken.bpm is None
    assert filled == {"Good Track": 122.0}


def test_zero_bpm_in_json_is_not_treated_as_a_real_value(tmp_path):
    """0 is falsy and not a real BPM - must not silently "fill" a track
    with a zero that would blow up downstream (60 <= bpm <= 200 checks)."""
    _sections_json(tmp_path, "Zero Track", 0)
    t = TrackInfo(name="Zero Track", sections=[], arr_start=0.0, arr_end=400.0)

    filled = fill_missing_bpm_from_stem_grid([t], tmp_path)

    assert t.bpm is None
    assert filled == {}


def test_non_numeric_bpm_string_does_not_crash_other_tracks(tmp_path):
    """Real bug found in review: the old bare float(bpm) raised uncaught on
    a non-numeric string, crashing the WHOLE fallback (every other track's
    real, valid BPM lost too) instead of just skipping the one bad file."""
    _sections_json(tmp_path, "Bad Track", "not-a-number")
    _sections_json(tmp_path, "Good Track", 128.0)
    bad = TrackInfo(name="Bad Track", sections=[], arr_start=0.0, arr_end=400.0)
    good = TrackInfo(name="Good Track", sections=[], arr_start=0.0, arr_end=400.0)

    filled = fill_missing_bpm_from_stem_grid([bad, good], tmp_path)

    assert bad.bpm is None
    assert good.bpm == 128.0
    assert filled == {"Good Track": 128.0}


def test_nan_and_out_of_range_bpm_are_rejected_not_silently_accepted(tmp_path):
    """A successful float() conversion is not enough on its own - nan is
    truthy and would have passed the old bare `if bpm:` check; a negative
    or absurd BPM is syntactically a valid float too. Both would have
    silently poisoned a --tempo-arc build (the exact thing this fallback
    exists to unblock) with garbage rather than being rejected."""
    _sections_json(tmp_path, "Nan Track", float("nan"))
    _sections_json(tmp_path, "Negative Track", -128.0)
    _sections_json(tmp_path, "Absurd Track", 5000.0)
    tracks = [TrackInfo(name=n, sections=[], arr_start=0.0, arr_end=400.0)
              for n in ("Nan Track", "Negative Track", "Absurd Track")]

    filled = fill_missing_bpm_from_stem_grid(tracks, tmp_path)

    assert all(t.bpm is None for t in tracks)
    assert filled == {}
