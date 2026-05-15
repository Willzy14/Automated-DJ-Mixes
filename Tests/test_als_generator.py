"""Tests for ALS template patching — decompress, patch, compress."""

import gzip
from pathlib import Path

import pytest

from automated_dj_mixes.als_generator import (
    TrackPatch,
    decompress_als,
    compress_als,
    generate_session,
    _find_track_line_ranges,
    _find_filter_target_id,
    _db_to_ableton_volume,
    _set_project_bpm,
)
from automated_dj_mixes.analysis import TrackAnalysis
from automated_dj_mixes.warping import WarpMarker
from automated_dj_mixes.automation import AutomationPoint


TEMPLATE = Path(__file__).resolve().parent.parent / "Templates" / "DJ Mix Template 2026.als"
TEMPLATE_EXISTS = TEMPLATE.exists()

skip_no_template = pytest.mark.skipif(
    not TEMPLATE_EXISTS, reason="ALS template not present"
)


def _fake_analysis(name="test_track", bpm=126.0, lufs=-8.5):
    return TrackAnalysis(
        path=Path(f"C:/fake/{name}.wav"),
        key="Am",
        camelot="8A",
        bpm=bpm,
        lufs=lufs,
        first_downbeat_sec=0.3,
        duration_sec=300.0,
        sample_rate=44100,
    )


def _fake_markers():
    return [
        WarpMarker(beat_time=0.0, sample_time=0.3),
        WarpMarker(beat_time=629.37, sample_time=300.0),
    ]


# --- Unit tests (no template needed) ---


def test_db_to_volume_zero():
    assert _db_to_ableton_volume(0.0) == pytest.approx(1.0)


def test_db_to_volume_minus6():
    assert _db_to_ableton_volume(-6.0) == pytest.approx(0.5012, rel=0.01)


def test_db_to_volume_minus12():
    assert _db_to_ableton_volume(-12.0) == pytest.approx(0.2512, rel=0.01)


# --- Template-based tests ---


@skip_no_template
def test_roundtrip_preserves_content(tmp_path):
    lines = decompress_als(TEMPLATE)
    out = tmp_path / "roundtrip.als"
    compress_als(lines, out)
    lines2 = decompress_als(out)
    assert lines == lines2


@skip_no_template
def test_find_track_ranges():
    lines = decompress_als(TEMPLATE)
    ranges = _find_track_line_ranges(lines)
    assert len(ranges) == 12
    assert ranges[0][2] == "Session Time"
    assert ranges[1][2] == "2-Audio"
    assert ranges[11][2] == "12-Audio"


@skip_no_template
def test_find_lp_filter_target():
    lines = decompress_als(TEMPLATE)
    ranges = _find_track_line_ranges(lines)
    start, end, _ = ranges[1]
    target = _find_filter_target_id(lines, start, end, "lp")
    assert target is not None
    assert target.isdigit()


@skip_no_template
def test_find_hp_filter_target():
    lines = decompress_als(TEMPLATE)
    ranges = _find_track_line_ranges(lines)
    start, end, _ = ranges[1]
    target = _find_filter_target_id(lines, start, end, "hp")
    assert target is not None
    assert target.isdigit()


@skip_no_template
def test_lp_and_hp_targets_differ():
    lines = decompress_als(TEMPLATE)
    ranges = _find_track_line_ranges(lines)
    start, end, _ = ranges[1]
    lp = _find_filter_target_id(lines, start, end, "lp")
    hp = _find_filter_target_id(lines, start, end, "hp")
    assert lp != hp


@skip_no_template
def test_generate_inserts_clip(tmp_path):
    patch = TrackPatch(
        analysis=_fake_analysis(),
        track_index=0,
        warp_markers=_fake_markers(),
    )
    out = tmp_path / "test.als"
    generate_session(TEMPLATE, [patch], out, project_bpm=128.0)
    lines = decompress_als(out)
    assert any("AudioClip" in l for l in lines)


@skip_no_template
def test_generate_sets_track_name(tmp_path):
    patch = TrackPatch(
        analysis=_fake_analysis("My Cool Track"),
        track_index=0,
        warp_markers=_fake_markers(),
    )
    out = tmp_path / "test.als"
    generate_session(TEMPLATE, [patch], out)
    lines = decompress_als(out)
    assert any('Value="My Cool Track"' in l and "EffectiveName" in l for l in lines)


@skip_no_template
def test_generate_inserts_warp_markers(tmp_path):
    patch = TrackPatch(
        analysis=_fake_analysis(),
        track_index=0,
        warp_markers=_fake_markers(),
    )
    out = tmp_path / "test.als"
    generate_session(TEMPLATE, [patch], out)
    lines = decompress_als(out)
    warp_lines = [l for l in lines if "WarpMarker" in l and "SecTime" in l]
    assert len(warp_lines) >= 2


@skip_no_template
def test_generate_sets_project_bpm(tmp_path):
    patch = TrackPatch(
        analysis=_fake_analysis(),
        track_index=0,
        warp_markers=_fake_markers(),
    )
    out = tmp_path / "test.als"
    generate_session(TEMPLATE, [patch], out, project_bpm=140.0)
    lines = decompress_als(out)
    found = False
    for i, line in enumerate(lines):
        if "<Tempo>" in line:
            for j in range(i, min(i + 5, len(lines))):
                if 'Manual Value="140.0"' in lines[j]:
                    found = True
            break
    assert found


@skip_no_template
def test_generate_with_automation(tmp_path):
    patches = [
        TrackPatch(
            analysis=_fake_analysis("trk_out"),
            track_index=0,
            warp_markers=_fake_markers(),
            arrangement_start_beats=0.0,
        ),
        TrackPatch(
            analysis=_fake_analysis("trk_in"),
            track_index=1,
            warp_markers=_fake_markers(),
            arrangement_start_beats=500.0,
        ),
    ]
    auto = {
        0: [("lp_filter", [
            AutomationPoint(500.0, 20000.0),
            AutomationPoint(628.0, 200.0),
        ])],
        1: [("hp_filter", [
            AutomationPoint(500.0, 500.0),
            AutomationPoint(564.0, 20.0),
        ])],
    }
    out = tmp_path / "test.als"
    generate_session(TEMPLATE, patches, out, transition_automation=auto)
    lines = decompress_als(out)
    events = [l for l in lines if "<FloatEvent" in l and "Time=\"500" in l or "Time=\"564" in l or "Time=\"628" in l]
    assert len(events) == 4


@skip_no_template
def test_generate_multiple_tracks(tmp_path):
    patches = [
        TrackPatch(
            analysis=_fake_analysis(f"track_{i}"),
            track_index=i,
            warp_markers=_fake_markers(),
            arrangement_start_beats=i * 500.0,
        )
        for i in range(5)
    ]
    out = tmp_path / "test.als"
    generate_session(TEMPLATE, patches, out)
    lines = decompress_als(out)
    clips = [l for l in lines if "AudioClip" in l and "Id=" in l]
    assert len(clips) == 5
    for i in range(5):
        assert any(f"track_{i}" in l for l in lines)
