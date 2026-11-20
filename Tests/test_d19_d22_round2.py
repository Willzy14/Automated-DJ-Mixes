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
    with pytest.raises(ValueError, match="no readable"):
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
