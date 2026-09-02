"""Pins for validate_hints_vs_sections (first coverage — 2026-09-02).

Three behaviours earned on the 02.09.26 House 10 run:
1. Sections JSON keys carry ALS XML escapes ("&amp;") — tracks must still
   match their hint entries (three of ten tracks were silently skipped).
2. A hint entry that matches NO sections track is a hard error, not a
   silent drop.
3. first_drop_sec validates against the NEAREST drop-section start, and
   first_break_sec against the first break AFTER that anchor drop — a
   deliberate hint on a later drop boundary (drums-only early "drop")
   is a choice, not a disagreement.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from validate_hints_vs_sections import validate  # noqa: E402


def _sec(label, start_beats, end_beats):
    return {"label": label,
            "source_start_beats": start_beats,
            "source_end_beats": end_beats}


def _write_project(tmp_path, sections, hints, bpm=120.0,
                   stem_name="A &amp; B - Track 24 Bit MASTER"):
    proj = tmp_path / "proj"
    (proj / "Sections Review").mkdir(parents=True)
    (proj / "Hints").mkdir()
    (proj / "Output").mkdir()
    (proj / "_Stem Analysis").mkdir()
    (proj / "Sections Review" / "Sections_V1.json").write_text(
        json.dumps({stem_name: sections}), encoding="utf-8")
    (proj / "Hints" / "track_hints.json").write_text(
        json.dumps(hints), encoding="utf-8")
    # BPM fallback path: stem JSON named for the UNESCAPED wav stem.
    import html
    unescaped = html.unescape(stem_name)
    (proj / "_Stem Analysis" / f"SECTIONS_STEM_{unescaped}.json").write_text(
        json.dumps({"bpm": bpm}), encoding="utf-8")
    return proj


BASIC_SECTIONS = [
    _sec("intro", 0, 64),        # bars 0-16
    _sec("drop", 64, 128),       # bars 16-32 (drums-only early drop)
    _sec("break", 128, 256),     # bars 32-64 (pre-bass break)
    _sec("drop", 256, 416),      # bars 64-104 (the REAL bass drop)
    _sec("break", 416, 576),     # bars 104-144
    _sec("drop", 576, 764),      # bars 144-191
    _sec("outro", 764, 836),     # bars 191-209
]


def _hints_for_basic(first_drop_bar=64, first_break_bar=104):
    # 120 BPM -> 2.0 s/bar
    return {
        "A & B - Track 24 Bit MASTER.wav": {
            "first_drop_sec": first_drop_bar * 2.0,
            "first_break_sec": first_break_bar * 2.0,
            "outro_start_sec": 191 * 2.0,
            "last_bass_drop_sec": 176 * 2.0,
        }
    }


def test_amp_escaped_track_matches_and_passes(tmp_path):
    """'&amp;' sections key + '&' hint key must pair up and PASS."""
    proj = _write_project(tmp_path, BASIC_SECTIONS, _hints_for_basic())
    code, errors, report = validate(proj, version=1)
    assert code == 0, f"expected PASS, got {code}: {errors}\n{report}"
    assert "no matching sections track" not in report


def test_unmatched_hint_entry_is_hard_error(tmp_path):
    """A hint key matching no sections track must fail the gate loudly."""
    hints = _hints_for_basic()
    hints["Ghost Track 24 Bit MASTER.wav"] = {
        "first_drop_sec": 10.0, "first_break_sec": 20.0,
        "outro_start_sec": 30.0, "last_bass_drop_sec": 25.0,
    }
    proj = _write_project(tmp_path, BASIC_SECTIONS, hints)
    code, errors, report = validate(proj, version=1)
    assert code != 0
    assert "no matching sections track" in report


def test_first_drop_matches_nearest_drop_not_only_first(tmp_path):
    """A first_drop hint on the bar-64 drop must match that drop (0 bars
    off), not error against the drums-only bar-16 drop; first_break then
    anchors to the break AFTER bar 64."""
    proj = _write_project(tmp_path, BASIC_SECTIONS, _hints_for_basic())
    code, errors, report = validate(proj, version=1)
    assert code == 0, f"{errors}\n{report}"
    # And a hint far from EVERY drop start still errors.
    proj2 = _write_project(
        tmp_path / "sub", BASIC_SECTIONS,
        _hints_for_basic(first_drop_bar=84))  # 20 bars from nearest drop
    code2, errors2, report2 = validate(proj2, version=1)
    assert code2 != 0
