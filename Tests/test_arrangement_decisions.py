"""Claude-arranged mode (2026-09-15): `propose_arrangement --decisions` builds a
transition from a per-transition decision instead of the anchor search, and
everything downstream - report, MixPlan, loops, cuts, automation, gates - is
the production path. These tests pin the decision arithmetic, the two new
outgoing edits (a front cut that pulls later clips in, an outro skip that keeps
the ending) and the no-outro tail loop, each against the numbers in the
15.09.26 August Releases Mix decisions file.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as AE  # noqa: E402
from apply_loops import cut_named_clip_front_and_pull, find_track_line_ranges  # noqa: E402
from propose_arrangement import (  # noqa: E402
    OverlapAnalysis, TrackInfo, _plan_marker_loops, generate_report, propose_arrangement,
)


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
    # 152-bar IN: a swap_in_bar past its own end must be refused even though it fits
    # comfortably inside the 214-bar OUT (found in peer review, 2026-09-16: the
    # outgoing-side bound was checked, the incoming-side one was not).
    ({"entry_out_bar": 10, "swap_in_bar": 160}, "past.*end"),
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


def test_a_tail_loop_past_the_outgoing_end_is_refused():
    # 214-bar OUT: a loop source past its own end must be refused (found in peer
    # review, 2026-09-16 - only source_end_bar > source_start_bar was checked).
    with pytest.raises(ValueError, match="past.*end"):
        AE.fills_from_decision(OUT, IN, _decision(
            tail_loop={"source_start_bar": 210, "source_end_bar": 220, "reps": 2}))


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


@pytest.fixture
def blend_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def build(a_windows=((0, 401),), zero=0, reject=False, a_exit=96):
        stems = tmp_path / "_Stem Analysis"
        stems.mkdir()
        sections, lines = {}, ['<Ableton>\r\n', '<LiveSet>\r\n', '<Tracks>\r\n']
        for i, name in enumerate("ABC"):
            windows = a_windows if name == "A" else [(0, 400)]
            base = i * 500
            lines.extend([f'<AudioTrack Id="{i + 10}">\r\n', '<Name>\r\n',
                          f'<EffectiveName Value="{name}" />\r\n', '</Name>\r\n',
                          '<DeviceChain>\r\n', '<MainSequencer>\r\n', '<Sample>\r\n',
                          '<ArrangerAutomation>\r\n', '<Events>\r\n'])
            sections[name] = []
            for j, (start, end) in enumerate(windows):
                clip_name = f"drop_{j + 1}"
                clip = _clip(100 + 10*i + j, clip_name, base + start, end - start, start)
                clip[-1:-1] = [
                    '<WarpMarkers>\r\n',
                    f'<WarpMarker Id="{200 + 20*i + 2*j}" SecTime="0" BeatTime="0" />\r\n',
                    f'<WarpMarker Id="{201 + 20*i + 2*j}" SecTime="400" BeatTime="400" />\r\n',
                    '</WarpMarkers>\r\n',
                ]
                lines.extend(clip)
                sections[name].append({"name": clip_name, "label": "drop",
                                       "arr_time": base + start, "arr_end": base + end,
                                       "source_start_beats": start, "source_end_beats": end})
            lines.extend(['</Events>\r\n', '</ArrangerAutomation>\r\n', '</Sample>\r\n',
                          '</MainSequencer>\r\n', '</DeviceChain>\r\n', '</AudioTrack>\r\n'])
            detector = {"track": name, "bpm": 60, "n_bars": 100,
                        "sections": [{"name": "drop_1", "label": "drop", "start_bar": 0,
                                      "end_bar": 100, "start_sec": zero if name == "A" else 0,
                                      "end_sec": 400}]}
            (stems / f"SECTIONS_STEM_{name}.json").write_text(json.dumps(detector), encoding="ascii")
            np.savez(stems / f"{name}__stemenv.npz", hop_t=0.25, mix=np.full(1616, 0.1))
        lines.extend(['</Tracks>\r\n', '</LiveSet>\r\n', '</Ableton>\r\n'])
        als = tmp_path / "Layout.als"
        als.write_bytes(gzip.compress("".join(lines).encode("ascii")))
        layout = tmp_path / "Sections.json"
        layout.write_text(json.dumps(sections), encoding="ascii")
        decisions = tmp_path / "Decisions.json"
        decisions.write_text(json.dumps({"transitions": [
            dict(pair_index=1, out_track="A", in_track="B", entry_out_bar=76,
                 swap_in_bar=8, out_end_bar=a_exit,
                 intro_loop={"source_start_bar": 3 if reject else 2, "source_end_bar": 6}),
            dict(pair_index=2, out_track="B", in_track="C", entry_out_bar=80,
                 swap_in_bar=8, out_end_bar=103 if reject else 104,
                 tail_loop={"source_start_bar": 93 if reject else 92, "source_end_bar": 96,
                            "reps": 1}),
        ]}), encoding="ascii")
        return als, layout, tmp_path / "Arranged.als", decisions

    return build


def _written_windows(path):
    root = ET.fromstring(gzip.decompress(path.read_bytes()))
    tags = ("CurrentStart", "CurrentEnd", "Loop/LoopStart", "Loop/LoopEnd")
    return {
        track.find("Name/EffectiveName").get("Value"): {
            tuple(float(clip.find(tag).get("Value")) for tag in tags)
            for clip in track.findall(".//AudioClip")
        }
        for track in root.findall("./LiveSet/Tracks/AudioTrack")
    }


def test_blend_decisions_edit_actual_als_and_shift_next_transition(blend_layout, capsys):
    als, layout, output, decisions = blend_layout()
    plan = propose_arrangement(als, layout, output, decisions_path=decisions)
    assert _written_windows(output) == {
        "A": {(0, 384, 0, 384)},
        "B": {(304, 328, 0, 24), (328, 344, 8, 24),
              (344, 720, 24, 400), (720, 736, 368, 384)},
        "C": {(640, 1040, 0, 400)},
    }
    assert [ov.decision_exit for ov in plan.overlaps] == [(96, 384), (104, 736)]
    assert [alignment.swap_beats for alignment in plan.alignments] == [352, 672]
    assert "UNMEASURED" not in capsys.readouterr().out


@pytest.mark.parametrize(("windows", "zero", "exit_bar", "message"), [
    (((0, 402),), 0, 96, r"A.*exit bar 96.*length mismatch \+2\.000 beats"),
    (((0, 400),), 2, 100, r"A.*exit bar 100.*bar-zero mismatch -2\.000 beats"),
    (((0, 380), (388, 401)), 0, 96, r"A.*trim_end at beat 384.*gap"),
    (((0, 399),), 0, 100, r"A.*exit bar 100.*ends 1 beats before"),
])
def test_blend_decisions_refuse_clock_mismatches_and_gap_edits(
        blend_layout, windows, zero, exit_bar, message):
    als, layout, output, decisions = blend_layout(windows, zero, a_exit=exit_bar)
    with pytest.raises(ValueError, match=message):
        propose_arrangement(als, layout, output, decisions_path=decisions)
    assert not output.exists()


def test_production_drops_rejected_loops_and_preserves_proposal(blend_layout):
    als, layout, output, decisions = blend_layout(reject=True)
    proposal = decisions.read_bytes()
    with pytest.raises(ValueError, match=r"Loop quality gate failed.*failed=period"):
        propose_arrangement(als, layout, output, decisions_path=decisions)
    assert not output.exists()
    plan = propose_arrangement(als, layout, output, decisions_path=decisions,
                               decisions_drop_rejected_loops=True)
    assert decisions.read_bytes() == proposal
    assert not plan.loops
    assert _written_windows(output) == {
        "A": {(0, 384, 0, 384)}, "B": {(316, 716, 0, 400)}, "C": {(636, 1036, 0, 400)},
    }
    assert [alignment.swap_beats for alignment in plan.alignments] == [348, 668]
    rows = json.loads(generate_report(plan, output).read_text(encoding="utf-8"))["transitions"]
    drops = [row["decision_loop_drops"][0] for row in rows]
    assert [(drop["repeat"], drop["failed_checks"]) for drop in drops] == [
        ("intro_loop", ["period"]), ("tail_loop", ["period"])]
    assert (drops[0]["requested_entry_bar"], drops[0]["applied_entry_bar"]) == (76, 79)
    assert (drops[1]["requested_exit_bar"], drops[1]["applied_exit_bar"]) == (103, 100)


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


# --------------------------------------------------------------------------- #
# Burn list E8 (2026-09-16): three unhardened schema edges, hardened          #
# 2026-09-22. Each test below is a falsifier for the pre-fix behaviour        #
# (proved: reverted against `git stash`, all three failed to raise).         #
# --------------------------------------------------------------------------- #

def test_ambiguous_prefix_match_is_refused_not_silently_accepted():
    """Two tracks in the SAME pair sharing a long common prefix (a radio
    edit vs. an extended mix, back to back - exactly the real DJ-naming
    pattern the item names) must not let a loose, truncated `in_track` name
    silently resolve when it could equally describe either side. Built so
    the shared prefix is verifiably >=30 chars (the pre-fix rule's fixed
    b[:30]/a[:30] prefix test could not tell the two apart)."""
    shared = "Some Artist Feat Other - A Really Long Track Title "
    assert len(shared) >= 30
    out_side = _track(shared + "(Radio Edit)", 96, [
        {"name": "drop_1", "label": "drop", "start_bar": 16.0, "end_bar": 96.0}])
    in_side = _track(shared + "(Extended Mix)", 128, [
        {"name": "drop_1", "label": "drop", "start_bar": 16.0, "end_bar": 128.0}])
    # out_track is named exactly (unambiguous); in_track is truncated to
    # just the shared prefix - it loosely matches BOTH tracks in this pair,
    # so it cannot safely resolve to either. entry/swap bars kept inside
    # both tracks' short lengths (96/128 bars) so the ambiguity check is
    # what actually fires, not an unrelated bounds check.
    with pytest.raises(ValueError, match="ambiguous"):
        AE.alignment_from_decision(out_side, in_side, _decision(
            out_track=out_side.name, in_track=shared.strip(),
            entry_out_bar=60, swap_in_bar=16))
    # Sanity: the SAME wanted string against the track it genuinely names
    # (exactly, not just by prefix) must still work - this is not a
    # blanket ban on the pair, only on the ambiguous case.
    al = AE.alignment_from_decision(out_side, in_side, _decision(
        out_track=out_side.name, in_track=in_side.name,
        entry_out_bar=60, swap_in_bar=16))
    assert al.in_name == in_side.name


def test_unambiguous_loose_prefix_still_matches():
    """The original tolerance (whitespace/punctuation noise, a slightly
    abbreviated name) must still work when only ONE track in the pair could
    plausibly satisfy it."""
    al = AE.alignment_from_decision(OUT, IN, _decision(
        in_track=IN.name[:20]))   # a genuine truncation of the real name
    assert al.in_name == IN.name


@pytest.mark.parametrize("field, value", [
    ("entry_out_bar", 180.5),
    ("swap_in_bar", 16.3),
])
def test_fractional_bar_values_are_refused(field, value):
    """A decision names whole musical bars. A meaningfully fractional value
    is far more likely an authoring mistake than an intentional sub-bar
    position, and must be rejected, not silently propagated into downstream
    bar/beat arithmetic."""
    with pytest.raises(ValueError, match="not a whole bar"):
        AE.alignment_from_decision(OUT, IN, _decision(**{field: value}))


def test_fractional_bar_within_floating_point_noise_is_tolerated():
    """179.9999999999 (real float noise from JSON round-tripping) must snap
    to 180, not be rejected as if it were a genuine authoring mistake."""
    al = AE.alignment_from_decision(OUT, IN, _decision(entry_out_bar=179.9999999999))
    assert al.arr_offset_bars == 180


def test_fractional_tail_loop_bar_is_also_refused():
    """The same rounding discipline applies inside fills_from_decision's
    tail_loop fields, not just alignment_from_decision's top-level ones."""
    d = _decision(entry_out_bar=136, intro_trim_bars=4, swap_in_bar=36,
                  tail_loop={"source_start_bar": 0.0, "source_end_bar": 8.5,
                            "reps": 2, "target": "outro"})
    with pytest.raises(ValueError, match="not a whole bar"):
        AE.fills_from_decision(OUT, IN, d)


# --------------------------------------------------------------------------- #
# pair_index duplicate / out-of-range validation (propose_arrangement.py)     #
# --------------------------------------------------------------------------- #

def test_duplicate_pair_index_is_refused():
    from propose_arrangement import _validate_decision_pair_indices
    with pytest.raises(ValueError, match="duplicate pair_index"):
        _validate_decision_pair_indices([1, 2, 2, 3], n_tracks=5, filename="test.json")


def test_out_of_range_pair_index_is_refused():
    from propose_arrangement import _validate_decision_pair_indices
    # 5 tracks -> 4 transitions -> valid pair_index is 1..4.
    with pytest.raises(ValueError, match="out of range"):
        _validate_decision_pair_indices([1, 2, 5], n_tracks=5, filename="test.json")
    with pytest.raises(ValueError, match="out of range"):
        _validate_decision_pair_indices([0, 1, 2], n_tracks=5, filename="test.json")


def test_valid_pair_indices_pass_silently():
    from propose_arrangement import _validate_decision_pair_indices
    _validate_decision_pair_indices([1, 2, 3, 4], n_tracks=5, filename="test.json")  # no raise


# --------------------------------------------------------------------------- #
# Burn list D19 remainder (2026-11-04): the planner never lets an intro cut   #
# take the whole intro (it leaves >= 0.25 bar, commit ae3cc35), but a written #
# decision could: intro_trim_bars equal to the incoming's intro became a      #
# zero-length clip that only the ALS validator caught, after the build.       #
# --------------------------------------------------------------------------- #

def _in_with_intro_end(intro_end):
    return _track(IN.name, 152, [
        {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": intro_end},
        {"name": "drop_1", "label": "drop", "start_bar": intro_end, "end_bar": 40.0},
    ])


def test_d19_a_decision_that_trims_the_whole_intro_is_refused():
    # IN's intro is bars 0-16. Trimming 16 leaves nothing of it; swap_in 32 is
    # past the trim, so the older swap-after-trim check does not catch this.
    d = _decision(entry_out_bar=180, intro_trim_bars=16, swap_in_bar=32)
    with pytest.raises(ValueError, match="whole intro"):
        AE.alignment_from_decision(OUT, IN, d)
    with pytest.raises(ValueError, match="whole intro"):
        AE.fills_from_decision(OUT, IN, d)


def test_d19_a_decision_that_trims_more_than_the_intro_is_refused():
    d = _decision(entry_out_bar=180, intro_trim_bars=20, swap_in_bar=32)
    with pytest.raises(ValueError, match="whole intro"):
        AE.alignment_from_decision(OUT, IN, d)
    with pytest.raises(ValueError, match="whole intro"):
        AE.fills_from_decision(OUT, IN, d)


def test_d19_the_refusal_names_the_pair_the_trim_and_where_the_intro_ends():
    d = _decision(pair_index=7, entry_out_bar=180, intro_trim_bars=16, swap_in_bar=32)
    with pytest.raises(ValueError) as err:
        AE.alignment_from_decision(OUT, IN, d)
    text = str(err.value)
    assert "pair 7" in text and "intro_trim_bars 16" in text
    assert IN.name in text and "bar 16" in text


def test_d19_a_trim_that_leaves_half_a_bar_of_the_intro_passes_unchanged():
    # Intro ends at bar 8.5; trimming 8 leaves 0.5 bar. Same numbers as before.
    inc = _in_with_intro_end(8.5)
    d = _decision(entry_out_bar=180, intro_trim_bars=8, swap_in_bar=16)
    al = AE.alignment_from_decision(OUT, inc, d)
    assert al.arr_offset_bars == 172          # entry 180 - trim 8
    assert al.handoff_bar_out == 188          # origin 172 + swap_in 16
    assert al.overlap_bars == 42              # 214 - 172
    assert al.intro_cut_bars == 8
    cut = [f for f in AE.fills_from_decision(OUT, inc, d) if f.kind == "intro_cut"]
    assert len(cut) == 1 and cut[0].cut_to_bar == 8 and cut[0].target_marker_bar == 16


def test_d19_the_float_edge_case_is_refused_but_a_quarter_bar_is_enough():
    # The Dombresky case from the burn list: intro ends at 8.0018, trim 8.
    d = _decision(entry_out_bar=180, intro_trim_bars=8, swap_in_bar=16)
    for intro_end in (8.0018, 8.2499):
        with pytest.raises(ValueError, match="whole intro"):
            AE.alignment_from_decision(OUT, _in_with_intro_end(intro_end), d)
        with pytest.raises(ValueError, match="whole intro"):
            AE.fills_from_decision(OUT, _in_with_intro_end(intro_end), d)
    for intro_end in (8.25, 8.5):
        AE.alignment_from_decision(OUT, _in_with_intro_end(intro_end), d)
        AE.fills_from_decision(OUT, _in_with_intro_end(intro_end), d)


def test_d19_a_trim_is_not_judged_without_intro_length_data():
    # No leading 'intro' section means there is no intro length to compare
    # with (the planner skips its own cut in the same case), so the decision
    # is left to the author exactly as before.
    inc = _track(IN.name, 152, [
        {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 32.0}])
    d = _decision(entry_out_bar=180, intro_trim_bars=16, swap_in_bar=32)
    assert AE.alignment_from_decision(OUT, inc, d).intro_cut_bars == 16
    assert [f.kind for f in AE.fills_from_decision(OUT, inc, d)] == ["intro_cut"]


def test_d19_no_trim_never_triggers_the_intro_guard():
    inc = _in_with_intro_end(8.0018)
    d = _decision(entry_out_bar=180, intro_trim_bars=0, swap_in_bar=16)
    assert AE.alignment_from_decision(OUT, inc, d).intro_cut_bars == 0
    assert AE.fills_from_decision(OUT, inc, d) == []
