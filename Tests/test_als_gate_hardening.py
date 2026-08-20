"""Codex BLOCKER 6 — "corrupt or incomplete ALS can still ship" hardening.

Three gates, three test groups:

1. als_generator.generate_session must HARD-ERROR (never truncate) when the
   job needs more tracks than the template offers, and must reject patches
   whose track_index points past the template. Legitimate smaller-than-
   template jobs (unused tracks stay empty) still work — and now get a
   post-write expectation gate (track count + mixer devices).
2. apply_automation must HARD-ERROR (never warn) when a track has automation
   points to write but its Utility Gain / ChannelEq target cannot be
   resolved — that is a broken transition, not a cosmetic warning.
3. validate_als's new expectation layers are covered in
   test_validate_als_expectations.py; here we prove the POSITIVE path on the
   real V10 mix Sam approved (no false positives from the new gates).
"""

import gzip
import json
import sys
from pathlib import Path

import pytest

from automated_dj_mixes.als_generator import TrackPatch, generate_session
from automated_dj_mixes.analysis import TrackAnalysis
from automated_dj_mixes.warping import WarpMarker

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "Templates" / "DJ Mix Template 2026.als"
TEMPLATE_USABLE_TRACKS = 11  # 12 AudioTracks minus the Session Time track

V10_ALS = ROOT / "Test Project" / "14.08.26" / "Output" / "14.08.26 Mix V10.als"
V10_REPORT = ROOT / "Test Project" / "14.08.26" / "Output" / "ARRANGEMENT_REPORT_V10.json"

skip_no_template = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="ALS template not present"
)
skip_no_v10 = pytest.mark.skipif(
    not (V10_ALS.exists() and V10_REPORT.exists()),
    reason="Test Project/14.08.26 V10 output not in worktree",
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


def _patches(n, start_index=0):
    return [
        TrackPatch(
            analysis=_fake_analysis(f"track_{i}"),
            track_index=i,
            warp_markers=_fake_markers(),
        )
        for i in range(start_index, start_index + n)
    ]


# ── 1. generate_session truncation gate ──────────────────────────────────────


@skip_no_template
def test_generate_session_truncation_raises(tmp_path):
    """A 12-track job on an 11-usable-track template used to silently drop
    track 12 with only a WARNING print. Now it must raise, naming both
    counts and the template."""
    patches = _patches(TEMPLATE_USABLE_TRACKS + 1)
    with pytest.raises(ValueError, match="refusing to truncate") as exc:
        generate_session(TEMPLATE, patches, tmp_path / "overflow.als")
    msg = str(exc.value)
    assert str(TEMPLATE_USABLE_TRACKS) in msg
    assert str(TEMPLATE_USABLE_TRACKS + 1) in msg
    assert TEMPLATE.name in msg
    assert not (tmp_path / "overflow.als").exists(), (
        "no ALS may be written on a truncation error"
    )


@skip_no_template
def test_generate_session_out_of_range_index_raises(tmp_path):
    """Even when the patch COUNT fits, a track_index past the template's last
    track must raise instead of silently skipping the patch."""
    patch = TrackPatch(
        analysis=_fake_analysis(),
        track_index=TEMPLATE_USABLE_TRACKS,  # one past the end (0-based)
        warp_markers=_fake_markers(),
    )
    with pytest.raises(ValueError, match="track_index"):
        generate_session(TEMPLATE, [patch], tmp_path / "sparse.als")


@skip_no_template
def test_generate_session_smaller_job_still_works(tmp_path):
    """Legitimate smaller-than-template jobs must keep working: unused
    template tracks stay empty and the new post-write expectation gate
    (track count + mixer devices) passes on the written file."""
    out = tmp_path / "small.als"
    result = generate_session(TEMPLATE, _patches(2), out)
    assert result == out
    assert out.exists()


@skip_no_template
def test_generate_session_exact_fit_still_works(tmp_path):
    """A job using every usable template track is not truncation."""
    out = tmp_path / "full.als"
    generate_session(TEMPLATE, _patches(TEMPLATE_USABLE_TRACKS), out)
    assert out.exists()


# ── 2. apply_automation missing-target gate ──────────────────────────────────


def test_require_targets_raises_on_missing_volume():
    from apply_automation import _require_automation_targets

    with pytest.raises(ValueError, match="StereoGain") as exc:
        _require_automation_targets("Track A", True, False, None, "123")
    assert "Track A" in str(exc.value)


def test_require_targets_raises_on_missing_bass():
    from apply_automation import _require_automation_targets

    with pytest.raises(ValueError, match="ChannelEq"):
        _require_automation_targets("Track A", False, True, "123", None)


def test_require_targets_reports_both_missing():
    from apply_automation import _require_automation_targets

    with pytest.raises(ValueError) as exc:
        _require_automation_targets("Track A", True, True, None, None)
    msg = str(exc.value)
    assert "StereoGain" in msg and "ChannelEq" in msg


def test_require_targets_passes_when_resolved():
    from apply_automation import _require_automation_targets

    _require_automation_targets("Track A", True, True, "1", "2")  # no raise


def test_require_targets_exempts_tracks_without_points():
    """A track with no automation points has nothing to write — absent
    targets are legitimately optional there (e.g. a clip-only track with no
    transitions). Only load-bearing misses are errors."""
    from apply_automation import _require_automation_targets

    _require_automation_targets("Track A", False, False, None, None)  # no raise


def _write_sections_als_without_devices(path):
    """Two overlapping labelled tracks, NO StereoGain/ChannelEq devices —
    the 'stripped device chain' failure mode. Formats match the regexes in
    extract_sections_als.parse_sections_als and apply_automation's
    find_track_line_ranges."""

    def track(track_id, name, clip_id, time, end, label):
        dur = end - time
        return (
            f'<AudioTrack Id="{track_id}">\n'
            f'<Name><EffectiveName Value="{name}" /></Name>\n'
            f'<DeviceChain><MainSequencer><Sample><ArrangerAutomation><Events>\n'
            f'<AudioClip Id="{clip_id}" Time="{time}">\n'
            f'<Name Value="{label}" />\n'
            f'<CurrentStart Value="{time}" />\n'
            f'<CurrentEnd Value="{end}" />\n'
            f'<LoopStart Value="0.0" />\n'
            f'<LoopEnd Value="{dur}" />\n'
            f'<Color Value="10" />\n'
            f'</AudioClip>\n'
            f'</Events></ArrangerAutomation></Sample></MainSequencer></DeviceChain>\n'
            f'<AutomationEnvelopes><Envelopes /></AutomationEnvelopes>\n'
            f'</AudioTrack>\n'
        )

    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Ableton MajorVersion="5" MinorVersion="0">\n'
        '<MainTrack>\n<Tempo>\n<Manual Value="128.0" />\n</Tempo>\n</MainTrack>\n'
        + track(1, "Track Alpha", 11, 0.0, 800.0, "drop_1")
        + track(2, "Track Beta", 12, 500.0, 1400.0, "drop_1")
        + '</Ableton>\n'
    )
    with gzip.open(path, "wb") as f:
        f.write(xml.encode("utf-8"))


def test_apply_automation_missing_target_hard_fails(tmp_path, monkeypatch):
    """End-to-end: a sections ALS whose tracks lack the Utility/ChannelEq
    devices must make apply_automation.main() raise — the old behaviour
    printed '!! ... NO target found' and shipped an ALS without the planned
    transition automation."""
    from apply_automation import main

    als_in = tmp_path / "in.als"
    als_out = tmp_path / "out.als"
    _write_sections_als_without_devices(als_in)

    sections_json = tmp_path / "sections.json"
    sections_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "apply_automation.py",
        str(als_in),
        str(sections_json),
        str(als_out),
    ])
    with pytest.raises(ValueError, match="Automation target"):
        main()
    assert not als_out.exists(), (
        "no output ALS may be written when a transition target is missing"
    )


# ── 3. V10 positive path — the approved mix must pass the new gates ──────────


@skip_no_v10
def test_v10_passes_all_expectation_layers():
    """The V10 mix Sam approved must PASS validate_als with every new check
    active: its own report's track count, mixer-device presence on all
    active tracks, and envelope coverage for all planned transitions.
    A failure here is a real finding, never something to paper over."""
    from validate_als import MIXER_AUTOMATION_DEVICES, validate_als

    rep = json.loads(V10_REPORT.read_text(encoding="utf-8"))
    errs = validate_als(
        V10_ALS,
        expected_track_count=rep["track_count"],
        expected_track_devices=MIXER_AUTOMATION_DEVICES,
        expected_transitions=rep["transitions"],
    )
    assert errs == []
