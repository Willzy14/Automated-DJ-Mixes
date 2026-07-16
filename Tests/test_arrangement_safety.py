import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def _track(name, *, n_bars=128, sections=None, bass_in=0.0, bass_out=120.0,
           loop_windows=None):
    from align_engine import Track

    sections = sections or [
        {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 96.0},
        {"name": "outro_1", "label": "outro", "start_bar": 96.0, "end_bar": float(n_bars)},
    ]
    return Track(
        name=name,
        bpm=126.0,
        spb=4 * 60.0 / 126.0,
        downbeat=0.0,
        n_bars=n_bars,
        sections=sections,
        bass_in_bar=bass_in,
        bass_out_bar=bass_out,
        last_min_bars=64,
        loop_windows=loop_windows or [],
    )


def test_align_pair_hard_caps_overlap_and_prefers_smaller_tie(monkeypatch):
    import align_engine

    outgoing = _track("out")
    incoming = _track("in", bass_in=0.0)
    monkeypatch.setattr(
        align_engine,
        "_handoff_candidates",
        lambda _track: [(80.0, "drop", "outro"), (96.0, "drop", "outro")],
    )
    monkeypatch.setattr(align_engine, "_score_lineup", lambda *_args: 0)

    alignment = align_engine.align_pair(outgoing, incoming)

    assert alignment.overlap_bars == 32.0
    assert alignment.overlap_bars <= align_engine.MAX_OVERLAP_BARS


def test_landmark_alignment_preserves_odd_bar_section_pairing():
    from align_engine import align_pair

    outgoing = _track(
        "out",
        n_bars=194,
        sections=[
            {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 175.0},
            {"name": "outro_1", "label": "outro", "start_bar": 175.0, "end_bar": 194.0},
        ],
    )
    incoming = _track(
        "in",
        n_bars=186,
        sections=[
            {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 8.0},
            {"name": "drop_1", "label": "drop", "start_bar": 8.0, "end_bar": 16.0},
            {"name": "build_1", "label": "build", "start_bar": 16.0, "end_bar": 20.0},
            {"name": "drop_2", "label": "drop", "start_bar": 20.0, "end_bar": 160.0},
            {"name": "outro_1", "label": "outro", "start_bar": 160.0, "end_bar": 186.0},
        ],
    )
    outgoing.musical_landmarks = [{
        "landmark_id": "kick_gap_190_194",
        "type": "kick_dropout",
        "start_bar": 190.0,
        "end_bar": 194.0,
    }]

    alignment = align_pair(outgoing, incoming)

    assert alignment.alignment_policy == "paired_landmarks_v2"
    assert alignment.arr_offset_bars == 167.0
    assert alignment.handoff_bar_out == 175.0
    assert alignment.swap_progress == pytest.approx(8 / 27)


def test_landmark_loop_reaches_named_cue_without_random_intro_loop():
    from align_engine import Alignment, plan_fill_or_cut

    outgoing = _track(
        "out",
        n_bars=213,
        sections=[
            {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 192.0},
            {"name": "outro_1", "label": "outro", "start_bar": 192.0, "end_bar": 213.0},
        ],
        loop_windows=[(192.0, 209.0)],
    )
    incoming = _track(
        "in",
        n_bars=194,
        sections=[
            {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 32.0},
            {"name": "drop_1", "label": "drop", "start_bar": 32.0, "end_bar": 96.0},
            {"name": "outro_1", "label": "outro", "start_bar": 168.0, "end_bar": 194.0},
        ],
    )
    incoming.musical_landmarks = [{
        "landmark_id": "kick_gap_224_232",
        "type": "kick_dropout",
        "start_bar": 56.0,
        "end_bar": 58.0,
    }]
    alignment = Alignment(
        "out", "in", 208.0, "paired/dropout->drop", 32.0,
        176.0, 37.0, 3, alignment_policy="paired_landmarks_v2",
    )

    specs = plan_fill_or_cut(outgoing, incoming, alignment)

    assert not any(spec.kind == "incoming_intro" for spec in specs)
    tail = next(spec for spec in specs if spec.kind == "outgoing_tail")
    assert tail.target_marker_name == "landmark:kick_gap_224_232:end"
    assert (
        tail.reps * (tail.source_end_bar - tail.source_start_bar)
        + tail.partial_bars
    ) == 21.0
    assert tail.source_end_bar - tail.source_start_bar == 4.0
    assert alignment.overlap_bars + 21.0 == 58.0


def test_intro_and_outro_loops_share_remaining_overlap_budget():
    from align_engine import Alignment, MAX_OVERLAP_BARS, plan_fill_or_cut

    outgoing = _track(
        "out",
        sections=[
            {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 96.0},
            {"name": "outro_1", "label": "outro", "start_bar": 96.0, "end_bar": 128.0},
        ],
        loop_windows=[(96.0, 128.0)],
    )
    incoming = _track(
        "in",
        sections=[
            {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 32.0},
            {"name": "drop_1", "label": "drop", "start_bar": 32.0, "end_bar": 64.0},
            {"name": "break_1", "label": "break", "start_bar": 64.0, "end_bar": 96.0},
            {"name": "drop_2", "label": "drop", "start_bar": 96.0, "end_bar": 128.0},
        ],
        loop_windows=[(0.0, 32.0)],
    )
    alignment = Alignment(
        out_name="out",
        in_name="in",
        handoff_bar_out=100.0,
        handoff_kind="drop->outro",
        anchor_bar_in=0.0,
        arr_offset_bars=100.0,
        overlap_bars=40.0,
        score=0,
    )

    specs = plan_fill_or_cut(outgoing, incoming, alignment)
    loop_bars = sum(
        spec.reps * (spec.source_end_bar - spec.source_start_bar) + spec.partial_bars
        for spec in specs
        if spec.kind in {"incoming_intro", "outgoing_tail"}
    )

    assert alignment.overlap_bars + loop_bars <= MAX_OVERLAP_BARS
    assert all(spec.reps <= 8 for spec in specs)


def test_loop_spec_rejects_excessive_repeat_or_extension():
    from apply_loops import LoopSpec, validate_loop_spec

    with pytest.raises(ValueError, match="repeat cap"):
        validate_loop_spec(LoopSpec("track", 0, 16, 9, 0))

    with pytest.raises(ValueError, match="extension cap"):
        validate_loop_spec(LoopSpec("track", 0, 32, 5, 0))


def test_arrangement_plan_rejects_final_overlap_above_cap():
    from propose_arrangement import (
        ArrangementPlan,
        OverlapAnalysis,
        TrackInfo,
        validate_arrangement_plan,
    )

    tracks = [
        TrackInfo("out", [], 0.0, 400.0),
        TrackInfo("in", [], 160.0, 560.0),
    ]
    overlap = OverlapAnalysis(
        out_track="out",
        in_track="in",
        pair_index=1,
        overlap_start=160.0,
        overlap_end=400.0,
        overlap_beats=240.0,
        overlap_bars=60.0,
        status="ok",
    )
    plan = ArrangementPlan(tracks, [overlap], [], [])

    with pytest.raises(ValueError, match="48-bar cap"):
        validate_arrangement_plan(plan)


def test_named_landmark_policy_allows_a_56_bar_overlap():
    from propose_arrangement import (
        ArrangementPlan,
        OverlapAnalysis,
        TrackInfo,
        validate_arrangement_plan,
    )

    tracks = [
        TrackInfo("out", [], 0.0, 400.0),
        TrackInfo("in", [], 176.0, 576.0),
    ]
    overlap = OverlapAnalysis(
        "out", "in", 1, 176.0, 400.0, 224.0, 56.0, "ok",
        overlap_policy="named_landmark_64",
        loop_target_marker="landmark:kick_gap:end",
    )

    validate_arrangement_plan(ArrangementPlan(tracks, [overlap], [], []))


def test_cue_bounded_loop_preserves_swap_and_later_target():
    from types import SimpleNamespace

    from align_engine import pick_cue_bounded_drum_loop

    track = SimpleNamespace(
        loop_windows=[(180, 200)],
        vocal_regions=[],
        fills=[],
    )

    chunk = pick_cue_bounded_drum_loop(
        track,
        gap_bars=21,
        required_boundary_bars=16,
    )

    assert chunk is not None
    assert chunk[1] - chunk[0] == 4


def test_als_writer_fails_closed_when_post_write_validation_fails(tmp_path):
    from apply_loops import compress_als

    with pytest.raises(ValueError, match="ALS validation failed"):
        compress_als(["<Ableton>\n"], tmp_path / "invalid.als")


def test_playback_policy_sets_tempo_and_every_clip_warp_mode():
    from propose_arrangement import TrackInfo, apply_playback_policy

    lines = [
        '<AudioTrack Id="1">\n',
        '  <EffectiveName Value="out" />\n',
        '  <WarpMode Value="4" />\n',
        '  <WarpMode Value="4" />\n',
        '</AudioTrack>\n',
        '<MainTrack>\n',
        '  <AutomationEnvelopes>\n',
        '    <Envelopes>\n',
        '      <AutomationEnvelope Id="20">\n',
        '        <EnvelopeTarget><PointeeId Value="8" /></EnvelopeTarget>\n',
        '        <Automation><Events>\n',
        '          <FloatEvent Time="0" Value="123" />\n',
        '        </Events></Automation>\n',
        '      </AutomationEnvelope>\n',
        '    </Envelopes>\n',
        '  </AutomationEnvelopes>\n',
        '<Tempo>\n',
        '  <Manual Value="123" />\n',
        '</Tempo>\n',
        '</MainTrack>\n',
    ]
    apply_playback_policy(lines, [TrackInfo("out", [], 0.0, 128.0)], 120.49, 6)

    assert 'Manual Value="120.49"' in "".join(lines)
    assert "".join(lines).count('<WarpMode Value="6" />') == 2
    assert '<PointeeId Value="8" />' not in "".join(lines)
    assert '<FloatEvent Time="0" Value="123" />' not in "".join(lines)


def test_explicit_arrangement_report_is_required_and_preserves_swap(tmp_path):
    import json
    from apply_automation import _load_arrangement_report

    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="was not found"):
        _load_arrangement_report(tmp_path / "mix.als", missing)

    report = tmp_path / "report.json"
    report.write_text(json.dumps({"transitions": [{
        "out_track": "out",
        "in_track": "in",
        "swap_beats": 448.0,
        "handoff_kind": "drop->outro",
    }]}), encoding="utf-8")
    swaps = _load_arrangement_report(tmp_path / "mix.als", report)
    assert swaps[("out", "in")]["swap_beats"] == 448.0


def test_arrangement_report_swap_matches_xml_encoded_track_name(tmp_path):
    import json
    from apply_automation import (
        TrackInfo,
        _load_arrangement_report,
        plan_transitions,
    )

    report = tmp_path / "report.json"
    report.write_text(json.dumps({"transitions": [{
        "out_track": "Blank & Jones",
        "in_track": "Aight",
        "swap_beats": 448.0,
        "handoff_kind": "drop->outro",
    }]}), encoding="utf-8")
    swaps = _load_arrangement_report(tmp_path / "mix.als", report)
    tracks = [
        TrackInfo("Blank &amp; Jones", [], 0.0, 532.0),
        TrackInfo("Aight", [], 352.0, 900.0),
    ]

    planned = plan_transitions(tracks, swaps)

    assert planned[0].bass_swap == 448.0
    assert planned[0].reason == "align_engine drop->outro"


def test_report_swap_near_overlap_start_preserves_loop_boundary():
    from apply_automation import TrackInfo, plan_transitions

    tracks = [
        TrackInfo("out", [], 0.0, 724.0),
        TrackInfo("in", [], 576.0, 1000.0),
    ]
    swaps = {
        ("out", "in"): {
            "swap_beats": 580.0,
            "handoff_kind": "drop->outro",
        }
    }

    planned = plan_transitions(tracks, swaps)

    assert planned[0].bass_swap == 580.0


def test_outgoing_marker_loop_never_uses_the_fading_outro_as_source():
    from types import SimpleNamespace
    from propose_arrangement import OverlapAnalysis, TrackInfo, _plan_marker_loops

    outgoing = TrackInfo("out", [
        {"name": "drop_2", "label": "drop", "arr_time": 352.0,
         "arr_end": 448.0, "source_start_beats": 352.0,
         "source_end_beats": 448.0},
        {"name": "outro_1", "label": "outro", "arr_time": 448.0,
         "arr_end": 484.0, "source_start_beats": 448.0,
         "source_end_beats": 484.0},
    ], 0.0, 484.0)
    incoming = TrackInfo("in", [], 352.0, 900.0)
    analysis = OverlapAnalysis("out", "in", 1, 352.0, 484.0, 132.0, 33.0, "ok")
    fill = SimpleNamespace(
        kind="outgoing_tail", reps=3, partial_bars=0.0,
        source_start_bar=112.0, source_end_bar=116.0,
    )

    _plan_marker_loops(outgoing, incoming, SimpleNamespace(fills_cuts=[fill]), analysis)

    assert analysis.out_tail_loop.source_beat_start == 432.0
    assert analysis.out_tail_loop.source_beat_end == 448.0


def test_overlap_landmarks_are_reported_without_changing_alignment():
    from align_engine import Alignment, report_landmark_candidates

    outgoing = _track("out")
    incoming = _track("in")
    incoming.musical_landmarks = [{
        "landmark_id": "kick_gap_92_96",
        "type": "pre_drop_kick_gap",
        "start_beat": 92,
        "end_beat": 96,
        "duration_beats": 4,
        "section_name": "intro_1",
        "confidence": "high",
        "candidate_roles": ["transition_end"],
    }]
    alignment = Alignment(
        "out", "in", 112.0, "drop->outro", 0.0, 88.0, 40.0, 2,
        swap_beats=448.0,
    )

    candidates = report_landmark_candidates(
        outgoing, incoming, alignment, 0.0, 352.0
    )

    assert alignment.swap_beats == 448.0
    assert len(candidates) == 1
    assert candidates[0]["suggested_transition_finish_beat"] == 448.0
    assert candidates[0]["distance_from_current_swap_beats"] == 0.0
    assert candidates[0]["selected"] is False


def test_report_landmark_geometry_moves_with_inserted_tail_loop():
    from types import SimpleNamespace
    from apply_loops import LoopSpec
    from propose_arrangement import ArrangementPlan, _final_landmark_candidates

    loop = LoopSpec("out", 432.0, 448.0, 3, 448.0)
    alignment = SimpleNamespace(
        swap_beats=448.0,
        landmark_candidates=[{
            "track_name": "out",
            "track_role": "outgoing",
            "source_start_beat": 448.0,
            "source_end_beat": 484.0,
            "arrangement_start_beat": 448.0,
            "arrangement_end_beat": 484.0,
            "suggested_transition_finish_beat": 448.0,
        }],
    )
    plan = ArrangementPlan([], [], [], [loop])

    candidates = _final_landmark_candidates(plan, alignment)

    assert candidates[0]["arrangement_start_beat"] == 496.0
    assert candidates[0]["arrangement_end_beat"] == 532.0
    assert candidates[0]["suggested_transition_finish_beat"] == 496.0
    assert candidates[0]["distance_from_current_swap_beats"] == 48.0
