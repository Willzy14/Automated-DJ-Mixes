"""Claude-arranged mode (2026-09-15): `propose_arrangement --decisions` builds a
transition from a per-transition decision instead of the anchor search, and
everything downstream - report, MixPlan, loops, cuts, automation, gates - is
the production path. These tests pin the decision arithmetic, the two new
outgoing edits (a front cut that pulls later clips in, an outro skip that keeps
the ending) and the no-outro tail loop, each against the numbers in the
15.09.26 August Releases Mix decisions file.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as AE  # noqa: E402
from apply_loops import cut_named_clip_front_and_pull, find_track_line_ranges  # noqa: E402
from propose_arrangement import OverlapAnalysis, TrackInfo, _plan_marker_loops  # noqa: E402


def _track(name, n_bars, sections):
    return AE.Track(
        name=name, bpm=126.0, spb=4 * 60.0 / 126.0, downbeat=0.0, n_bars=n_bars,
        sections=sections, bass_in_bar=0.0, bass_out_bar=float(n_bars) - 8,
        last_min_bars=64,
    )


# 15.09.26 T1: Demarkus (214 bars, outro from 196) -> Sam Leagas (drop_1 at 16).
OUT = _track("Demarkus Le- A Deep-Felt Love", 214, [
    {"name": "drop_4", "label": "drop", "start_bar": 156.0, "end_bar": 196.0},
    {"name": "outro_1", "label": "outro", "start_bar": 196.0, "end_bar": 214.0},
])
IN = _track("Sam Leagas - Bad Behaviours", 152, [
    {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 16.0},
    {"name": "drop_1", "label": "drop", "start_bar": 16.0, "end_bar": 32.0},
])


def _decision(**over):
    d = {"pair_index": 1, "out_track": OUT.name, "in_track": IN.name,
         "entry_out_bar": 180, "intro_trim_bars": 0, "swap_in_bar": 16,
         "swap_cue": "drop_1", "reason": "test"}
    d.update(over)
    return d


# --------------------------------------------------------------------------- #
# alignment_from_decision                                                     #
# --------------------------------------------------------------------------- #

def test_swap_lands_on_the_outgoing_bar_the_decision_implies():
    al = AE.alignment_from_decision(OUT, IN, _decision())
    assert al.arr_offset_bars == 180
    assert al.handoff_bar_out == 196          # entry 180 + incoming bar 16
    assert al.anchor_bar_in == 16
    assert al.overlap_bars == 34              # 214 - 180, pre-loop
    assert al.alignment_policy == AE.DECISIONS_POLICY
    assert AE.DECISIONS_POLICY in AE.LANDMARK_POLICIES, (
        "downstream (clip splitting at the swap, the paired_boundary gate, the "
        "automation margin rule) keys on LANDMARK_POLICIES membership")
    assert al.paired_cues[0]["arrangement_bar"] == 196
    assert al.paired_cues[0]["incoming_source_bar"] == 16


def test_intro_trim_moves_the_track_origin_back_so_the_kept_clip_starts_at_entry():
    # 15.09.26 T3: Zaro enters at its bar 4 on HNTR bar 136; drop_1 (bar 36) on HNTR 168.
    al = AE.alignment_from_decision(OUT, IN, _decision(
        entry_out_bar=136, intro_trim_bars=4, swap_in_bar=36))
    assert al.arr_offset_bars == 132           # track origin, 4 bars before the kept clip
    assert al.handoff_bar_out == 168
    assert al.intro_cut_bars == 4
    fills = AE.fills_from_decision(OUT, IN, _decision(
        entry_out_bar=136, intro_trim_bars=4, swap_in_bar=36))
    cut = [f for f in fills if f.kind == "intro_cut"]
    assert len(cut) == 1 and cut[0].cut_to_bar == 4


@pytest.mark.parametrize("bad, message", [
    ({"entry_out_bar": 210, "swap_in_bar": 16}, "after"),      # swap at 226 > 214 bars
    ({"intro_trim_bars": 16, "swap_in_bar": 8}, "intro trim"),  # swap inside the cut
    ({"in_track": "Somebody Else - Other Song"}, "names in_track"),
])
def test_impossible_decisions_are_refused_with_the_reason(bad, message):
    with pytest.raises(ValueError, match=message):
        AE.alignment_from_decision(OUT, IN, _decision(**bad))


def test_a_swap_on_the_outgoing_last_bar_is_legal():
    # 15.09.26 T4: Zaro (132 bars, no outro) swaps at its own end, then loops.
    zaro = _track("Zaro - Pach", 132, [
        {"name": "drop_2", "label": "drop", "start_bar": 100.0, "end_bar": 132.0}])
    al = AE.alignment_from_decision(zaro, IN, _decision(
        out_track=zaro.name, entry_out_bar=116, swap_in_bar=16))
    assert al.handoff_bar_out == 132 and al.overlap_bars == 16


# --------------------------------------------------------------------------- #
# fills_from_decision                                                         #
# --------------------------------------------------------------------------- #

def test_cuts_are_planned_before_the_tail_loop_and_the_loop_targets_the_extended_end():
    fills = AE.fills_from_decision(OUT, IN, _decision(
        outgoing_cut={"clip": "drop_4", "cut_bars": 10},
        tail_loop={"source_start_bar": 196, "source_end_bar": 198, "reps": 7,
                   "partial_bars": 0, "target": "drop_2"}))
    assert [f.kind for f in fills] == ["outgoing_cut", "outgoing_tail"]
    loop = fills[1]
    assert (loop.reps, loop.source_start_bar, loop.source_end_bar) == (7, 196, 198)
    assert loop.target_marker_bar == 214 + 14
    assert loop.target_marker_name == "decision:drop_2"
    assert fills[0].clip_name == "drop_4" and fills[0].skip_bars == 10


def test_an_empty_loop_or_cut_is_refused():
    with pytest.raises(ValueError, match="tail_loop"):
        AE.fills_from_decision(OUT, IN, _decision(
            tail_loop={"source_start_bar": 196, "source_end_bar": 196, "reps": 0}))
    with pytest.raises(ValueError, match="outro_skip"):
        AE.fills_from_decision(OUT, IN, _decision(
            outro_skip={"clip": "outro_1", "skip_bars": 4, "keep_end_bars": 0}))


# --------------------------------------------------------------------------- #
# _plan_marker_loops: the two outgoing edits and the no-outro tail loop       #
# --------------------------------------------------------------------------- #

def _section(name, label, arr_time, arr_end, src):
    return {"name": name, "label": label, "arr_time": arr_time, "arr_end": arr_end,
            "source_start_beats": src, "source_end_beats": src + (arr_end - arr_time)}


def _analysis():
    return OverlapAnalysis(out_track="OUT", in_track="IN", pair_index=1,
                           overlap_start=0.0, overlap_end=0.0, overlap_beats=0.0,
                           overlap_bars=0.0, status="ok")


def test_front_cut_shortens_the_clip_pulls_later_clips_in_and_records_the_als_edit():
    # 15.09.26 T9: Switch Disco drop_5 (124-144) loses its first 10 bars at the swap.
    outgoing = TrackInfo(name="OUT", bpm=133.0, arr_start=0.0, arr_end=608.0, sections=[
        _section("fill_2", "fill", 480.0, 496.0, 480.0),
        _section("drop_5", "drop", 496.0, 576.0, 496.0),
        _section("outro_1", "outro", 576.0, 608.0, 576.0),
    ])
    incoming = TrackInfo(name="IN", bpm=130.0, arr_start=432.0, arr_end=1200.0,
                         sections=[_section("intro_1", "intro", 432.0, 496.0, 0.0)])
    al = SimpleNamespace(alignment_policy=AE.DECISIONS_POLICY, fills_cuts=[
        AE.FillCutSpec(kind="outgoing_cut", clip_name="drop_5", skip_bars=10)])
    analysis = _analysis()
    _plan_marker_loops(outgoing, incoming, al, analysis)
    assert analysis.front_cut == ("OUT", "drop_5", 40.0)
    drop5 = outgoing.sections[1]
    assert (drop5["arr_time"], drop5["arr_end"], drop5["source_start_beats"]) == (496.0, 536.0, 536.0)
    outro = outgoing.sections[2]
    assert (outro["arr_time"], outro["arr_end"]) == (536.0, 568.0)
    assert outgoing.arr_end == 568.0
    assert outgoing.sections[0]["arr_time"] == 480.0     # the fill before it stays put


def test_outro_skip_keeps_the_ending_and_shortens_the_track():
    # 15.09.26 T10: 4 bars out of RUZE's 18-bar outro, last 5 kept.
    outgoing = TrackInfo(name="OUT", bpm=130.0, arr_start=0.0, arr_end=836.0, sections=[
        _section("drop_4", "drop", 576.0, 764.0, 576.0),
        _section("outro_1", "outro", 764.0, 836.0, 764.0),
    ])
    incoming = TrackInfo(name="IN", bpm=128.0, arr_start=704.0, arr_end=1400.0,
                         sections=[_section("intro_1", "intro", 704.0, 768.0, 0.0)])
    al = SimpleNamespace(alignment_policy=AE.DECISIONS_POLICY, fills_cuts=[
        AE.FillCutSpec(kind="outro_skip", clip_name="outro_1", skip_bars=4, keep_end_bars=5)])
    analysis = _analysis()
    _plan_marker_loops(outgoing, incoming, al, analysis)
    assert analysis.outro_split == ("OUT", "outro_1", 16.0, 20.0)
    assert outgoing.sections[1]["arr_end"] == 820.0
    assert outgoing.arr_end == 820.0


def test_a_decision_can_loop_a_track_that_has_no_outro():
    # 15.09.26 T4: Zaro ends cold at bar 132; its last 2 bars x3 + 1 bar.
    outgoing = TrackInfo(name="OUT", bpm=128.0, arr_start=0.0, arr_end=528.0, sections=[
        _section("drop_2", "drop", 400.0, 528.0, 400.0),
    ])
    incoming = TrackInfo(name="IN", bpm=126.0, arr_start=464.0, arr_end=800.0,
                         sections=[_section("intro_1", "intro", 464.0, 592.0, 0.0)])
    spec = AE.FillCutSpec(kind="outgoing_tail", reps=3, source_start_bar=130,
                          source_end_bar=132, partial_bars=1, target_marker_bar=139,
                          target_marker_name="decision:intro")
    al = SimpleNamespace(alignment_policy=AE.DECISIONS_POLICY, fills_cuts=[spec])
    analysis = _analysis()
    _plan_marker_loops(outgoing, incoming, al, analysis)
    loop = analysis.out_tail_loop
    assert loop is not None
    assert loop.insert_at_beat == 528.0              # after the last clip, nothing pushed back
    assert loop.shifts_before_insert == []
    assert (loop.source_beat_start, loop.source_beat_end, loop.count,
            loop.tail_partial_beats) == (520.0, 528.0, 3, 4.0)
    assert outgoing.arr_end == 528.0 + 28.0
    assert analysis.overlap_policy == "named_landmark_64"

    # The anchor-search policies still skip a track with no outro (unchanged).
    outgoing2 = TrackInfo(name="OUT", bpm=128.0, arr_start=0.0, arr_end=528.0, sections=[
        _section("drop_2", "drop", 400.0, 528.0, 400.0)])
    al2 = SimpleNamespace(alignment_policy="paired_landmarks_v2", fills_cuts=[spec])
    analysis2 = _analysis()
    _plan_marker_loops(outgoing2, incoming, al2, analysis2)
    assert analysis2.out_tail_loop is None and outgoing2.arr_end == 528.0


# --------------------------------------------------------------------------- #
# apply_loops.cut_named_clip_front_and_pull on ALS lines                      #
# --------------------------------------------------------------------------- #

def _clip(cid, name, time, length, src):
    return [
        f'<AudioClip Id="{cid}" Time="{time}">\r\n',
        f'  <CurrentStart Value="{time}" />\r\n',
        f'  <CurrentEnd Value="{time + length}" />\r\n',
        '  <Loop>\r\n',
        f'    <LoopStart Value="{src}" />\r\n',
        f'    <LoopEnd Value="{src + length}" />\r\n',
        '    <StartRelative Value="0" />\r\n',
        '    <LoopOn Value="false" />\r\n',
        f'    <OutMarker Value="{src + length}" />\r\n',
        f'    <HiddenLoopStart Value="{src}" />\r\n',
        f'    <HiddenLoopEnd Value="{src + length}" />\r\n',
        '  </Loop>\r\n',
        f'  <Name Value="{name}" />\r\n',
        '</AudioClip>\r\n',
    ]


def _als_lines():
    lines = ['<AudioTrack Id="9">\r\n', '  <Name>\r\n',
             '    <EffectiveName Value="OUT" />\r\n', '  </Name>\r\n',
             '  <DeviceChain>\r\n', '    <MainSequencer>\r\n',
             '      <Sample>\r\n', '        <ArrangerAutomation>\r\n',
             '          <Events>\r\n']
    lines += _clip(1, "fill_2", 480.0, 16.0, 480.0)
    lines += _clip(2, "drop_5", 496.0, 80.0, 496.0)
    lines += _clip(3, "outro_1", 576.0, 32.0, 576.0)
    lines += ['          </Events>\r\n', '        </ArrangerAutomation>\r\n',
              '      </Sample>\r\n', '    </MainSequencer>\r\n',
              '  </DeviceChain>\r\n', '</AudioTrack>\r\n']
    return lines


def _fields(lines, name):
    text = "".join(lines)
    block = text.split(f'<Name Value="{name}" />')[0].rsplit("<AudioClip ", 1)[1]
    import re
    get = lambda tag: float(re.search(rf'{tag} Value="([^"]+)"', block).group(1))
    time = float(re.search(r'Time="([^"]+)"', block).group(1))
    return time, get("CurrentStart"), get("CurrentEnd"), get("LoopStart"), get("LoopEnd"), get("HiddenLoopStart")


def test_als_front_cut_moves_source_not_position_and_pulls_the_rest_in():
    lines = _als_lines()
    ranges = find_track_line_ranges(lines)
    assert ranges, "fixture track must be discoverable"
    start, end, _ = ranges[0]
    assert cut_named_clip_front_and_pull(lines, start, end, "drop_5", 40.0)
    assert _fields(lines, "fill_2") == (480.0, 480.0, 496.0, 480.0, 496.0, 480.0)
    assert _fields(lines, "drop_5") == (496.0, 496.0, 536.0, 536.0, 576.0, 536.0)
    assert _fields(lines, "outro_1") == (536.0, 536.0, 568.0, 576.0, 608.0, 576.0)


def test_als_front_cut_refuses_the_whole_clip_or_a_missing_clip():
    lines = _als_lines()
    start, end, _ = find_track_line_ranges(lines)[0]
    assert not cut_named_clip_front_and_pull(lines, start, end, "drop_5", 80.0)
    assert not cut_named_clip_front_and_pull(lines, start, end, "drop_9", 4.0)
    assert _fields(lines, "drop_5") == (496.0, 496.0, 576.0, 496.0, 576.0, 496.0)
