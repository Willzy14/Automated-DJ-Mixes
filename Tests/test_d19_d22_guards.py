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


# --------------------------------------------------------------------------- #
# D22 remainder (2026-11-04): the third writer (als_generator.compress_als)   #
# wrote straight to the output and validated afterwards; and a failed final   #
# rename (a read-only or locked output) surfaced as PermissionError instead   #
# of the ValueError the callers expect, in all three writers.                 #
# --------------------------------------------------------------------------- #

_WRITERS = ("als_generator", "apply_loops", "apply_automation")
_OLD_BYTES_MARK = b"<OLD-VALID-OUTPUT/>\n"


def _writer(name):
    if name == "als_generator":
        from automated_dj_mixes import als_generator
        return als_generator.compress_als
    if name == "apply_loops":
        return apply_loops.compress_als
    import apply_automation
    return apply_automation.compress_als


def _partials(folder):
    return sorted(p.name for p in folder.glob("*.partial"))


def _existing_output(folder, name="Sections V9.als"):
    out = folder / name
    out.write_bytes(gzip.compress(_OLD_BYTES_MARK, mtime=0))
    return out, out.read_bytes()


def test_d22_the_three_writers_are_imported_from_this_checkout():
    # Proves the tests exercise THIS checkout's code, not another copy.
    import apply_automation
    from automated_dj_mixes import als_generator
    for module in (als_generator, apply_loops, apply_automation):
        print("module under test:", module.__file__)
        assert Path(module.__file__).resolve().is_relative_to(ROOT.resolve())


@pytest.mark.parametrize("name", _WRITERS)
def test_d22_invalid_als_raises_valueerror_and_leaves_no_output_and_no_partial(
        tmp_path, name):
    out = tmp_path / "Sections V9.als"
    with pytest.raises(ValueError, match="ALS validation failed"):
        _writer(name)(["<not-an-als>\n"], out)
    assert not out.exists()
    assert _partials(tmp_path) == []


@pytest.mark.parametrize("name", _WRITERS)
def test_d22_a_rejected_write_leaves_an_existing_output_byte_identical(
        tmp_path, name):
    out, before = _existing_output(tmp_path)
    with pytest.raises(ValueError, match="ALS validation failed"):
        _writer(name)(["<not-an-als>\n"], out)
    assert out.read_bytes() == before
    assert _partials(tmp_path) == []


@pytest.mark.parametrize("exc", [PermissionError(13, "Access is denied"),
                                 OSError(28, "No space left on device")])
@pytest.mark.parametrize("name", _WRITERS)
def test_d22_a_failed_rename_surfaces_as_valueerror_and_keeps_the_old_output(
        tmp_path, monkeypatch, name, exc):
    import validate_als
    monkeypatch.setattr(validate_als, "report_als", lambda p: [])
    out, before = _existing_output(tmp_path)

    def _refuse(self, target):
        raise exc

    monkeypatch.setattr(Path, "replace", _refuse)
    with pytest.raises(ValueError, match="Sections V9.als"):
        _writer(name)(["<Ableton/>\n"], out)
    assert out.read_bytes() == before
    assert _partials(tmp_path) == []


@pytest.mark.skipif(sys.platform != "win32",
                    reason="replacing a read-only file only fails on Windows")
@pytest.mark.parametrize("name", _WRITERS)
def test_d22_a_really_read_only_output_is_refused_with_valueerror(
        tmp_path, monkeypatch, name):
    import os
    import stat
    import validate_als
    monkeypatch.setattr(validate_als, "report_als", lambda p: [])
    out, before = _existing_output(tmp_path)
    os.chmod(out, stat.S_IREAD)
    try:
        with pytest.raises(ValueError, match="Sections V9.als"):
            _writer(name)(["<Ableton/>\n"], out)
        assert out.read_bytes() == before
        assert _partials(tmp_path) == []
    finally:
        os.chmod(out, stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.parametrize("name", _WRITERS)
def test_d22_a_valid_write_publishes_the_new_content_and_removes_the_partial(
        tmp_path, monkeypatch, name):
    import validate_als
    monkeypatch.setattr(validate_als, "report_als", lambda p: [])
    out, _ = _existing_output(tmp_path)
    assert _writer(name)(["<Ableton/>\n", "<x/>\n"], out) == out
    assert gzip.open(out, "rb").read() == b"<Ableton/>\n<x/>\n"
    assert _partials(tmp_path) == []


def test_d22_als_generator_valid_write_is_byte_identical_to_the_old_direct_write(
        tmp_path, monkeypatch):
    # Characterisation pin (it passes on the old code by design): the fix
    # writes a `.partial` first, and a plain gzip.open(partial) would put
    # "<name>.partial" into the gzip header, changing the bytes of a perfectly
    # good output. Time is frozen so the header mtime is comparable.
    import time
    import validate_als
    from automated_dj_mixes import als_generator
    monkeypatch.setattr(validate_als, "report_als", lambda p: [])
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    lines = ["<Ableton>\r\n", '  <LiveSet Value="1"/>\r\n', "</Ableton>\r\n"]
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old = old_dir / "Sections V9.als"
    # The body of als_generator.compress_als as it was before D22:
    with gzip.open(old, "wb") as f:
        f.write("".join(lines).encode("utf-8"))
    new = new_dir / "Sections V9.als"
    assert als_generator.compress_als(lines, new) == new
    assert new.read_bytes() == old.read_bytes()
    assert gzip.open(new, "rb").read() == "".join(lines).encode("utf-8")
    assert _partials(new_dir) == []


# --------------------------------------------------------------------------- #
# D19 remainder (2026-11-04): the two paths the planner fix did not cover.    #
# --------------------------------------------------------------------------- #

def _trim_decision(trim=8):
    # out is 110 bars, in is 80: entry 90 - trim 8 = origin 82, swap lands on
    # out bar 102 (inside the outgoing), so only the intro guard can object.
    return {"pair_index": 1, "out_track": "out", "in_track": "in",
            "entry_out_bar": 90, "intro_trim_bars": trim, "swap_in_bar": 20,
            "swap_cue": "drop_1", "reason": "test"}


@pytest.mark.parametrize("intro_end", [8.0018, 8.2499, 8.25, 8.5, 8.9, 9.0, 12.0])
def test_d19_decision_guard_agrees_with_the_planner_guard(intro_end):
    # The decision path must use the planner's intro value and margin: for the
    # same incoming intro and the same 8-bar cut, a decision is accepted
    # exactly when plan_fill_or_cut would have emitted the cut.
    o, i, al = _pair(intro_end)
    planner_cuts = bool(_cuts(ae.plan_fill_or_cut(o, i, al)))
    try:
        built = ae.alignment_from_decision(o, i, _trim_decision(8))
        ae.fills_from_decision(o, i, _trim_decision(8))
        accepted = True
    except ValueError as exc:
        assert "whole intro" in str(exc)
        accepted = False
    assert accepted == planner_cuts, intro_end
    if accepted:
        assert built.intro_cut_bars == 8


def test_d19_a_decision_trimming_the_whole_intro_of_an_8_bar_intro_raises():
    o, i, _ = _pair(8.0)
    with pytest.raises(ValueError, match="whole intro"):
        ae.alignment_from_decision(o, i, _trim_decision(8))
    with pytest.raises(ValueError, match="whole intro"):
        ae.fills_from_decision(o, i, _trim_decision(8))


def test_d19_a_decision_trimming_intro_minus_half_a_bar_passes_unchanged():
    o, i, _ = _pair(8.5)
    al = ae.alignment_from_decision(o, i, _trim_decision(8))
    assert (al.arr_offset_bars, al.handoff_bar_out, al.intro_cut_bars) == (82, 102, 8)
    cuts = _cuts(ae.fills_from_decision(o, i, _trim_decision(8)))
    assert len(cuts) == 1 and cuts[0].cut_to_bar == 8


def _one_clip_als(time=100.0, length=32.0, src=0.0, name="intro_1"):
    lines = ['<AudioTrack Id="9">\r\n', '  <Name>\r\n',
             '    <EffectiveName Value="IN" />\r\n', '  </Name>\r\n',
             '  <DeviceChain>\r\n', '    <MainSequencer>\r\n',
             '      <Sample>\r\n', '        <ArrangerAutomation>\r\n',
             '          <Events>\r\n',
             f'<AudioClip Id="1" Time="{time}">\r\n',
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
             '          </Events>\r\n', '        </ArrangerAutomation>\r\n',
             '      </Sample>\r\n', '    </MainSequencer>\r\n',
             '  </DeviceChain>\r\n', '</AudioTrack>\r\n']
    return lines


def _clip_fields(lines):
    import re
    text = "".join(lines)
    time = float(re.search(r'<AudioClip Id="1" Time="([^"]+)"', text).group(1))
    get = lambda tag: float(re.search(rf'<{tag} Value="([^"]+)"', text).group(1))
    return {"Time": time, "CurrentStart": get("CurrentStart"),
            "CurrentEnd": get("CurrentEnd"), "LoopStart": get("LoopStart"),
            "LoopEnd": get("LoopEnd"), "HiddenLoopStart": get("HiddenLoopStart")}


def _trim(lines, beats, name="intro_1"):
    start, end, _ = apply_loops.find_track_line_ranges(lines)[0]
    return apply_loops.trim_named_clip_front(lines, start, end, name, beats)


def test_d19_trim_named_clip_front_moves_start_and_loop_start_by_exactly_the_trim():
    lines = _one_clip_als(time=100.0, length=32.0, src=0.0)
    assert _trim(lines, 12.0) == 3
    assert _clip_fields(lines) == {
        "Time": 112.0, "CurrentStart": 112.0, "CurrentEnd": 132.0,
        "LoopStart": 12.0, "LoopEnd": 32.0, "HiddenLoopStart": 0.0}


def test_d19_trim_named_clip_front_refuses_a_trim_equal_to_the_clip_length():
    lines = _one_clip_als(time=100.0, length=32.0)
    before = list(lines)
    with pytest.raises(ValueError, match="intro_1"):
        _trim(lines, 32.0)
    assert lines == before            # nothing half-changed
    f = _clip_fields(lines)
    assert f["CurrentStart"] < f["CurrentEnd"]


def test_d19_trim_named_clip_front_refuses_a_trim_past_the_clip_length():
    lines = _one_clip_als(time=100.0, length=32.0)
    before = list(lines)
    with pytest.raises(ValueError, match="intro_1"):
        _trim(lines, 40.0)
    assert lines == before


def test_d19_trim_named_clip_front_refuses_when_only_float_noise_would_remain():
    lines = _one_clip_als(time=100.0, length=32.0000001)
    before = list(lines)
    with pytest.raises(ValueError):
        _trim(lines, 32.0)
    assert lines == before


def test_d19_trim_named_clip_front_allows_a_trim_that_leaves_a_beat():
    lines = _one_clip_als(time=100.0, length=32.0)
    assert _trim(lines, 31.0) == 3
    f = _clip_fields(lines)
    assert (f["CurrentStart"], f["CurrentEnd"], f["LoopStart"], f["LoopEnd"]) == (
        131.0, 132.0, 31.0, 32.0)


def test_d19_trim_named_clip_front_zero_trim_and_unknown_clip_still_return_0():
    lines = _one_clip_als()
    before = list(lines)
    assert _trim(lines, 0.0) == 0
    assert _trim(lines, -4.0) == 0
    assert _trim(lines, 4.0, name="no_such_clip") == 0
    assert lines == before
