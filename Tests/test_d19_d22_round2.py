"""Round 2 of the D19 / D22 guards (Codex review, 2026-11-20).

1. D19: the trim guard reads either XML quote style and fails closed when it
   is about to move a window's start but cannot read that window's end.
2. D22: every compress_als call stages to its OWN `.partial`, so two writers
   aimed at one output can not swap each other's bytes.
3. D22: with a MixPlan, a failed reconcile hard gate leaves the output and the
   arrangement report exactly as they were before the call.
4. A decision tail_loop may not carry a negative partial_bars.
"""
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as ae
import apply_automation
import apply_loops
from automated_dj_mixes import als_generator


# --------------------------------------------------------------------------- #
# 1. trim_named_clip_front                                                    #
# --------------------------------------------------------------------------- #

def _clip(cs="100.0", ce="132.0", ls="0.0", le="32.0", time="100.0"):
    """One audio clip whose four window values are written exactly as given."""
    return [
        '<AudioTrack Id="9">\n', '<Name><EffectiveName Value="IN" /></Name>\n',
        f'<AudioClip Id="1" Time={time}>\n',
        f'  <CurrentStart Value={cs} />\n',
        f'  <CurrentEnd Value={ce} />\n',
        '  <Loop>\n',
        f'    <LoopStart Value={ls} />\n',
        f'    <LoopEnd Value={le} />\n',
        '  </Loop>\n',
        '  <Name Value="intro_1" />\n',
        '</AudioClip>\n', '</AudioTrack>\n']


def _trim(lines, beats):
    start, end, _ = apply_loops.find_track_line_ranges(lines)[0]
    return apply_loops.trim_named_clip_front(lines, start, end, "intro_1", beats)


@pytest.mark.parametrize("kwargs, window", [
    # arrangement window is exactly one trim long; the source window is longer
    (dict(ce="'132.0'", le='"64.0"'), "CurrentEnd - CurrentStart"),
    # source window is exactly one trim long; the arrangement window is longer
    (dict(ce='"164.0"', le="'32.0'"), "LoopEnd - LoopStart"),
])
def test_single_quoted_end_is_read_and_a_trim_of_the_whole_clip_is_refused(
        kwargs, window):
    lines = _clip(cs='"100.0"', ls='"0.0"', time='"100.0"', **kwargs)
    before = list(lines)
    with pytest.raises(ValueError, match=window):
        _trim(lines, 32.0)
    assert lines == before


def test_single_quoted_clip_is_trimmed_by_exactly_the_trim_and_keeps_its_quotes():
    lines = _clip(cs="'100.0'", ce="'132.0'", ls="'0.0'", le="'32.0'", time="'100.0'")
    assert _trim(lines, 12.0) == 3
    text = "".join(lines)
    assert "Time='112.0'" in text
    assert "<CurrentStart Value='112.0' />" in text
    assert "<LoopStart Value='12.0' />" in text
    assert "<CurrentEnd Value='132.0' />" in text and "<LoopEnd Value='32.0' />" in text


@pytest.mark.parametrize("hi_tag", ["CurrentEnd", "LoopEnd"])
@pytest.mark.parametrize("how", ["unreadable", "missing"])
def test_a_start_with_no_readable_end_fails_closed_before_any_edit(hi_tag, how):
    lines = _clip(cs='"100.0"', ce='"132.0"', ls='"0.0"', le='"32.0"', time='"100.0"')
    if how == "unreadable":
        lines = [ln.replace(f'<{hi_tag} Value="', f'<{hi_tag} Value="n/a') for ln in lines]
    else:
        lines = [ln for ln in lines if f"<{hi_tag} " not in ln]
    before = list(lines)
    with pytest.raises(ValueError, match="refusing to"):
        _trim(lines, 12.0)
    assert lines == before


def test_a_window_with_neither_start_nor_end_is_still_left_alone():
    lines = [ln for ln in _clip(cs='"100.0"', ce='"132.0"', ls='"0.0"', le='"32.0"',
                                time='"100.0"')
             if "<LoopStart" not in ln and "<LoopEnd" not in ln]
    assert _trim(lines, 12.0) == 2


# --------------------------------------------------------------------------- #
# 2. unique partial per writer                                                #
# --------------------------------------------------------------------------- #

VALID_LINES = [
    '<?xml version="1.0" encoding="utf-8"?>\n',
    '<Ableton MajorVersion="5" MinorVersion="0">\n',
    '<MainTrack>\n<Tempo>\n<Manual Value="120.0" />\n</Tempo>\n</MainTrack>\n',
    '</Ableton>\n',
]
BAD_LINES = ["<not-an-als>\n"]


def _unpack(path):
    with gzip.open(path, "rb") as f:
        return f.read()


def _partials(folder):
    return sorted(p.name for p in Path(folder).iterdir() if p.name.endswith(".partial"))


@pytest.mark.parametrize("writer", [apply_loops.compress_als, als_generator.compress_als,
                                    apply_automation.compress_als],
                         ids=["apply_loops", "als_generator", "apply_automation"])
def test_a_second_writer_between_validate_and_rename_never_lands_on_the_output(
        tmp_path, monkeypatch, writer):
    import validate_als
    real = validate_als.report_als
    state = {"inside": False, "partials": []}

    def hook(path, *args, **kwargs):
        errors = real(path, *args, **kwargs)
        if not state["inside"]:
            state["inside"] = True
            state["partials"].append(Path(path).name)
            try:
                # writer B: same output, bad bytes, runs while A sits between
                # its validation and its rename
                with pytest.raises(ValueError):
                    writer(BAD_LINES, out)
            finally:
                state["inside"] = False
        return errors

    monkeypatch.setattr(validate_als, "report_als", hook)
    out = tmp_path / "Sections V3.als"
    writer(VALID_LINES, out)
    assert _unpack(out) == "".join(VALID_LINES).encode("utf-8")
    assert _partials(tmp_path) == []
    # the staged name is per call and still ends in .partial
    assert re.fullmatch(r"Sections V3\.als\..+\.partial", state["partials"][0])


@pytest.mark.parametrize("writer", [apply_loops.compress_als, als_generator.compress_als],
                         ids=["apply_loops", "als_generator"])
def test_two_calls_use_different_partial_names(tmp_path, monkeypatch, writer):
    import validate_als
    real = validate_als.report_als
    seen = []

    def hook(path, *args, **kwargs):
        seen.append(Path(path).name)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(validate_als, "report_als", hook)
    writer(VALID_LINES, tmp_path / "a.als")
    writer(VALID_LINES, tmp_path / "a.als")
    assert len(seen) == 2 and seen[0] != seen[1]
    assert all(n.endswith(".partial") for n in seen)


# --------------------------------------------------------------------------- #
# 3. apply_automation.main with a MixPlan                                     #
# --------------------------------------------------------------------------- #

from test_codex_round2_blockers import _report_for_end_to_end, _write_sections_als  # noqa: E402


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _setup(tmp_path, monkeypatch, with_plan=True):
    report = _report_for_end_to_end(tmp_path)
    als_in = tmp_path / "in.als"
    als_out = tmp_path / "out.als"
    _write_sections_als(als_in, devices=True)
    (tmp_path / "sections.json").write_text("{}", encoding="utf-8")
    plan = tmp_path / "MIX_PLAN.json"
    plan.write_text("{}", encoding="utf-8")
    argv = ["apply_automation.py", str(als_in), str(tmp_path / "sections.json"),
            str(als_out), str(report)]
    if with_plan:
        argv.append(str(plan))
    monkeypatch.setattr(sys, "argv", argv)
    return report, als_out


def _strays(folder):
    return sorted(p.name for p in Path(folder).iterdir()
                  if p.name.endswith((".partial", ".tmp")))


def _patch_reconcile(monkeypatch, fail):
    import validate_mix_plan_als
    seen = {}

    def fake(plan_path, report_path, als_path):
        seen["swap"] = json.loads(Path(report_path).read_text(encoding="utf-8"))[
            "transitions"][0]["swap_beats"]
        seen["als_exists"] = Path(als_path).is_file()
        if fail:
            raise ValueError("MixPlan plan_hash is stale; forced FAIL")
        return {"status": "PASS", "checks": [], "errors": []}

    monkeypatch.setattr(validate_mix_plan_als, "reconcile", fake)
    return seen


@pytest.mark.parametrize("pre_existing", [False, True], ids=["no_output_before", "output_before"])
def test_a_failed_reconcile_publishes_nothing_and_leaves_the_report_alone(
        tmp_path, monkeypatch, pre_existing):
    report, out = _setup(tmp_path, monkeypatch)
    seen = _patch_reconcile(monkeypatch, fail=True)
    report_before = _sha(report)
    if pre_existing:
        apply_loops.compress_als(VALID_LINES, out)
        out_before = _sha(out)
    with pytest.raises(ValueError, match="forced FAIL"):
        apply_automation.main()
    # the gate really did see the mutated report (792 = clamped from 796)
    assert seen["swap"] == 792.0
    assert _sha(report) == report_before
    if pre_existing:
        assert _sha(out) == out_before
    else:
        assert not out.exists()
    assert _strays(tmp_path) == []


def test_a_passing_reconcile_publishes_the_same_files_as_a_run_without_a_plan(
        tmp_path, monkeypatch):
    plan_dir = tmp_path / "with_plan"
    plain_dir = tmp_path / "plain"
    plan_dir.mkdir()
    plain_dir.mkdir()
    report_a, out_a = _setup(plan_dir, monkeypatch, with_plan=True)
    seen = _patch_reconcile(monkeypatch, fail=False)
    apply_automation.main()
    report_b, out_b = _setup(plain_dir, monkeypatch, with_plan=False)
    apply_automation.main()
    assert seen["als_exists"] is True
    assert _unpack(out_a) == _unpack(out_b)
    assert report_a.read_bytes() == report_b.read_bytes()
    tr = json.loads(report_a.read_text(encoding="utf-8"))["transitions"][0]
    assert tr["swap_beats"] == 792.0 and tr["swap_beats_pre_automation"] == 796.0
    assert _strays(plan_dir) == []


def test_without_a_plan_the_run_is_unchanged_and_reconcile_is_never_called(
        tmp_path, monkeypatch):
    report, out = _setup(tmp_path, monkeypatch, with_plan=False)
    seen = _patch_reconcile(monkeypatch, fail=True)
    apply_automation.main()
    assert out.is_file() and "swap" not in seen
    assert _strays(tmp_path) == []


# --------------------------------------------------------------------------- #
# 4. fills_from_decision                                                      #
# --------------------------------------------------------------------------- #

def _track(name, n_bars):
    return ae.Track(
        name=name, bpm=128.0, spb=4 * 60.0 / 128.0, downbeat=0.0, n_bars=n_bars,
        sections=[{"name": "drop_1", "label": "drop", "start_bar": 0.0,
                   "end_bar": float(n_bars)}],
        bass_in_bar=0.0, bass_out_bar=float(n_bars) - 8, last_min_bars=64)


def _tail(**over):
    loop = {"source_start_bar": 90, "source_end_bar": 94, "reps": 1, "partial_bars": 0}
    loop.update(over)
    return {"pair_index": 3, "swap_in_bar": 8, "tail_loop": loop}


def test_a_negative_partial_bars_is_refused_like_the_other_tail_loop_checks():
    o, i = _track("out", 110), _track("in", 80)
    with pytest.raises(ValueError, match=r"decision for pair 3: tail_loop partial_bars"):
        ae.fills_from_decision(o, i, _tail(partial_bars=-8))


def test_a_negative_partial_bars_is_refused_even_when_reps_would_cover_it():
    o, i = _track("out", 110), _track("in", 80)
    with pytest.raises(ValueError, match="partial_bars -1 must not be negative"):
        ae.fills_from_decision(o, i, _tail(reps=4, partial_bars=-1))


@pytest.mark.parametrize("reps, partial, marker", [(1, 0, 114.0), (0, 2, 112.0),
                                                   (2, 2, 120.0)])
def test_valid_tail_loops_are_unchanged(reps, partial, marker):
    o, i = _track("out", 110), _track("in", 80)
    (spec,) = ae.fills_from_decision(o, i, _tail(reps=reps, partial_bars=partial))
    assert spec.kind == "outgoing_tail" and spec.target_marker_bar == marker


# =========================================================================== #
# Round 3                                                                     #
# =========================================================================== #

# --- D: structural, fail-closed trim -------------------------------------- #

def _plain_clip():
    return _clip(cs='"100.0"', ce='"132.0"', ls='"4.0"', le='"36.0"', time='"100.0"')


def _line_of(lines, tag):
    return next(i for i, ln in enumerate(lines) if f"<{tag} " in ln)


def _m_whitespace(lines, tag):
    i = _line_of(lines, tag)
    lines[i] = lines[i].replace("Value=", "Value = ")


def _m_attribute_first(lines, tag):
    i = _line_of(lines, tag)
    lines[i] = lines[i].replace(f"<{tag} ", f'<{tag} Id="7" ')


def _m_next_line(lines, tag):
    i = _line_of(lines, tag)
    lines[i:i + 1] = [lines[i].replace(f"<{tag} Value", f"<{tag}\n    Value", 1)]


def _m_entity(lines, tag):
    i = _line_of(lines, tag)
    lines[i] = re.sub(r'Value="(\d)', lambda m: f'Value="&#{ord(m.group(1))};', lines[i])


def _m_commented_duplicate(lines, tag):
    i = _line_of(lines, tag)
    lines.insert(i, f'  <!-- <{tag} Value="1.0" /> -->\n')


def _m_real_duplicate(lines, tag):
    i = _line_of(lines, tag)
    lines.insert(i, lines[i])


@pytest.mark.parametrize("mutate", [_m_whitespace, _m_attribute_first, _m_next_line,
                                    _m_entity, _m_commented_duplicate,
                                    _m_real_duplicate],
                         ids=lambda f: f.__name__[3:])
@pytest.mark.parametrize("tag", ["CurrentStart", "CurrentEnd", "LoopStart", "LoopEnd"])
def test_a_tag_that_is_not_exactly_one_plain_value_is_refused_with_no_line_changed(
        tag, mutate):
    lines = _plain_clip()
    mutate(lines, tag)
    before = list(lines)
    with pytest.raises(ValueError, match=tag):
        _trim(lines, 12.0)
    assert lines == before


@pytest.mark.parametrize("time_attr", ['Time = "100.0"', 'Id="2" Time="100.0" Time="5"'])
def test_a_time_attribute_that_is_not_one_plain_value_is_refused(time_attr):
    lines = _plain_clip()
    lines = [ln.replace('Time="100.0"', time_attr) for ln in lines]
    before = list(lines)
    with pytest.raises(ValueError, match="Time"):
        _trim(lines, 12.0)
    assert lines == before


def test_time_is_never_moved_when_current_start_is_absent():
    lines = [ln for ln in _plain_clip() if "<CurrentStart" not in ln]
    before = list(lines)
    with pytest.raises(ValueError, match="CurrentStart"):
        _trim(lines, 12.0)
    assert lines == before


def test_a_plain_clip_is_still_trimmed():
    lines = _plain_clip()
    assert _trim(lines, 12.0) == 3
    text = "".join(lines)
    assert 'Time="112.0"' in text and '<CurrentStart Value="112.0" />' in text
    assert '<LoopStart Value="16.0" />' in text


# --- B: the gzip header ---------------------------------------------------- #

def _freeze_time(monkeypatch):
    import time
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)


def test_published_bytes_do_not_depend_on_the_staging_name(tmp_path, monkeypatch):
    _freeze_time(monkeypatch)
    out_a, out_b = tmp_path / "a" / "Sections V9.als", tmp_path / "b" / "Sections V9.als"
    for writer in (apply_loops.compress_als, als_generator.compress_als):
        out_a.parent.mkdir(exist_ok=True)
        out_b.parent.mkdir(exist_ok=True)
        writer(VALID_LINES, out_a)
        writer(VALID_LINES, out_b)
        assert _sha(out_a) == _sha(out_b)
    assert b"Sections V9.als\x00" in out_a.read_bytes()[:64]
    assert b".partial" not in out_a.read_bytes()[:64]


def test_apply_loops_bytes_equal_the_original_direct_write(tmp_path, monkeypatch):
    _freeze_time(monkeypatch)
    old = tmp_path / "old" / "Sections V9.als"
    old.parent.mkdir()
    with gzip.open(old, "wb") as f:      # the write before D22 touched it
        f.write("".join(VALID_LINES).encode("utf-8"))
    new = tmp_path / "new" / "Sections V9.als"
    apply_loops.compress_als(VALID_LINES, new)
    assert new.read_bytes() == old.read_bytes()


# --- C: exclusive staging -------------------------------------------------- #

@pytest.mark.parametrize("module", [apply_loops, als_generator],
                         ids=["apply_loops", "als_generator"])
def test_an_existing_staged_name_is_never_overwritten_and_a_new_token_is_tried(
        tmp_path, monkeypatch, module):
    import secrets
    tokens = iter(["a" * 16, "b" * 16])
    monkeypatch.setattr(secrets, "token_hex", lambda n: next(tokens))
    out = tmp_path / "x.als"
    squatter = tmp_path / ("x.als." + "a" * 16 + ".partial")
    squatter.write_bytes(b"another writer stage")
    module.compress_als(VALID_LINES, out)
    assert squatter.read_bytes() == b"another writer stage"
    assert _unpack(out) == "".join(VALID_LINES).encode("utf-8")


@pytest.mark.parametrize("module", [apply_loops, als_generator],
                         ids=["apply_loops", "als_generator"])
def test_staging_gives_up_after_a_bounded_number_of_collisions(tmp_path, monkeypatch, module):
    import secrets
    monkeypatch.setattr(secrets, "token_hex", lambda n: "c" * 16)
    out = tmp_path / "x.als"
    squatter = tmp_path / ("x.als." + "c" * 16 + ".partial")
    squatter.write_bytes(b"keep")
    with pytest.raises(ValueError, match="unique staging file"):
        module.compress_als(VALID_LINES, out)
    assert squatter.read_bytes() == b"keep" and not out.exists()


def test_the_staging_constructs_remove_their_file_on_any_exit(tmp_path):
    out = tmp_path / "x.als"
    for exc in (KeyboardInterrupt, RuntimeError):
        with pytest.raises(exc):
            with apply_loops.staged_als(VALID_LINES, out) as p:
                assert p.is_file() and p.name.endswith(".partial")
                raise exc()
        assert _partials(tmp_path) == []
        with pytest.raises(exc):
            with apply_loops.staged_file(out, b"data") as p:
                assert p.read_bytes() == b"data"
                raise exc()
        assert _partials(tmp_path) == []
    with pytest.raises(ValueError):
        with apply_loops.staged_als(BAD_LINES, out):
            pytest.fail("an invalid ALS must not be yielded")
    assert _partials(tmp_path) == [] and not out.exists()


# --- A: staged reconcile --------------------------------------------------- #

def _patch_reconcile_recording(monkeypatch, report, out, outcome):
    import validate_mix_plan_als
    seen = {}

    def fake(plan_path, report_path, als_path):
        seen["staged_report"] = Path(report_path) != report
        seen["staged_als"] = Path(als_path) != out
        seen["swap"] = json.loads(Path(report_path).read_text(encoding="utf-8"))[
            "transitions"][0]["swap_beats"]
        seen["real_report_sha"] = _sha(report)
        seen["out_exists"] = out.exists()
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        if outcome == "fail":
            raise ValueError("MixPlan plan_hash is stale; forced FAIL")
        return {"status": "PASS", "checks": [], "errors": []}

    monkeypatch.setattr(validate_mix_plan_als, "reconcile", fake)
    return seen


@pytest.mark.parametrize("outcome, exc", [("fail", ValueError),
                                          ("interrupt", KeyboardInterrupt)])
def test_reconcile_sees_staged_files_and_the_finals_stay_untouched_on_failure(
        tmp_path, monkeypatch, outcome, exc):
    report, out = _setup(tmp_path, monkeypatch)
    seen = _patch_reconcile_recording(monkeypatch, report, out, outcome)
    before = _sha(report)
    with pytest.raises(exc):
        apply_automation.main()
    assert seen["staged_report"] and seen["staged_als"]
    assert seen["swap"] == 792.0                # the gate saw the updated report
    assert seen["real_report_sha"] == before    # while the real one was untouched
    assert seen["out_exists"] is False          # and nothing was published yet
    assert _sha(report) == before and not out.exists()
    assert _strays(tmp_path) == []


def test_a_pass_publishes_the_als_then_the_report(tmp_path, monkeypatch):
    report, out = _setup(tmp_path, monkeypatch)
    seen = _patch_reconcile_recording(monkeypatch, report, out, "pass")
    before = _sha(report)
    apply_automation.main()
    assert seen["out_exists"] is False and seen["real_report_sha"] == before
    assert out.is_file() and _sha(report) != before
    assert json.loads(report.read_text(encoding="utf-8"))["transitions"][0][
        "swap_beats"] == 792.0
    assert _strays(tmp_path) == []


def test_a_failure_between_the_two_publishes_leaves_new_als_and_old_report(
        tmp_path, monkeypatch):
    report, out = _setup(tmp_path, monkeypatch)
    _patch_reconcile_recording(monkeypatch, report, out, "pass")
    before = _sha(report)
    real_replace = apply_automation.os.replace

    def replace(src, dst):
        if Path(dst) == report:
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(apply_automation.os, "replace", replace)
    with pytest.raises(OSError, match="disk full"):
        apply_automation.main()
    assert out.is_file() and _sha(report) == before
    assert _strays(tmp_path) == []


# --- E: tail_loop limits --------------------------------------------------- #

@pytest.mark.parametrize("reps", [2.5, float("nan"), float("inf"), True, "2"])
def test_tail_loop_reps_must_be_a_whole_number(reps):
    o, i = _track("out", 110), _track("in", 80)
    with pytest.raises(ValueError, match="not a whole number"):
        ae.fills_from_decision(o, i, _tail(reps=reps))


def test_tail_loop_reps_above_the_repeat_limit_are_refused():
    o, i = _track("out", 110), _track("in", 80)
    with pytest.raises(ValueError, match="repeat limit"):
        ae.fills_from_decision(o, i, _tail(reps=ae.MAX_LOOP_REPEATS + 1))


def test_tail_loop_partial_must_be_less_than_the_loop():
    o, i = _track("out", 110), _track("in", 80)
    with pytest.raises(ValueError, match="less than the 4-bar loop"):
        ae.fills_from_decision(o, i, _tail(partial_bars=4))


def test_tail_loop_total_extension_above_the_limit_is_refused_and_the_limit_is_allowed():
    o, i = _track("out", 110), _track("in", 80)
    with pytest.raises(ValueError, match="extension"):
        ae.fills_from_decision(o, i, _tail(source_end_bar=95, reps=8))   # 8 x 5 = 40
    (spec,) = ae.fills_from_decision(o, i, _tail(reps=8))                 # 8 x 4 = 32
    assert spec.target_marker_bar == 110 + ae.MAX_LOOP_EXTENSION_BARS


def test_tail_loop_limits_are_the_validate_loop_spec_limits():
    assert ae.MAX_LOOP_REPEATS == apply_loops.MAX_LOOP_REPEATS
    assert ae.MAX_LOOP_EXTENSION_BARS * 4 == apply_loops.MAX_LOOP_EXTENSION_BEATS
