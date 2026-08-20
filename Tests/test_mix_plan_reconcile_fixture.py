"""Reusable end-to-end reconcile fixture for Tests/.

This module builds a deterministic synthetic trio of artifacts - a MixPlan
(``mix_plan.json``), an arrangement report (``report.json``) and a
gzipped Ableton Live set (``fixture.als``) - that together round-trip
through ``validate_mix_plan_als.reconcile``. The whole point is to give
policy-conditional reconcile assertions something to chew on that is NOT a
hand-typed fragment: the plan's ``arrangement_start_beat`` /
``arrangement_end_beat`` / ``warp_marker_count`` / ``warp_grid_sha256`` /
``source_grid_bpm`` are derived from the ALS via
``summarize_track_warp_grids`` after the XML is written, and the
``plan_hash`` is then frozen via ``validate_mix_plan_als._canonical_hash``.
Geometry knobs move clip edges only; the contract fields on the plan
track the clip extents you actually wrote, so broken-boundary variants
fail ONLY the clip-boundary check.

BPM is fixed at 128.0 throughout so the warp-grid math
(``60 * 128 / 60 = 128``) is trivial to verify by eye.
"""

import gzip
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import validate_mix_plan_als  # noqa: E402
from automated_dj_mixes.warp_contract import summarize_track_warp_grids  # noqa: E402
from automated_dj_mixes.warping import WARP_MODE_REPITCH  # noqa: E402


SWAP_BEAT = 604.0
PROJECT_BPM = 128.0

# Marker (SecTime, BeatTime) pairs shared by every clip on both tracks.
# Two markers -> summarize_warp_grid is happy (>= 2 required).
# 60s / 128 beats == 60 / 128 BPM = 128 BPM, so source_grid_bpm == 128.0.
WARP_MARKER_PAIRS = (
    ('<WarpMarker Id="1" SecTime="0.0" BeatTime="0.0" />'
     '<WarpMarker Id="2" SecTime="60.0" BeatTime="128.0" />')
)


def _warp_markers_block() -> str:
    return '<WarpMarkers>' + "".join(WARP_MARKER_PAIRS) + '</WarpMarkers>'


def _clip_xml(clip_index: int, time: float, end: float) -> str:
    """One AudioClip element. Time is an ATTRIBUTE on the element so
    ``clip.get("Time")`` returns it (reconcile reads it that way).
    """
    return (
        f'<AudioClip Id="{clip_index}" Time="{time}">'
        f'<Name Value="clip_{clip_index}" />'
        f'<CurrentStart Value="{time}" />'
        f'<CurrentEnd Value="{end}" />'
        f'<WarpMode Value="{WARP_MODE_REPITCH}" />'
        f'{_warp_markers_block()}'
        f'</AudioClip>'
    )


def _track_envelopes_xml(pointee_a: int, pointee_b: int) -> str:
    """Two AutomationEnvelope elements with distinct PointeeIds. Each
    carries FloatEvents at (SWAP_BEAT, 1.0) and (612.0, 0.0) so that the
    per-track automation check sees >=2 envelopes AND >=2 envelopes
    carrying an event exactly at the swap beat.
    """
    return (
        '<AutomationEnvelopes><Envelopes>'
        f'<AutomationEnvelope Id="5000">'
        f'<EnvelopeTarget><PointeeId Value="{pointee_a}" /></EnvelopeTarget>'
        f'<Automation><Events>'
        f'<FloatEvent Id="1" Time="{SWAP_BEAT}" Value="1.0" />'
        f'<FloatEvent Id="2" Time="612.0" Value="0.0" />'
        f'</Events></Automation>'
        f'</AutomationEnvelope>'
        f'<AutomationEnvelope Id="5001">'
        f'<EnvelopeTarget><PointeeId Value="{pointee_b}" /></EnvelopeTarget>'
        f'<Automation><Events>'
        f'<FloatEvent Id="3" Time="{SWAP_BEAT}" Value="1.0" />'
        f'<FloatEvent Id="4" Time="612.0" Value="0.0" />'
        f'</Events></Automation>'
        f'</AutomationEnvelope>'
        '</Envelopes></AutomationEnvelopes>'
    )


def _track_xml(
    track_id_attr: int,
    name: str,
    clips_xml: str,
    pointee_a: int,
    pointee_b: int,
) -> str:
    """One AudioTrack. Name goes into ``EffectiveName`` because reconcile
    extracts names via ``next(track.iter("EffectiveName"), None)``.
    """
    return (
        f'<AudioTrack Id="{track_id_attr}">'
        f'<Name><EffectiveName Value="{name}" /></Name>'
        f'<DeviceChain>'
        f'<MainSequencer><Sample><ArrangerAutomation><Events>'
        f'{clips_xml}'
        f'</Events></ArrangerAutomation></Sample></MainSequencer>'
        f'</DeviceChain>'
        f'{_track_envelopes_xml(pointee_a, pointee_b)}'
        f'</AudioTrack>'
    )


def _build_als_xml(
    track_a_clips: str,
    track_b_clips: str,
) -> str:
    # MainTrack MUST come before AudioTracks (Live's own order). The
    # tempo AutomationTarget Id is declared but no envelope points at
    # it, so the fixed_center_v1 branch sees no tempo events.
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Ableton MajorVersion="5" MinorVersion="0">'
        '<MainTrack>'
        '<Tempo>'
        f'<Manual Value="{PROJECT_BPM}" />'
        '<AutomationTarget Id="77" />'
        '</Tempo>'
        '</MainTrack>'
        + _track_xml(1, "Alpha Track", track_a_clips, 1000, 1001)
        + _track_xml(2, "Beta Track", track_b_clips, 1010, 1011)
        + '</Ableton>'
    )


def build_reconcile_fixture(
    tmp_path,
    alignment_policy="paired_landmarks_v2",
    handoff_kind="paired/section:drop:end->drop",
    incoming_boundary_at_swap=True,
    outgoing_boundary_at_swap=True,
):
    """Build a synthetic plan+report+ALS triple that reconciles.

    Returns ``(plan_path, report_path, als_path)``. Two tracks, one
    transition, swap at beat 604.0. Geometry knobs move CLIP EDGES only;
    the plan's arrangement_start/end_beat fields are derived from the
    clips actually written, so a broken-boundary variant fails ONLY the
    clip-boundary check, never the arrangement-extent check.
    """

    # Track A (outgoing): if a real split is wanted at the swap, two
    # clips share an edge at 604.0; otherwise one continuous clip covers
    # the whole outgoing span (no edge at 604 -> boundary check fails).
    if outgoing_boundary_at_swap:
        track_a_clips = (
            _clip_xml(101, 0.0, SWAP_BEAT)
            + _clip_xml(102, SWAP_BEAT, 640.0)
        )
        track_a_starts = [0.0, SWAP_BEAT]
        track_a_ends = [SWAP_BEAT, 640.0]
    else:
        track_a_clips = _clip_xml(101, 0.0, 640.0)
        track_a_starts = [0.0]
        track_a_ends = [640.0]

    # Track B (incoming): real cue at 604 vs 8 beats early. The "starts
    # early" variant still produces a valid arrangement-extent (596..1280)
    # so the plan passes arrangement-start/end checks; only the clip
    # boundary check at 604 fails.
    if incoming_boundary_at_swap:
        track_b_clips = _clip_xml(201, SWAP_BEAT, 1280.0)
        track_b_starts = [SWAP_BEAT]
        track_b_ends = [1280.0]
    else:
        track_b_clips = _clip_xml(201, 596.0, 1280.0)
        track_b_starts = [596.0]
        track_b_ends = [1280.0]

    als_xml = _build_als_xml(track_a_clips, track_b_clips)

    # Gate: emitted XML must parse BEFORE we gzip. A bad fixture has
    # burned enough hours already.
    ET.fromstring(als_xml)

    als_path = tmp_path / "fixture.als"
    with gzip.open(als_path, "wb") as handle:
        handle.write(als_xml.encode("utf-8"))

    # Read it back and derive warp fields per track from the actual XML.
    with gzip.open(als_path, "rb") as handle:
        root = ET.fromstring(handle.read())

    track_a_el = next(
        track for track in root.iter("AudioTrack")
        if next(track.iter("EffectiveName")).get("Value") == "Alpha Track"
    )
    track_b_el = next(
        track for track in root.iter("AudioTrack")
        if next(track.iter("EffectiveName")).get("Value") == "Beta Track"
    )
    summary_a = summarize_track_warp_grids(track_a_el)
    summary_b = summarize_track_warp_grids(track_b_el)

    plan = {
        "project_bpm": PROJECT_BPM,
        "human_overrides": [],
        "policy_versions": [
            {"name": "tempo_strategy", "value": "fixed_center_v1"}
        ],
        "tracks": [
            {
                "display_name": "Alpha Track",
                "track_instance_id": "trk_a",
                "arrangement_start_beat": min(track_a_starts),
                "arrangement_end_beat": max(track_a_ends),
                "warp_mode": "repitch",
                "warp_marker_count": summary_a.marker_count,
                "warp_grid_sha256": summary_a.grid_sha256,
                "source_grid_bpm": summary_a.source_grid_bpm,
            },
            {
                "display_name": "Beta Track",
                "track_instance_id": "trk_b",
                "arrangement_start_beat": min(track_b_starts),
                "arrangement_end_beat": max(track_b_ends),
                "warp_mode": "repitch",
                "warp_marker_count": summary_b.marker_count,
                "warp_grid_sha256": summary_b.grid_sha256,
                "source_grid_bpm": summary_b.source_grid_bpm,
            },
        ],
        "loops": [],
        "transitions": [
            {
                "transition_id": "t1",
                "transition_index": 0,
                "out_track_instance_id": "trk_a",
                "in_track_instance_id": "trk_b",
            }
        ],
    }
    plan["plan_hash"] = validate_mix_plan_als._canonical_hash(plan)

    plan_path = tmp_path / "mix_plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    report = {
        "transitions": [
            {
                "out_track": "Alpha Track",
                "in_track": "Beta Track",
                "swap_beats": SWAP_BEAT,
                "handoff_kind": handoff_kind,
                "alignment_policy": alignment_policy,
            }
        ]
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    return plan_path, report_path, als_path


def test_fixture_good_paired_passes(tmp_path):
    """Default (paired, both boundaries at swap) must reconcile green.

    Pins the fixture itself: a future regression that drops the warp
    markers, breaks the plan_hash, or forgets an envelope will surface
    here first.
    """
    result = validate_mix_plan_als.reconcile(
        *build_reconcile_fixture(tmp_path)
    )
    assert result["status"] == "PASS"
    assert "paired_boundary:t1:outgoing" in result["checks"]
    assert "paired_boundary:t1:incoming" in result["checks"]


def test_fixture_broken_incoming_paired_raises(tmp_path):
    """Incoming with no clip boundary at the swap must raise.

    The clip starts at 596.0, so the boundary set is {596, 1280}, and
    604.0 is absent. reconcile() formats the failure as
    ``Transition 1 incoming track has no clip boundary at frozen
    swap 604 (policy paired_landmarks_v2)``.
    """
    with pytest.raises(ValueError, match="no clip boundary"):
        validate_mix_plan_als.reconcile(
            *build_reconcile_fixture(
                tmp_path, incoming_boundary_at_swap=False
            )
        )
