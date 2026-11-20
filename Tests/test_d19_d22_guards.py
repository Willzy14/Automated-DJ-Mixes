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


# --------------------------------------------------------------------------- #
# Round 2 (2026-11-04)                                                        #
# --------------------------------------------------------------------------- #

# Item 1: the job-aware gate (track count + mixer devices) used to run in
# generate_session on the ALREADY PUBLISHED file, so a failing gate left an
# invalid-for-the-job .als on disk for a resumed run to pick up (D22). It now
# runs on the .partial inside compress_als, before the rename.

def _gate_case(case):
    from validate_als import MIXER_AUTOMATION_DEVICES
    if case == "track_count":
        return ["<Ableton>\n", "</Ableton>\n"], {"expect_track_count": 2}
    return _one_clip_als(), {"expect_track_count": 1,
                             "expect_track_devices": MIXER_AUTOMATION_DEVICES}


@pytest.mark.parametrize("case", ["track_count", "mixer_devices"])
def test_d22_a_failed_job_gate_leaves_no_output_and_no_partial(tmp_path, case):
    from automated_dj_mixes import als_generator
    lines, gate = _gate_case(case)
    out = tmp_path / "Sections V9.als"
    with pytest.raises(ValueError, match="ALS expectation gate failed for Sections V9.als"):
        als_generator.compress_als(lines, out, **gate)
    assert not out.exists()
    assert _partials(tmp_path) == []


@pytest.mark.parametrize("case", ["track_count", "mixer_devices"])
def test_d22_a_failed_job_gate_keeps_an_existing_output_byte_identical(tmp_path, case):
    from automated_dj_mixes import als_generator
    lines, gate = _gate_case(case)
    out, before = _existing_output(tmp_path)
    with pytest.raises(ValueError, match="ALS expectation gate failed"):
        als_generator.compress_als(lines, out, **gate)
    assert out.read_bytes() == before
    assert _partials(tmp_path) == []


@pytest.mark.parametrize("case", ["track_count", "mixer_devices"])
def test_d22_the_job_gate_error_text_is_the_text_generate_session_used_to_raise(
        tmp_path, case):
    import validate_als
    from automated_dj_mixes import als_generator
    lines, gate = _gate_case(case)
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    # The old path: publish after the basic gate, then run the job gate on the
    # published file and raise this message (the f-string is the old one).
    old_out = old_dir / "Sections V9.als"
    als_generator.compress_als(lines, old_out)
    errors = validate_als.validate_als(
        old_out, expected_track_count=gate.get("expect_track_count"),
        expected_track_devices=gate.get("expect_track_devices"))
    assert errors
    old_text = f"ALS expectation gate failed for {old_out.name}: {errors[0]}"
    with pytest.raises(ValueError) as exc:
        als_generator.compress_als(lines, new_dir / "Sections V9.als", **gate)
    assert str(exc.value) == old_text


def test_d22_a_passing_job_gate_writes_the_same_bytes_as_before(tmp_path, monkeypatch):
    import time
    from automated_dj_mixes import als_generator
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    lines = _one_clip_als()
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old = old_dir / "Sections V9.als"
    with gzip.open(old, "wb") as f:        # the pre-D22 direct write
        f.write("".join(lines).encode("utf-8"))
    new = new_dir / "Sections V9.als"
    assert als_generator.compress_als(lines, new, expect_track_count=1) == new
    assert new.read_bytes() == old.read_bytes()
    assert _partials(new_dir) == []


TEMPLATE = ROOT / "Templates" / "DJ Mix Template 2026.als"
skip_no_template = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="ALS template not present")


def _template_patch():
    from automated_dj_mixes.als_generator import TrackPatch
    from automated_dj_mixes.analysis import TrackAnalysis
    from automated_dj_mixes.warping import WarpMarker
    analysis = TrackAnalysis(
        path=Path("C:/fake/test_track.wav"), key="Am", camelot="8A", bpm=126.0,
        lufs=-8.5, first_downbeat_sec=0.3, duration_sec=300.0, sample_rate=44100)
    markers = [WarpMarker(beat_time=0.0, sample_time=0.3),
               WarpMarker(beat_time=629.37, sample_time=300.0)]
    return TrackPatch(analysis=analysis, track_index=0, warp_markers=markers)


@skip_no_template
@pytest.mark.parametrize("old_output_exists", [False, True])
def test_d22_generate_session_publishes_nothing_when_the_job_gate_fails(
        tmp_path, monkeypatch, old_output_exists):
    import validate_als
    from automated_dj_mixes import als_generator
    real = validate_als.validate_als

    def forced(path, expected_track_count=None, expected_track_devices=None,
               expected_transitions=None):
        if (expected_track_count is None and expected_track_devices is None
                and expected_transitions is None):
            return real(path)             # the basic gate stays real
        return ["forced expectation failure"]

    monkeypatch.setattr(validate_als, "validate_als", forced)
    if old_output_exists:
        out, before = _existing_output(tmp_path)
    else:
        out, before = tmp_path / "Sections V9.als", None
    with pytest.raises(ValueError) as exc:
        als_generator.generate_session(TEMPLATE, [_template_patch()], out)
    assert str(exc.value) == (
        f"ALS expectation gate failed for {out.name}: forced expectation failure")
    if before is None:
        assert not out.exists()
    else:
        assert out.read_bytes() == before
    assert _partials(tmp_path) == []


@skip_no_template
def test_d22_generate_session_runs_the_job_gate_on_the_partial_not_the_published_file(
        tmp_path, monkeypatch):
    import validate_als
    from automated_dj_mixes import als_generator
    real = validate_als.validate_als
    calls = []

    def spy(path, *args, **kwargs):
        calls.append((Path(path), kwargs.get("expected_track_count"),
                      kwargs.get("expected_track_devices")))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(validate_als, "validate_als", spy)
    out = tmp_path / "Sections V9.als"
    als_generator.generate_session(TEMPLATE, [_template_patch()], out)
    job = [c for c in calls if c[1] is not None]
    assert len(job) == 1
    path, count, devices = job[0]
    assert path.name.startswith("Sections V9.als.") and path.name.endswith(".partial")
    assert count == 1 and devices == validate_als.MIXER_AUTOMATION_DEVICES
    assert all(c[0] != out for c in calls), "nothing re-validates the published file"
    assert out.exists() and _partials(tmp_path) == []


# Item 2: the trim guard's SOURCE (loop) window, on its own.

def test_d19_trim_named_clip_front_refuses_when_only_the_loop_window_is_too_short():
    lines = [ln.replace('<LoopEnd Value="32.0" />', '<LoopEnd Value="16.0" />')
             for ln in _one_clip_als(time=100.0, length=32.0, src=0.0)]
    f = _clip_fields(lines)
    # arrangement window 32 beats, source window 16 beats; trim 20 is between
    assert (f["CurrentEnd"] - f["CurrentStart"], f["LoopEnd"] - f["LoopStart"]) == (32.0, 16.0)
    before = list(lines)
    with pytest.raises(ValueError, match="LoopEnd - LoopStart"):
        _trim(lines, 20.0)
    assert lines == before
    assert _trim(lines, 12.0) == 3         # a trim inside both windows still works


# Item 3: a text output path.

@pytest.mark.parametrize("name", _WRITERS)
def test_d22_a_text_output_path_still_works_for_a_valid_write(
        tmp_path, monkeypatch, name):
    import validate_als
    monkeypatch.setattr(validate_als, "report_als", lambda p: [])
    out = tmp_path / "Sections V9.als"
    result = _writer(name)(["<Ableton/>\n"], str(out))
    assert Path(result) == out
    assert gzip.open(out, "rb").read() == b"<Ableton/>\n"
    assert _partials(tmp_path) == []


@pytest.mark.parametrize("name", _WRITERS)
def test_d22_a_text_output_path_is_refused_with_valueerror_not_attributeerror(
        tmp_path, name):
    out = tmp_path / "Sections V9.als"
    with pytest.raises(ValueError, match="ALS validation failed"):
        _writer(name)(["<not-an-als>\n"], str(out))
    assert not out.exists()
    assert _partials(tmp_path) == []


# Item 4: the guard reads numbers the way an XML writer may spell them.

@pytest.mark.parametrize("start_text, end_text", [
    ("1E2", "1.32E+2"), ("+100", "+132"), ("1.0E+02", "132")])
def test_d19_trim_named_clip_front_measures_exponent_and_plus_sign_values(
        start_text, end_text):
    # The loop window is made longer than the trim so only the arrangement
    # window, written in the odd spelling, can raise.
    lines = [ln.replace('<CurrentStart Value="100.0" />',
                        f'<CurrentStart Value="{start_text}" />')
               .replace('<CurrentEnd Value="132.0" />',
                        f'<CurrentEnd Value="{end_text}" />')
               .replace('<LoopEnd Value="32.0" />', '<LoopEnd Value="64.0" />')
             for ln in _one_clip_als(time=100.0, length=32.0)]
    before = list(lines)
    with pytest.raises(ValueError, match="CurrentEnd - CurrentStart"):
        _trim(lines, 32.0)
    assert lines == before


def test_d19_trim_named_clip_front_does_not_refuse_a_window_it_cannot_measure():
    lines = [ln for ln in _one_clip_als()
             if "<LoopStart" not in ln and "<LoopEnd" not in ln]
    assert _trim(lines, 12.0) == 2          # Time + CurrentStart only


# --------------------------------------------------------------------------- #
# Round 3 (2026-11-04): the edit loops of trim_named_clip_front and           #
# shift_named_clip read numbers the way the trim guard does, so an exponent   #
# or plus-sign start is moved, not just measured (no silent half-trim).       #
# --------------------------------------------------------------------------- #

import re


def _shift(lines, delta, name="intro_1"):
    start, end, _ = apply_loops.find_track_line_ranges(lines)[0]
    return apply_loops.shift_named_clip(lines, start, end, name, delta)


def _spelled_clip(time_text, cs_text, ce_text, ls_text, le_text):
    """The clip of _one_clip_als(time=100, length=32, src=4) with each number
    written the way the arguments spell it."""
    swaps = [
        ('Time="100.0"', f'Time="{time_text}"'),
        ('<CurrentStart Value="100.0" />', f'<CurrentStart Value="{cs_text}" />'),
        ('<CurrentEnd Value="132.0" />', f'<CurrentEnd Value="{ce_text}" />'),
        ('<LoopStart Value="4.0" />', f'<LoopStart Value="{ls_text}" />'),
        ('<LoopEnd Value="36.0" />', f'<LoopEnd Value="{le_text}" />'),
    ]
    out = []
    for ln in _one_clip_als(time=100.0, length=32.0, src=4.0):
        for old, new in swaps:
            ln = ln.replace(old, new)
        out.append(ln)
    return out


def _written(lines):
    text = "".join(lines)
    get = lambda pattern: re.search(pattern, text).group(1)
    return {"Time": get(r'<AudioClip Id="1" Time="([^"]+)"'),
            "CurrentStart": get(r'<CurrentStart Value="([^"]+)"'),
            "CurrentEnd": get(r'<CurrentEnd Value="([^"]+)"'),
            "LoopStart": get(r'<LoopStart Value="([^"]+)"'),
            "LoopEnd": get(r'<LoopEnd Value="([^"]+)"')}


def _changed_lines(before, after):
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


@pytest.mark.parametrize("time_text, cs_text, ce_text, ls_text, le_text", [
    ("100.0", "1E2", "132.0", "4E0", "36.0"),
    ("+100", "+100", "1.32E+2", "+4", "3.6E1"),
    ("1E2", "1.0E+02", "132.0", "4.0", "+36"),
])
def test_d19_trim_named_clip_front_moves_odd_spellings_by_exactly_the_trim(
        time_text, cs_text, ce_text, ls_text, le_text):
    lines = _spelled_clip(time_text, cs_text, ce_text, ls_text, le_text)
    before = list(lines)
    assert _trim(lines, 12.0) == 3
    w = _written(lines)
    assert (float(w["Time"]), float(w["CurrentStart"]), float(w["LoopStart"])) == (
        112.0, 112.0, 16.0)
    for key in ("Time", "CurrentStart", "LoopStart"):
        assert re.fullmatch(r"-?\d+\.\d+", w[key]), (key, w[key])   # no exponent
    # CurrentEnd and LoopEnd are untouched, not even re-spelled.
    changed = _changed_lines(before, lines)
    assert len(changed) == 3 and all("End" not in lines[i] for i in changed)
    assert (w["CurrentEnd"], w["LoopEnd"]) == (ce_text, le_text)


@pytest.mark.parametrize("time_text, cs_text, ce_text", [
    ("1E2", "+100", "1.32E+2"),
    ("+100", "1.0E+02", "+132"),
])
def test_d19_shift_named_clip_moves_odd_spellings_by_exactly_the_delta(
        time_text, cs_text, ce_text):
    lines = _spelled_clip(time_text, cs_text, ce_text, "4.0", "36.0")
    before = list(lines)
    assert _shift(lines, 8.0) == 3
    w = _written(lines)
    assert (float(w["Time"]), float(w["CurrentStart"]), float(w["CurrentEnd"])) == (
        108.0, 108.0, 140.0)
    for key in ("Time", "CurrentStart", "CurrentEnd"):
        assert re.fullmatch(r"-?\d+\.\d+", w[key]), (key, w[key])   # no exponent
    # The source window is not touched by a shift.
    changed = _changed_lines(before, lines)
    assert len(changed) == 3 and all("Loop" not in lines[i] for i in changed)
    assert (w["LoopStart"], w["LoopEnd"]) == ("4.0", "36.0")


@pytest.mark.parametrize("kind, spec, amount, expected_changes", [
    # Expected lines were taken from the trim_named_clip_front and
    # shift_named_clip that existed before round 3, run on these clips.
    ("trim", dict(time=100.0, length=32.0, src=0.0), 12.0,
     {9: '<AudioClip Id="1" Time="112.0">\r\n',
      10: '  <CurrentStart Value="112.0" />\r\n',
      13: '    <LoopStart Value="12.0" />\r\n'}),
    ("trim", dict(time=100.25, length=31.5, src=4.5), 3.5,
     {9: '<AudioClip Id="1" Time="103.75">\r\n',
      10: '  <CurrentStart Value="103.75" />\r\n',
      13: '    <LoopStart Value="8.0" />\r\n'}),
    ("shift", dict(time=100.0, length=32.0, src=0.0), 8.0,
     {9: '<AudioClip Id="1" Time="108.0">\r\n',
      10: '  <CurrentStart Value="108.0" />\r\n',
      11: '  <CurrentEnd Value="140.0" />\r\n'}),
    ("shift", dict(time=100.25, length=31.5, src=4.5), -2.25,
     {9: '<AudioClip Id="1" Time="98.0">\r\n',
      10: '  <CurrentStart Value="98.0" />\r\n',
      11: '  <CurrentEnd Value="129.5" />\r\n'}),
])
def test_d19_plain_decimal_clips_give_the_same_lines_as_the_pre_round_3_functions(
        kind, spec, amount, expected_changes):
    lines = _one_clip_als(**spec)
    expected = list(lines)
    for index, text in expected_changes.items():
        expected[index] = text
    assert (_trim if kind == "trim" else _shift)(lines, amount) == 3
    assert lines == expected
