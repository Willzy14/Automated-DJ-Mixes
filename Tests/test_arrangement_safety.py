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
