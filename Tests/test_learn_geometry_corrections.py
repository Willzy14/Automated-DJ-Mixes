"""Geometry corrections in learn_from_correction (burn list D12).

Shapes are taken from Sam's 2026-09-15 edits to the 15.09.26 August Releases
Mix, where the automation diff alone labelled 5 of 11 transitions and got two
of those wrong. Beats are arrangement beats; source beats are the clip's
LoopStart/LoopEnd.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from learn_from_correction import (  # noqa: E402
    TrackAutomation,
    _repeat_groups,
    analyse_transitions,
    ordered_tracks_from_clip_records,
)


def _clip(name, start, end, source_start, source_end=None):
    return {
        "name": name,
        "label": name.rsplit("_", 1)[0],
        "label_n": 1,
        "arr_time": float(start),
        "arr_end": float(end),
        "source_start_beats": float(source_start),
        "source_end_beats": float(
            source_end if source_end is not None else source_start + (end - start)),
    }


def _copies(name, start, source_start, source_end, reps):
    length = source_end - source_start
    return [_clip(name, start + i * length, start + (i + 1) * length,
                  source_start, source_end) for i in range(reps)]


def _labels(groups):
    return [g.label() for g in groups]


# ── repeat-group detection ──────────────────────────────────────────────────

def test_pipeline_tail_loop_counts_its_natural_first_copy():
    """apply_loops puts copy 1 where the source was anyway; the report calls
    that 2bx7+0b, so the group must include it."""
    clips = [_clip("drop_4", 0, 784, 0, 784)]
    clips += _copies("outro_1_tail_loop", 784, 784, 792, 7)
    clips += [_clip("outro_1", 840, 912, 784, 856)]
    assert _labels(_repeat_groups(clips)) == ["2bx7+0b"]


def test_hand_made_last_bars_loop_with_trailing_partial():
    """Zaro: the last 2 bars repeated three times, then one bar of it."""
    clips = [_clip("drop_2", 2160, 2288, 400, 528)]
    clips += _copies("drop_2", 2288, 520, 528, 3)
    clips += [_clip("drop_2", 2312, 2316, 520, 524)]
    assert _labels(_repeat_groups(clips)) == ["2bx3+1b"]


def test_the_real_outro_after_loop_copies_is_not_a_partial():
    """HNTR: two 4-bar copies, then the outro restarts on the same source
    beat but runs 8 bars - a section, not a partial repeat."""
    clips = [_clip("drop_5", 1804, 1900, 576, 672)]
    clips += _copies("outro_1_tail_loop", 1900, 672, 688, 2)
    clips += [_clip("outro_1", 1932, 1964, 672, 704)]
    assert _labels(_repeat_groups(clips)) == ["4bx2+0b"]


def test_forward_skip_and_landmark_split_are_not_loops():
    """A cut (RUZE's outro jumps 800 -> 816) and a clip split at the swap
    (Zaro's drop_2) both keep moving forward in source."""
    cut = [_clip("drop_4", 4480, 4668, 576, 764),
           _clip("outro_1", 4668, 4672, 764, 768),
           _clip("outro_1", 4672, 4704, 768, 800),
           _clip("outro_1", 4704, 4724, 816, 836)]
    split = [_clip("drop_2", 2156, 2220, 400, 464),
             _clip("drop_2", 2220, 2284, 464, 528)]
    assert _repeat_groups(cut) == []
    assert _repeat_groups(split) == []


def test_bridge_from_a_skipped_gap_is_not_a_loop():
    """Review, 2026-09-15: after a forward skip, a short clip from INSIDE the
    skipped gap is new material. The old [min, max] check called it a loop."""
    clips = [_clip("intro_1", 0, 256, 0, 256),
             _clip("drop_2", 256, 456, 500, 700),
             _clip("bridge_1", 456, 496, 300, 340)]
    assert _repeat_groups(clips) == []


def test_one_off_repeat_of_the_previous_clips_tail_is_a_loop():
    """Yellody (T11): the outro's last bar played once more."""
    clips = [_clip("outro_1", 5464, 5524, 612, 672),
             _clip("outro_1", 5524, 5528, 668, 672)]
    assert _labels(_repeat_groups(clips)) == ["1bx1+0b"]


def test_one_off_revisit_of_earlier_material_is_an_edit_not_a_loop():
    """A single copy that is not the previous clip's tail is a hand edit."""
    clips = [_clip("drop_1", 0, 128, 0, 128),
             _clip("drop_2", 128, 256, 128, 256),
             _clip("drop_1", 256, 272, 32, 48),
             _clip("outro_1", 272, 336, 256, 320)]
    assert _repeat_groups(clips) == []


def test_intro_loop_copies_before_the_natural_intro():
    """An intro loop replays a chunk before the track proper starts; the
    real intro after it starts outside the chunk and is left alone."""
    clips = _copies("intro_1_intro_loop", 700, 16, 32, 6)
    clips += [_clip("intro_1", 796, 860, 0, 64)]
    assert _labels(_repeat_groups(clips)) == ["4bx6+0b"]


# ── transition labels ───────────────────────────────────────────────────────

def _run(baseline, corrected, c_auto, s_auto):
    return analyse_transitions(
        c_auto, s_auto,
        ordered_tracks_from_clip_records(baseline),
        ordered_tracks_from_clip_records(corrected))


def test_tail_loop_removed_and_swap_on_loop_marks_bass_swap_unreliable():
    """T6, Tommy Farrow -> Arielle Free. The pipeline's swap sat on the first
    copy of a tail loop cut from the intro (source 0..32); Sam removed the
    loop and let the track run out. The swap did not move on the incoming
    (bar 16 both times). The old label read bass_swap_moved:-64beats."""
    baseline = {
        "Out": [_clip("drop_3", 2732, 2932, 448, 648)]
               + _copies("outro_1_tail_loop", 2932, 0, 32, 4)
               + [_clip("outro_1", 3060, 3124, 648, 712)],
        "In": [_clip("intro_1", 2868, 2932, 0, 64),
               _clip("drop_1", 2932, 3124, 64, 256)],
    }
    corrected = {
        "Out": [_clip("drop_3", 2864, 3124, 448, 708)],
        "In": [_clip("intro_1", 3000, 3064, 0, 64),
               _clip("drop_1", 3064, 3256, 64, 256)],
    }
    c_auto = {"Out": TrackAutomation("Out", bass_points=[(2932.0, 1.0), (2932.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(2868.0, 0.18), (2932.0, 0.18), (2932.0, 1.0)])}
    s_auto = {"Out": TrackAutomation("Out", bass_points=[(3064.0, 1.0), (3064.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(3000.0, 0.18), (3064.0, 0.18), (3064.0, 1.0)])}

    [td] = _run(baseline, corrected, c_auto, s_auto)

    assert td.bass_swap_reliable is False
    assert not any(c.startswith("bass_swap_moved") for c in td.corrections)
    assert "tail_loop_removed:8bx4+0b" in td.corrections
    assert "overlap_changed:64->31bars" in td.corrections
    assert not any(c.startswith("swap_moved") for c in td.corrections)
    assert td.geometry["swap_in_bar"] == [16.0, 16.0]
    assert td.geometry["swap_out_on_loop"] == [True, False]
    assert td.geometry["entry_out_bar"] == [146.0, 146.0]
    assert td.geometry["tail_after_swap_bars"] == [48.0, 15.0]
    assert td.verdict == "corrected"


def test_intro_trim_reversed_entry_later_swap_removed_loop_added():
    """T4, Zaro -> Pat Premier. Sam restored the 16-bar intro trim, brought
    Pat Premier in 16 bars later (at the kick dropout), looped Zaro's last 2
    bars, and did no EQ swap at all."""
    baseline = {
        "Out": [_clip("drop_2", 2156, 2220, 400, 464),
                _clip("drop_2", 2220, 2284, 464, 528)],
        "In": [_clip("intro_1", 2156, 2220, 64, 128),
               _clip("drop_1", 2220, 2316, 128, 224)],
    }
    corrected = {
        "Out": [_clip("drop_2", 2160, 2288, 400, 528)]
               + _copies("drop_2", 2288, 520, 528, 3)
               + [_clip("drop_2", 2312, 2316, 520, 524)],
        "In": [_clip("intro_1", 2224, 2352, 0, 128),
               _clip("drop_1", 2352, 2448, 128, 224)],
    }
    c_auto = {"Out": TrackAutomation("Out", bass_points=[(2220.0, 1.0), (2220.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(2156.0, 0.18), (2220.0, 0.18), (2220.0, 1.0)])}
    s_auto = {"Out": TrackAutomation("Out", bass_points=[(2224.0, 1.0), (2318.0, 0.97)]),
              "In": TrackAutomation("In", bass_points=[(2224.0, 0.52), (2286.0, 0.75), (2352.0, 1.0)])}

    [td] = _run(baseline, corrected, c_auto, s_auto)

    assert "intro_trim:16->0bars" in td.corrections
    assert "entry_moved_out:+16bars" in td.corrections
    assert "swap_removed" in td.corrections
    assert "tail_loop_added:2bx3+1b" in td.corrections
    assert "overlap_changed:32->23bars" in td.corrections
    assert td.geometry["swap_arr_beat"] == [2220.0, None]


def test_swap_moved_on_the_incoming_to_its_bass_in():
    """T7, Arielle Free -> Coldabank. Whole intro restored (9-bar trim gone),
    swap moved from Coldabank's drop_1 (bar 32) to its intro bar 8, on the
    same Arielle bar (her outro start, 115)."""
    baseline = {
        "Out": [_clip("drop_3", 3236, 3328, 368, 460),
                _clip("outro_1", 3328, 3356, 460, 488)],
        "In": [_clip("intro_1", 3236, 3296, 36, 96),
               _clip("break_1", 3296, 3328, 96, 128),
               _clip("drop_1", 3328, 3392, 128, 192)],
    }
    corrected = {
        "Out": [_clip("drop_3", 3368, 3460, 368, 460),
                _clip("outro_1", 3460, 3488, 460, 488)],
        "In": [_clip("intro_1", 3428, 3524, 0, 96),
               _clip("break_1", 3524, 3556, 96, 128),
               _clip("drop_1", 3556, 3620, 128, 192)],
    }
    c_auto = {"Out": TrackAutomation("Out", bass_points=[(3328.0, 1.0), (3328.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(3236.0, 0.18), (3328.0, 0.18), (3328.0, 1.0)])}
    s_auto = {"Out": TrackAutomation("Out", bass_points=[(3460.0, 1.0), (3460.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(3428.0, 0.18), (3460.0, 0.18), (3460.0, 1.0)])}

    [td] = _run(baseline, corrected, c_auto, s_auto)

    assert "swap_moved_in:-24bars" in td.corrections
    assert "intro_trim:9->0bars" in td.corrections
    assert "entry_moved_out:+15bars" in td.corrections
    assert not any(c.startswith("swap_moved_out") for c in td.corrections)
    assert td.geometry["swap_out_bar"] == [115.0, 115.0]
    assert td.geometry["swap_in_bar"] == [32.0, 8.0]


def test_outro_cut_and_swap_moved_on_the_outgoing():
    """T9, Switch Disco -> RUZE. RUZE enters 4 bars later so its drop lands
    after the fill; Switch Disco then skips 10 bars and its 2-bar x8 tail
    loop is gone. In Switch Disco's source the swap moved +14 bars (the
    4-bar shift plus the 10-bar cut); on RUZE it did not move."""
    baseline = {
        "Out": [_clip("drop_4", 3872, 3968, 384, 480),
                _clip("fill_2", 3968, 3984, 480, 496),
                _clip("drop_5", 3984, 4064, 496, 576)]
               + _copies("outro_1_tail_loop", 4064, 576, 584, 8)
               + [_clip("outro_1", 4128, 4160, 576, 608)],
        "In": [_clip("intro_1", 3904, 3968, 0, 64),
               _clip("drop_1", 3968, 4032, 64, 128)],
    }
    corrected = {
        "Out": [_clip("drop_4", 4100, 4196, 384, 480),
                _clip("fill_2", 4196, 4212, 480, 496),
                _clip("outro_1", 4212, 4284, 536, 608)],
        "In": [_clip("intro_1", 4148, 4212, 0, 64),
               _clip("drop_1", 4212, 4276, 64, 128)],
    }
    c_auto = {"Out": TrackAutomation("Out", bass_points=[(3968.0, 1.0), (3968.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(3904.0, 0.18), (3968.0, 0.18), (3968.0, 1.0)])}
    s_auto = {"Out": TrackAutomation("Out", bass_points=[(4212.0, 1.0), (4212.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(4148.0, 0.18), (4212.0, 0.18), (4212.0, 1.0)])}

    [td] = _run(baseline, corrected, c_auto, s_auto)

    assert "entry_moved_out:+4bars" in td.corrections
    assert "swap_moved_out:+14bars" in td.corrections
    assert "outro_cut:10bars" in td.corrections
    assert "tail_loop_removed:2bx8+0b" in td.corrections
    assert "overlap_changed:64->34bars" in td.corrections
    assert not any(c.startswith("swap_moved_in") for c in td.corrections)
    # both swaps sit on real section clips, so the old label is still trusted
    assert td.bass_swap_reliable is True
    assert any(c.startswith("bass_swap_moved:+56beats") for c in td.corrections)


def test_one_bar_swap_nudge_onto_the_phrase_grid():
    """T2, Sam Leagas -> HNTR: HNTR moved one bar later so the swap sits on
    bar 136 of Sam Leagas, not the detected boundary at 135."""
    baseline = {
        "Out": [_clip("drop_3", 1104, 1232, 384, 512),
                _clip("drop_4", 1232, 1260, 512, 540),
                _clip("outro_1", 1260, 1328, 540, 608)],
        "In": [_clip("intro_1", 1228, 1260, 0, 32),
               _clip("drop_1", 1260, 1356, 32, 128)],
    }
    corrected = {
        "Out": [_clip("drop_3", 1104, 1232, 384, 512),
                _clip("drop_4", 1232, 1264, 512, 544),
                _clip("outro_1", 1264, 1328, 544, 608)],
        "In": [_clip("intro_1", 1232, 1264, 0, 32),
               _clip("drop_1", 1264, 1360, 32, 128)],
    }
    c_auto = {"Out": TrackAutomation("Out", bass_points=[(1260.0, 1.0), (1260.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(1228.0, 0.18), (1260.0, 0.18), (1260.0, 1.0)])}
    s_auto = {"Out": TrackAutomation("Out", bass_points=[(1264.0, 1.0), (1264.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(1232.0, 0.18), (1264.0, 0.18), (1264.0, 1.0)])}

    [td] = _run(baseline, corrected, c_auto, s_auto)

    assert "swap_moved_out:+1bars" in td.corrections
    assert "entry_moved_out:+1bars" in td.corrections
    assert not any(c.startswith("swap_moved_in") for c in td.corrections)
    assert td.geometry["swap_in_bar"] == [8.0, 8.0]


def test_reliability_is_judged_on_the_delta_event_not_a_second_finder():
    """Review, 2026-09-15. A short outgoing still carries its own earlier
    bass-in ramp; a ramp point on the way up sits inside the source-space
    finder's window, so bass_swap_claude comes from the ramp (source 85)
    while the real first kill (a falling edge at 1105) sits inside a
    three-copy tail loop. Sam's side kills inside the same loop later. The
    old flag certified the ramp point as reliable and wrote
    bass_swap_moved:+10beats to the corpus. Now: not reliable, no label, and
    the geometry swap is the real kill."""
    out_clips = ([_clip("drop_1", 1000, 1100, 0, 100)]
                 + _copies("outro_1_tail_loop", 1100, 90, 100, 3)
                 + [_clip("outro_1", 1130, 1190, 100, 160)])
    in_clips = [_clip("intro_1", 1080, 1200, 0, 120)]
    baseline = {"Out": out_clips, "In": in_clips}
    corrected = {"Out": list(out_clips), "In": list(in_clips)}
    c_auto = {"Out": TrackAutomation("Out", bass_points=[
                  (1060.0, 0.18), (1085.0, 0.5), (1095.0, 1.0), (1105.0, 1.0), (1105.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(1080.0, 0.18), (1105.0, 0.18), (1105.0, 1.0)])}
    s_auto = {"Out": TrackAutomation("Out", bass_points=[
                  (1060.0, 0.18), (1085.0, 0.5), (1095.0, 1.0), (1125.0, 1.0), (1125.0, 0.18)]),
              "In": TrackAutomation("In", bass_points=[(1080.0, 0.18), (1125.0, 0.18), (1125.0, 1.0)])}

    [td] = _run(baseline, corrected, c_auto, s_auto)

    assert td.geometry["swap_arr_beat"] == [1105.0, 1125.0]
    assert td.geometry["swap_out_on_loop"] == [True, True]
    assert td.bass_swap_reliable is False
    assert not any(c.startswith("bass_swap_moved") for c in td.corrections)


def test_identical_transition_has_no_geometry_labels():
    clips = {
        "Out": [_clip("drop_1", 0, 400, 0, 400), _clip("outro_1", 400, 464, 400, 464)],
        "In": [_clip("intro_1", 400, 464, 0, 64), _clip("drop_1", 464, 800, 64, 400)],
    }
    auto = {"Out": TrackAutomation("Out", bass_points=[(464.0, 1.0), (464.0, 0.18)]),
            "In": TrackAutomation("In", bass_points=[(400.0, 0.18), (464.0, 0.18), (464.0, 1.0)])}

    [td] = _run(clips, clips, auto, auto)

    assert td.corrections == []
    assert td.verdict == "correct"
    assert td.bass_swap_reliable is True
    assert td.geometry["outro_cut_bars"] == 0.0
