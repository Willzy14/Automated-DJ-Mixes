"""R1/R3 outgoing-tail policy checks around a locked swap."""

import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as ae
from automated_dj_mixes.transition_policy import INTERIM_V1


def _pair(section_bars, *, swap=100.0):
    outgoing = ae.Track(
        name="out", bpm=128.0, spb=4 * 60.0 / 128.0, downbeat=0.0,
        n_bars=110.0,
        sections=[
            {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 100.0},
            {"name": "outro_1", "label": "outro", "start_bar": 100.0, "end_bar": 110.0},
        ],
        bass_in_bar=0.0, bass_out_bar=100.0, last_min_bars=64,
        loop_windows=[(100.0, 110.0)],
    )
    incoming = ae.Track(
        name="in", bpm=128.0, spb=4 * 60.0 / 128.0, downbeat=0.0,
        n_bars=80.0,
        sections=[{"name": f"break_{n}", "label": "break",
                   "start_bar": float(bar), "end_bar": float(bar) + 4.0}
                  for n, bar in enumerate(section_bars, 1)],
        bass_in_bar=0.0, bass_out_bar=72.0, last_min_bars=64,
    )
    alignment = ae.Alignment(
        "out", "in", swap, "outro_start", 0.0, 90.0, 40.0, 0,
        swap_beats=swap * 4, alignment_policy="paired_landmarks_v2",
    )
    return outgoing, incoming, alignment


def _tail(specs):
    return next((s for s in specs if s.kind == "outgoing_tail"), None)


def test_r1_skips_natural_end_on_section_line(monkeypatch):
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                        lambda *args, **kwargs: (100.0, 105.0))
    for section_bar in (18.0, 20.0, 22.0):
        outgoing, incoming, alignment = _pair((section_bar, 35.0))
        assert _tail(ae.plan_fill_or_cut(outgoing, incoming, alignment)) is None
        assert alignment.outgoing_loop_not_needed == {
            "reason": "natural end on section line", "section": "break_1"}
        assert alignment.outgoing_loop_abandoned is None


def test_r1_does_not_skip_when_swap_needs_loop(monkeypatch):
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                        lambda *args, **kwargs: (100.0, 104.0))
    outgoing, incoming, alignment = _pair((20.0, 32.0), swap=111.0)
    tail = _tail(ae.plan_fill_or_cut(outgoing, incoming, alignment))
    assert tail is not None
    assert tail.target_marker_name == "section:break_2"
    assert alignment.outgoing_loop_not_needed is None


def test_r3_caps_optional_15_bars_but_keeps_10(monkeypatch):
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                        lambda *args, **kwargs: (100.0, 105.0))
    for section_bar, expected in ((35.0, None), (30.0, "section:break_1")):
        outgoing, incoming, alignment = _pair((section_bar,))
        tail = _tail(ae.plan_fill_or_cut(outgoing, incoming, alignment))
        assert (tail.target_marker_name if tail else None) == expected
        if expected is None:
            assert alignment.outgoing_loop_not_needed == {
                "reason": "outgoing reach exceeds cap", "max_reach_bars": 12.0}


def test_r3_cap_cannot_create_short_swap_value_error(monkeypatch):
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                        lambda *args, **kwargs: (100.0, 105.0))
    outgoing, incoming, alignment = _pair((25.0, 35.0), swap=110.0)
    old_policy = replace(INTERIM_V1, max_outgoing_reach_bars=None)
    assert _tail(ae.plan_fill_or_cut(outgoing, incoming, alignment,
                                     policy=old_policy)) is not None
    outgoing, incoming, alignment = _pair((25.0, 35.0), swap=110.0)
    assert _tail(ae.plan_fill_or_cut(outgoing, incoming, alignment)) is None


def test_flags_off_preserve_old_decision(monkeypatch):
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                        lambda *args, **kwargs: (100.0, 105.0))
    old_policy = replace(INTERIM_V1,
                         skip_outgoing_loop_when_on_section_line=False,
                         max_outgoing_reach_bars=None)
    outgoing, incoming, alignment = _pair((20.0, 35.0))
    tail = _tail(ae.plan_fill_or_cut(outgoing, incoming, alignment,
                                     policy=old_policy))
    assert asdict(tail) == asdict(ae.FillCutSpec(
        kind="outgoing_tail", reps=3, source_start_bar=100.0,
        source_end_bar=105.0, target_marker_bar=125.0,
        target_marker_name="section:break_2",
        note="loop outro 5bx3+0b to section:break_2 125"))
    assert alignment.outgoing_loop_not_needed is None


def test_cap_suppresses_short_swap_error_that_old_policy_raises(monkeypatch):
    import pytest
    # A (5 bars out) yields a chunk but falls short of the swap; B (15 bars
    # out) is past the cap and has no usable chunk.
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                        lambda o, gap, **kw: (100.0, 105.0) if gap <= 5 else None)
    old_policy = replace(INTERIM_V1, max_outgoing_reach_bars=None,
                         skip_outgoing_loop_when_on_section_line=False)
    outgoing, incoming, alignment = _pair((25.0, 35.0), swap=110.0)
    with pytest.raises(ValueError, match="Cannot plan outgoing tail loop"):
        ae.plan_fill_or_cut(outgoing, incoming, alignment, policy=old_policy)
    outgoing, incoming, alignment = _pair((25.0, 35.0), swap=110.0)
    assert _tail(ae.plan_fill_or_cut(outgoing, incoming, alignment)) is None
    assert alignment.outgoing_loop_not_needed["reason"].startswith(
        "no loop within the reach cap")


def test_r1_prefers_section_at_or_after_natural_end(monkeypatch):
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                        lambda *args, **kwargs: (100.0, 105.0))
    outgoing, incoming, alignment = _pair((18.0, 22.0))   # E = 20: tie
    ae.plan_fill_or_cut(outgoing, incoming, alignment)
    assert alignment.outgoing_loop_not_needed["section"] == "break_2"


def test_not_needed_resets_between_calls(monkeypatch):
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                        lambda *args, **kwargs: (100.0, 105.0))
    outgoing, incoming, alignment = _pair((20.0, 35.0))
    ae.plan_fill_or_cut(outgoing, incoming, alignment)
    assert alignment.outgoing_loop_not_needed is not None
    old_policy = replace(INTERIM_V1,
                         skip_outgoing_loop_when_on_section_line=False,
                         max_outgoing_reach_bars=None)
    ae.plan_fill_or_cut(outgoing, incoming, alignment, policy=old_policy)
    assert alignment.outgoing_loop_not_needed is None


def test_no_outro_sets_nothing(monkeypatch):
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                        lambda *args, **kwargs: (100.0, 105.0))
    outgoing, incoming, alignment = _pair((20.0, 35.0))
    outgoing.sections = [s for s in outgoing.sections if s["label"] != "outro"]
    assert _tail(ae.plan_fill_or_cut(outgoing, incoming, alignment)) is None
    assert alignment.outgoing_loop_not_needed is None
