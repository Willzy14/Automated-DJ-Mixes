"""D19: an intro cut may not remove the whole intro. D22: a rejected ALS write
must not leave an output file behind."""
import gzip
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as ae
import apply_loops


def _pair(intro_end):
    outgoing = ae.Track(
        name="out", bpm=128.0, spb=4 * 60.0 / 128.0, downbeat=0.0, n_bars=110.0,
        sections=[
            {"name": "break_1", "label": "break", "start_bar": 84.0, "end_bar": 98.0},
            {"name": "drop_1", "label": "drop", "start_bar": 98.0, "end_bar": 110.0},
        ],
        bass_in_bar=0.0, bass_out_bar=100.0, last_min_bars=64, loop_windows=[])
    incoming = ae.Track(
        name="in", bpm=128.0, spb=4 * 60.0 / 128.0, downbeat=0.0, n_bars=80.0,
        sections=[
            {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": intro_end},
            {"name": "drop_1", "label": "drop", "start_bar": intro_end, "end_bar": 60.0},
        ],
        bass_in_bar=0.0, bass_out_bar=72.0, last_min_bars=64)
    alignment = ae.Alignment(
        "out", "in", 100.0, "outro_start", 0.0, 90.0, 40.0, 0,
        swap_beats=400.0, alignment_policy="paired_landmarks_v2")
    return outgoing, incoming, alignment


def _cuts(specs):
    return [s for s in specs if s.kind == "intro_cut"]


def test_intro_cut_never_removes_the_whole_intro():
    # cut would be 8 bars; the intro ends at 8.0018 -> must NOT cut
    o, i, al = _pair(8.0018)
    assert _cuts(ae.plan_fill_or_cut(o, i, al)) == []
    assert al.intro_cut_bars in (0, 0.0, None)


def test_intro_cut_still_fires_when_a_real_intro_remains():
    o, i, al = _pair(32.0)
    cuts = _cuts(ae.plan_fill_or_cut(o, i, al))
    assert cuts and cuts[0].cut_to_bar > 0
    assert 32.0 - cuts[0].cut_to_bar >= 1.0


def test_rejected_als_leaves_no_output_file(tmp_path):
    out = tmp_path / "Sections V9.als"
    with pytest.raises(Exception):
        apply_loops.compress_als(["<not-an-als>\n"], out)
    assert not out.exists()
    assert not list(tmp_path.glob("*.partial"))


def test_valid_als_is_published_and_partial_removed(tmp_path, monkeypatch):
    import validate_als
    monkeypatch.setattr(validate_als, "report_als", lambda p: [])
    out = tmp_path / "Sections V9.als"
    apply_loops.compress_als(["<Ableton/>\n"], out)
    assert out.exists() and not list(tmp_path.glob("*.partial"))
    assert gzip.open(out, "rb").read() == b"<Ableton/>\n"


def test_intro_cut_allowed_when_a_beat_or_more_remains():
    for intro_end in (8.5, 8.9):
        o, i, al = _pair(intro_end)
        cuts = _cuts(ae.plan_fill_or_cut(o, i, al))
        assert cuts and cuts[0].cut_to_bar == 8.0, intro_end


def test_apply_automation_writer_also_leaves_nothing_on_rejection(tmp_path):
    import apply_automation
    out = tmp_path / "Sections V9.als"
    with pytest.raises(Exception):
        apply_automation.compress_als(["<not-an-als>\n"], out)
    assert not out.exists()
    assert not list(tmp_path.glob("*.partial"))
