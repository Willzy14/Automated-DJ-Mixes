"""MixPlan reconciliation of a phased ALS against the grid phase record (validate_mix_plan_als.py --grid-phase).

The ALS, the plan and the pins come from the reconcile fixture; the phased file is made by the real text patch
(grid_phase_fix.patch_als) and the record by the real writer (grid_phase_fix.build_record), so the validator is shown the
files the tool writes. Every tamper test makes the record agree with the tampered ALS first (a forger would), so only the
check under test can catch it."""
import builtins
import gzip
import hashlib
import io
import json
import math
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Source"))
import grid_phase_fix as gpf  # noqa: E402
import test_mix_plan_reconcile_fixture as fx  # noqa: E402
import validate_mix_plan_als as vmp  # noqa: E402

VALIDATOR = ROOT / "Source" / "validate_mix_plan_als.py"
ALPHA, BETA = 0, 1                              # AudioTrack positions in the fixture file

JITTER = (0.0, 0.0012345678901234, -0.0007, 0.0021000000000003, 0.0003333333333, -0.0011, 0.0005, 0.0009)


def _grid(rows):
    return "".join(f'<WarpMarker Id="{i + 1}" SecTime="{sec!r}" BeatTime="{beat!r}" />'
                   for i, (sec, beat) in enumerate(rows))


# a 128 BPM grid with ugly floats (they must survive the json record bit for bit), 1.875 s apart, starting at 2 s so a
# marker can be pulled earlier
RICH = _grid([(2.0 + i * 4 * 60 / 128 + JITTER[i], i * 4.0) for i in range(8)])
# markers 20 ms apart: a 25 ms pull (inside the 27 ms cap) can push one past its neighbour
TIGHT = _grid([(1.0 + i * 0.02, i * 0.0625) for i in range(8)])
# 160 markers one beat (0.5 s) apart: 80 s, so a 14 ms ramp over it changes the tempo by 0.02 BPM only, and the step between
# markers is small: the shape limits can be tested without the tempo limit or the step limit getting in the way
LONG = _grid([(2.0 + i * 0.5, float(i)) for i in range(160)])
# 60 markers 0.25 s apart (240 BPM, 14.75 s): a ramp of a few ms changes the tempo by more than 0.05 BPM
SHORT = _grid([(2.0 + i * 0.25, float(i)) for i in range(60)])


def _pretty(text):
    return text.replace("><", ">\n<")           # one element per line, as the text patch needs


def make_case(tmp_path, monkeypatch, markers=RICH, shifts=None):
    """plan, report, original ALS, phased ALS, record: shifts maps a track position to ms (a number or a function of the
    BeatTime); the default moves the first track by +5 ms."""
    monkeypatch.setattr(fx, "WARP_MARKER_PAIRS", markers)
    plan, report, als = fx.build_reconcile_fixture(tmp_path)
    original = tmp_path / "original.als"
    text = re.sub(r'(<CurrentEnd Value="[^"]*" />)', r'\1<FadeIn Value="0" />', gzip.open(als).read().decode("utf-8"))
    with gzip.open(original, "wb") as fh:                   # a clip setting (FadeIn) the phase must never touch
        fh.write(_pretty(text).encode("utf-8"))
    phased = tmp_path / "phased.als"
    shifts = {ALPHA: 5.0} if shifts is None else shifts
    changed = gpf.patch_als(original, phased, {k: (v if callable(v) else (lambda beat, v=v: v)) for k, v in shifts.items()})
    record = tmp_path / "grid_phase.json"
    record.write_text(json.dumps(gpf.build_record(original, phased, changed), indent=1), encoding="ascii")
    return SimpleNamespace(plan=plan, report=report, original=original, phased=phased, record=record, root=tmp_path)


@pytest.fixture
def case(tmp_path, monkeypatch):
    return make_case(tmp_path, monkeypatch)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reconcile(case, als=None, record=True, original=True):
    return vmp.reconcile(case.plan, case.report, als or case.phased, case.record if record is True else record,
                         case.original if original is True else original)


def fails(case, message, **kwargs):
    with pytest.raises(ValueError) as caught:
        reconcile(case, **kwargs)
    assert re.search(message, str(caught.value)), str(caught.value)


def forge(case, mutate=None, rehash=True):
    """Rewrite the record: mutate(dict), then make its hashes agree with the phased and the original file (as a forger would)."""
    data = json.loads(case.record.read_text())
    if rehash:
        data["phased_als_sha256"] = sha(case.phased)
        data["original_als_sha256"] = sha(case.original)
    if mutate:
        mutate(data)
    case.record.write_text(json.dumps(data, indent=1), encoding="ascii")


def rewrite(path, track, change):
    """Rewrite the WarpMarker lines of every clip of one track of an ALS: change(list of lines) -> list of lines."""
    lines = gzip.open(path).read().decode("utf-8").split("\n")
    out, seen, block = [], -1, None
    for line in lines:
        if line.startswith("<AudioTrack "):
            seen += 1
        if seen == track and line.startswith("<WarpMarker "):
            block = (block or []) + [line]
            continue
        if block is not None:
            out.extend(change(block))
            block = None
        out.append(line)
    with gzip.open(path, "wb") as fh:
        fh.write("\n".join(out).encode("utf-8"))


def tamper(case, track, change):
    rewrite(case.phased, track, change)
    forge(case)


def phased_rows(case):
    return [line for line in gzip.open(case.phased).read().decode("utf-8").split("\n") if line.startswith("<WarpMarker ")]


def sec_of(rows, index):
    return re.search(r'SecTime="([^"]*)"', rows[index]).group(1)


def set_field(index, field, value):
    return lambda rows: [re.sub(f'{field}="[^"]*"', f'{field}="{value}"', row) if i == index else row
                         for i, row in enumerate(rows)]


def drop(index):
    return lambda rows: rows[:index] + rows[index + 1:]


def swap(i, j):
    def run(rows):
        rows = list(rows)
        rows[i], rows[j] = rows[j], rows[i]
        return rows
    return run


def cli(case, als, *extra):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "Source"))
    return subprocess.run([sys.executable, str(VALIDATOR), str(case.plan), str(case.report), str(als), *extra],
                          capture_output=True, text=True, cwd=ROOT, env=env)


def phase_args(case):
    return ["--grid-phase", str(case.record), "--grid-phase-original", str(case.original)]


def edit_als(path, change):
    """Rewrite an ALS (gzip) through change(text) -> text."""
    text = gzip.open(path).read().decode("utf-8")
    with gzip.open(path, "wb") as fh:
        fh.write(change(text).encode("utf-8"))


def ramp(total_ms, count=160, start=0.0):
    """A shift profile rising evenly from start over `count` markers one beat apart."""
    return lambda beat: start + total_ms * beat / (count - 1)


# ---------------------------------------------------------------- the good path and the report

def test_the_original_still_reconciles_and_the_phased_file_without_the_record_still_fails_as_before(case):
    assert vmp.reconcile(case.plan, case.report, case.original)["status"] == "PASS"
    with pytest.raises(ValueError, match="Alpha Track: warp grid mismatch"):
        vmp.reconcile(case.plan, case.report, case.phased)


def test_the_phased_file_passes_with_its_record_and_says_so(case):
    result = reconcile(case)
    assert result["status"] == "PASS" and "warp_grid_phased:trk_a" in result["checks"] and "warp_grid:trk_b" in result["checks"]
    phase = result["grid_phase"]
    assert phase["track_count"] == 1 and phase["max_allowed_shift_ms"] == 27.0
    assert math.isclose(phase["max_shift_ms"], 5.0, abs_tol=1e-6)
    assert phase["phased_als_sha256"] == result["als_sha256"] == sha(case.phased)
    assert phase["original_als_sha256"] == sha(case.original) and phase["record_sha256"] == sha(case.record)
    assert phase["tracks"][0]["name"] == "Alpha Track" and phase["tracks"][0]["doc_index"] == ALPHA


def test_the_plain_reconciliation_has_no_grid_phase_section_and_the_old_pass_line(case):
    assert "grid_phase" not in vmp.reconcile(case.plan, case.report, case.original)
    plain = cli(case, case.original)
    assert plain.returncode == 0, plain.stderr
    assert re.fullmatch(r"PASS: \d+ MixPlan-to-ALS checks\n", plain.stdout), plain.stdout


def test_the_cli_pass_line_and_the_output_json_carry_the_phase(case):
    out = case.root / "recon.json"
    done = cli(case, case.phased, *phase_args(case), "--output", str(out))
    assert done.returncode == 0, done.stderr
    assert re.fullmatch(r"PASS: \d+ MixPlan-to-ALS checks, grid_phase: 1 tracks, max shift 5\.000 ms\n", done.stdout), done.stdout
    assert json.loads(out.read_text())["grid_phase"]["track_count"] == 1


def test_a_failed_phased_reconciliation_is_a_clear_message_not_a_trace_and_keeps_the_failure_as_json(case):
    forge(case, lambda d: d.update(schema="grid-phase/0"))
    out = case.root / "recon.json"
    done = cli(case, case.phased, *phase_args(case), "--output", str(out))
    assert done.returncode == 1 and done.stderr.startswith("FAIL: ") and "Traceback" not in done.stderr
    assert done.stdout == ""
    failure = json.loads(out.read_text())
    assert failure["status"] == "FAIL" and "grid-phase/0" in failure["error"] and "grid_phase" not in failure


def test_the_plain_failure_output_is_unchanged(case):
    # no --grid-phase: a failing reconciliation is still an uncaught ValueError (exit 1, a traceback), not the FAIL line
    done = cli(case, case.phased)
    assert done.returncode == 1 and "Traceback" in done.stderr and "FAIL:" not in done.stderr
    assert "ValueError: Alpha Track: warp grid mismatch" in done.stderr and done.stdout == ""


def test_the_record_and_the_original_are_given_together_or_not_at_all(case):
    only_record = cli(case, case.phased, "--grid-phase", str(case.record))
    only_original = cli(case, case.phased, "--grid-phase-original", str(case.original))
    for done in (only_record, only_original):
        assert done.returncode == 2 and "used together" in done.stderr and "Traceback" not in done.stderr


def test_reconcile_without_the_original_fails_with_a_clear_message(case):
    fails(case, "needs the original ALS to compare the phased file with", original=None)


def test_an_original_without_a_record_fails(case):
    with pytest.raises(ValueError, match="an original ALS was given without a grid phase record"):
        vmp.reconcile(case.plan, case.report, case.original, None, case.original)


def watch_opens(monkeypatch):
    """Every file name opened through builtins.open, io.open (which Path.read_text, read_bytes and open use) or gzip.open."""
    opened = []
    real_open, real_io, real_gzip = builtins.open, io.open, gzip.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: opened.append(Path(f).name) or real_open(f, *a, **k))
    monkeypatch.setattr(io, "open", lambda f, *a, **k: opened.append(Path(f).name) or real_io(f, *a, **k))
    monkeypatch.setattr(gzip, "open", lambda f, *a, **k: opened.append(Path(f).name) or real_gzip(f, *a, **k))
    return opened


def test_the_watch_sees_path_reads_so_the_next_test_is_not_vacuous(tmp_path, monkeypatch):
    (tmp_path / "beside.wav").write_bytes(b"x")
    opened = watch_opens(monkeypatch)
    (tmp_path / "beside.wav").read_bytes()
    (tmp_path / "beside.wav").read_text(errors="replace")
    with (tmp_path / "beside.wav").open("rb"):
        pass
    assert opened.count("beside.wav") == 3


def test_the_validator_with_a_record_reads_only_the_plan_the_report_the_als_the_record_and_the_original(case, monkeypatch):
    opened = watch_opens(monkeypatch)
    reconcile(case)
    assert opened and set(opened) <= {case.plan.name, case.report.name, case.phased.name, case.record.name,
                                      case.original.name}, opened
    assert not any(name.lower().endswith((".wav", ".aif", ".aiff", ".flac", ".mp3")) for name in opened)


# ---------------------------------------------------------------- the pin is still honoured, against the original

def test_a_forged_record_cannot_bless_an_als_that_matches_it_exactly(case):
    # the record says the original markers ARE the phased ones (so the ALS matches the record to the bit), but those
    # markers are not the pinned grid
    pairs = [[float(re.search(f'{f}="([^"]*)"', row).group(1)) for f in ("SecTime", "BeatTime")]
             for row in phased_rows(case)[:8]]

    def forged(data):
        data["tracks"][0]["original_markers"] = pairs
        data["tracks"][0]["max_abs_shift_ms"] = 0.0
    forge(case, forged)
    fails(case, "Alpha Track: grid phase record does not hold the pinned original warp grid")


def test_a_record_with_an_extra_untouched_track_whose_markers_differ_from_the_pin_fails(case):
    def extra(data):
        beta = [[sec + 0.001, beat] for sec, beat in data["tracks"][0]["original_markers"]]
        data["tracks"].append(dict(name="Beta Track", doc_index=BETA, original_markers=beta, max_abs_shift_ms=1.0))
    forge(case, extra)
    fails(case, "Beta Track: grid phase record does not hold the pinned original warp grid")


def test_a_track_outside_the_record_must_still_match_its_pin_exactly(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: 5.0, BETA: 4.0})
    forge(case, lambda d: d.update(tracks=[t for t in d["tracks"] if t["name"] == "Alpha Track"]))   # Beta moved, not recorded
    fails(case, "Beta Track: warp grid mismatch")


def test_two_tracks_in_the_record_both_pass(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: 5.0, BETA: 12.5})
    phase = reconcile(case)["grid_phase"]
    assert phase["track_count"] == 2 and math.isclose(phase["max_shift_ms"], 12.5, abs_tol=1e-6)


# ---------------------------------------------------------------- the cap lives in the validator

@pytest.mark.parametrize("shift, passes", [(26.9, True), (-26.9, True), (27.1, False), (-27.1, False), (29.9, False), (-29.9, False)])
def test_a_constant_shift_over_27_ms_either_way_fails_and_under_it_passes(tmp_path, monkeypatch, shift, passes):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: shift})
    if passes:
        assert reconcile(case)["status"] == "PASS"
    else:
        fails(case, "Alpha Track: phased SecTime moved .* ms at marker \\d+, more than 27 ms")


def test_one_marker_over_the_cap_fails_even_when_the_others_are_inside(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: lambda beat: 27.1 if beat == 12.0 else 2.0})
    fails(case, "moved 27.1\\d* ms at marker 3, more than 27 ms")
    (tmp_path / "neg").mkdir()
    case = make_case(tmp_path / "neg", monkeypatch, shifts={ALPHA: lambda beat: -27.1 if beat == 12.0 else -2.0})
    fails(case, "moved 27.1\\d* ms at marker 3, more than 27 ms")


def test_the_record_cannot_widen_the_cap(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: 31.0})

    def widen(data):
        data["constants"] = dict(MAX_DRIFT_MS=1000.0, MAX_SLIDE_MS=1000.0, MAX_PHASE_SHIFT_MS=1000.0)
        data["MAX_PHASE_SHIFT_MS"] = 1000.0
        data["tool_version"] = "grid_phase_fix 999"
    forge(case, widen)
    fails(case, "more than 27 ms")
    assert (vmp.MAX_ABS_SHIFT_MS, vmp.MAX_SWING_SHIFT_MS, vmp.MAX_STEP_SHIFT_MS, vmp.MAX_BPM_CHANGE) == (27.0, 14.0, 0.5, 0.05)


@pytest.mark.parametrize("start, swing, passes", [
    (-6.95, 13.9, True),        # mixed sign, 13.9 ms end to end
    (-13.9, 13.9, True),        # all negative
    (0.0, 13.9, True),          # all positive
    (-7.05, 14.1, False),
    (-14.1, 14.1, False),
    (0.0, 14.1, False),
])
def test_the_shift_profile_may_swing_14_ms_and_not_more(tmp_path, monkeypatch, start, swing, passes):
    case = make_case(tmp_path, monkeypatch, markers=LONG, shifts={ALPHA: ramp(swing, start=start)})
    if passes:
        assert reconcile(case)["status"] == "PASS"
    else:
        fails(case, "Alpha Track: the shift swings 14\\.\\d+ ms across the track, more than 14 ms")


@pytest.mark.parametrize("step, passes", [(0.4, True), (0.6, False)])
def test_the_shift_may_not_jump_between_neighbouring_markers(tmp_path, monkeypatch, step, passes):
    # every second marker moved by `step` ms: a sawtooth no drift fit makes
    case = make_case(tmp_path, monkeypatch, markers=LONG, shifts={ALPHA: lambda beat: step if int(beat) % 2 else 0.0})
    if passes:
        assert reconcile(case)["status"] == "PASS"
    else:
        fails(case, "Alpha Track: the shift changes by 0\\.6\\d* ms between markers \\d+ and \\d+, more than 0\\.5 ms")


def test_a_sawtooth_a_triangle_a_smooth_drift_and_a_big_constant_all_fail(tmp_path, monkeypatch):
    def run(name, markers, shift, message):
        folder = tmp_path / name
        folder.mkdir()
        fails(make_case(folder, monkeypatch, markers=markers, shifts={ALPHA: shift}), message)
    run("saw", LONG, lambda beat: 29.9 if int(beat) % 2 else -29.9, "between markers .* more than 0.5 ms")
    run("triangle", LONG, lambda beat: 29.0 * (1 - abs(beat - 80.0) / 80.0) - 14.0, "swings .* more than 14 ms")
    run("drift", LONG, ramp(29.0), "more than 27 ms")
    run("drift2", LONG, ramp(29.0), "swings 29.0\\d* ms")
    run("constant", RICH, 29.9, "more than 27 ms")


def test_a_gradual_tempo_error_is_caught_by_the_tracks_own_bpm(tmp_path, monkeypatch):
    # on the short 240 BPM grid a smooth ramp of 3.5 ms moves the track's own tempo by 0.057 BPM, 2.5 ms by 0.041
    ok = make_case(tmp_path, monkeypatch, markers=SHORT, shifts={ALPHA: ramp(2.5, count=60)})
    assert reconcile(ok)["status"] == "PASS"
    (tmp_path / "bad").mkdir()
    bad = make_case(tmp_path / "bad", monkeypatch, markers=SHORT, shifts={ALPHA: ramp(3.5, count=60)})
    fails(bad, "Alpha Track: the phased grid's own tempo .* BPM differs from the pinned .* BPM by 0\\.05\\d*, not under 0\\.05")


def test_a_record_that_understates_its_largest_shift_is_caught(case):
    forge(case, lambda d: d["tracks"][0].update(max_abs_shift_ms=1.0))
    fails(case, "the record says the largest shift is 1.000000 ms, the ALS shows 5.000000 ms")


# ---------------------------------------------------------------- what may change in the phased markers

def test_one_changed_beat_time_fails(case):
    tamper(case, ALPHA, set_field(3, "BeatTime", "12.5"))
    fails(case, "Alpha Track: BeatTime differs from the record at markers \\[3\\]")


def test_a_dropped_marker_fails(case):
    tamper(case, ALPHA, drop(4))
    fails(case, "Alpha Track: phased grid has 7 markers, the record has 8")


def test_two_swapped_markers_fail(case):
    tamper(case, ALPHA, swap(2, 5))
    fails(case, "Alpha Track: BeatTime differs from the record at markers \\[2, 5\\]")


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_a_non_finite_marker_fails(case, bad):
    tamper(case, ALPHA, set_field(3, "SecTime", bad))
    fails(case, "Alpha Track: Warp grid contains a non-finite marker")


def test_markers_that_cross_fail(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, markers=TIGHT)
    assert reconcile(case)["status"] == "PASS"
    # marker 4 pulled 25 ms earlier than the phased value: 20 ms from the original (inside the cap), but past marker 3
    tamper(case, ALPHA, set_field(4, "SecTime", repr(float(sec_of(phased_rows(case), 4)) - 0.025)))
    fails(case, "Alpha Track: phased SecTime is not strictly increasing")


def test_markers_that_meet_exactly_are_not_strictly_increasing(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, markers=TIGHT)
    tamper(case, ALPHA, set_field(4, "SecTime", sec_of(phased_rows(case), 3)))
    fails(case, "Alpha Track: phased SecTime is not strictly increasing")


def test_every_clip_of_a_changed_track_must_carry_the_same_markers(case):
    lines = gzip.open(case.phased).read().decode("utf-8").split("\n")
    first = next(i for i, line in enumerate(lines) if line.startswith("<WarpMarker "))
    lines[first] = re.sub(r'SecTime="[^"]*"', 'SecTime="0.001"', lines[first])      # one clip only
    with gzip.open(case.phased, "wb") as fh:
        fh.write("\n".join(lines).encode("utf-8"))
    forge(case)
    fails(case, "Alpha Track: AudioClips on the track do not share one warp grid")


# ---------------------------------------------------------------- the phased ALS is the original with only the recorded SecTimes moved

def test_the_fade_in_forgery_beside_a_slide_fails(tmp_path, monkeypatch):
    # every Alpha marker +25 ms (inside the envelope) AND one clip's FadeIn 0 -> 12.5; the record is a hand-forged copy
    # of the honest one with the new file's hash. 8da4d41 reconciled this file (PASS).
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: 25.0})
    edit_als(case.phased, lambda text: text.replace('<FadeIn Value="0" />', '<FadeIn Value="12.5" />', 1))
    forge(case)
    fails(case, "the phased ALS differs from the original on 1 line\\(s\\) that are not the SecTime of a recorded track's warp marker")


def test_an_extra_changed_line_anywhere_fails(case):
    edit_als(case.phased, lambda text: text.replace('<Manual Value="128.0" />', '<Manual Value="127.0" />'))
    forge(case)
    fails(case, "differs from the original on 1 line")


def test_a_changed_marker_id_fails(case):
    edit_als(case.phased, lambda text: text.replace('<WarpMarker Id="3" ', '<WarpMarker Id="33" ', 1))
    forge(case)
    fails(case, "differs from the original on 1 line")


def test_a_beat_time_written_differently_but_equal_as_a_number_fails(case):
    # 4.0 -> 4.00: the same float, so only the text comparison can see it
    edit_als(case.phased, lambda text: text.replace('BeatTime="4.0"', 'BeatTime="4.00"', 1))
    forge(case)
    fails(case, "differs from the original on 1 line")


def test_a_changed_line_count_fails(case):
    edit_als(case.phased, lambda text: text.replace('<FadeIn Value="0" />', '<FadeIn Value="0" />\n<Extra />', 1))
    forge(case)
    fails(case, "the phased ALS has \\d+ lines, the original has \\d+")


def test_a_marker_line_of_an_unlisted_track_changed_fails(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: 5.0, BETA: 4.0})
    forge(case, lambda d: d.update(tracks=[t for t in d["tracks"] if t["name"] == "Alpha Track"]))
    fails(case, "differs from the original on \\d+ line\\(s\\)")


def test_a_saved_warp_marker_line_of_a_listed_track_may_not_move(case):
    # lines inside <SavedWarpMarkersForStretched> are not <WarpMarkers>: the patch never touches them, so neither may a phased file
    block = '<SavedWarpMarkersForStretched><WarpMarker Id="9" SecTime="1.5" BeatTime="3" /></SavedWarpMarkersForStretched>'
    edit_als(case.original, lambda text: text.replace("</WarpMarkers>", "</WarpMarkers>" + block, 1).replace("><", ">\n<"))
    edit_als(case.phased, lambda text: text.replace("</WarpMarkers>", "</WarpMarkers>" + block.replace('"1.5"', '"1.6"'), 1).replace("><", ">\n<"))
    forge(case)
    fails(case, "differs from the original on 1 line")


def test_a_missing_original_fails_clearly(case):
    fails(case, "grid phase original ALS nowhere.als cannot be read", original=case.root / "nowhere.als")


def test_a_wrong_original_fails(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    (tmp_path / "other").mkdir()
    other = make_case(tmp_path / "other", monkeypatch, shifts={ALPHA: 5.0})
    edit_als(other.original, lambda text: text.replace('<Manual Value="128.0" />', '<Manual Value="128.5" />'))
    fails(case, "grid phase original ALS is not the file the record describes", original=other.original)


def test_an_all_zero_original_hash_in_the_record_fails(case):
    forge(case, lambda d: d.update(original_als_sha256="0" * 64))
    fails(case, "grid phase original ALS is not the file the record describes: it describes " + "0" * 64)


def test_an_original_that_is_not_one_element_per_line_fails_clearly(case):
    for path in (case.original, case.phased):
        edit_als(path, lambda text: text.replace("\n", ""))
    forge(case)
    fails(case, "is it laid out one element per line")


def test_a_phased_file_that_is_only_the_recorded_seconds_moved_passes(case):
    assert reconcile(case)["grid_phase"]["original_als_checked"] is True


# ---------------------------------------------------------------- the record belongs to this ALS and these tracks

def test_the_right_tracks_but_the_wrong_als_file_fails(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: 5.0})
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = make_case(other_dir, monkeypatch, shifts={ALPHA: 6.0})
    assert sha(case.phased) != sha(other.phased)
    fails(case, "grid phase record belongs to another ALS", als=other.phased)


def test_a_stale_record_for_an_edited_als_fails(case):
    with gzip.open(case.phased, "wb") as fh:
        fh.write(gzip.open(case.original).read())                  # the original bytes, not the phased ones
    fails(case, "grid phase record belongs to another ALS")


def test_a_duplicate_display_name_is_a_precise_failure(case):
    for path in (case.original, case.phased):
        edit_als(path, lambda text: text.replace('<EffectiveName Value="Beta Track"', '<EffectiveName Value="Alpha Track"'))
    forge(case)
    fails(case, "grid phase record names Alpha Track, but more than one track of the mix has that display name")


def test_a_record_for_a_track_not_in_the_plan_fails(case):
    forge(case, lambda d: d["tracks"][0].update(name="Gamma Track"))
    fails(case, "Gamma Track, which is not in the MixPlan")


def test_a_record_for_a_plan_track_missing_from_the_als_fails(case):
    text = gzip.open(case.phased).read().decode("utf-8").replace('<EffectiveName Value="Beta Track"', '<EffectiveName Value="Beta Renamed"')
    with gzip.open(case.phased, "wb") as fh:
        fh.write(text.encode("utf-8"))
    forge(case, lambda d: d["tracks"].append(dict(d["tracks"][0], name="Beta Track", doc_index=BETA)))
    fails(case, "Beta Track, which is not in the ALS")


def test_a_record_entry_at_the_wrong_file_position_fails(case):
    forge(case, lambda d: d["tracks"][0].update(doc_index=5))
    fails(case, "puts Alpha Track at AudioTrack 5, the ALS has it at 0")


def test_a_track_listed_twice_fails(case):
    forge(case, lambda d: d["tracks"].append(dict(d["tracks"][0])))
    fails(case, "lists Alpha Track twice")


# ---------------------------------------------------------------- a broken record is a clear failure, never a pass or a trace

def mangle(**changes):
    def run(data):
        for key, value in changes.items():
            if value is KeyError:
                data.pop(key)
            else:
                data[key] = value
    return run


@pytest.mark.parametrize("mutate, message", [
    (mangle(schema="grid-phase/2"), "schema 'grid-phase/2' is not 'grid-phase/1'"),
    (mangle(schema=KeyError), "schema None is not"),
    (mangle(tracks=KeyError), "lists no changed track"),
    (mangle(tracks=[]), "lists no changed track"),
    (mangle(tracks="Alpha Track"), "lists no changed track"),
    (mangle(original_als_sha256=KeyError), "original_als_sha256 is missing or not a sha256"),
    (mangle(original_als_sha256="abc"), "original_als_sha256 is missing or not a sha256"),
    (mangle(original_als_sha256="G" * 64), "original_als_sha256 is missing or not a sha256"),
    (mangle(phased_als_sha256=17), "phased_als_sha256 is missing or not a sha256"),
    (lambda d: d["tracks"][0].pop("name"), "track 0 has no name"),
    (lambda d: d["tracks"][0].update(doc_index=True), "track 0 has no doc_index"),
    (lambda d: d["tracks"][0].update(doc_index=-1), "track 0 has no doc_index"),
    (lambda d: d["tracks"][0].update(doc_index="0"), "track 0 has no doc_index"),
    (lambda d: d["tracks"][0].update(max_abs_shift_ms="5"), "track 0 has no max_abs_shift_ms"),
    (lambda d: d["tracks"][0].update(max_abs_shift_ms=-1.0), "track 0 has no max_abs_shift_ms"),
    (lambda d: d["tracks"][0].update(max_abs_shift_ms=10 ** 400), "track 0 has no max_abs_shift_ms"),
    (lambda d: d["tracks"][0].pop("original_markers"), "original_markers is not a list of"),
    (lambda d: d["tracks"][0].update(original_markers=[["0.0", "0.0"], ["1.0", "1.0"]]), "original_markers is not a list of"),
    (lambda d: d["tracks"][0].update(original_markers=[[0.0, True], [1.0, 1.0]]), "original_markers is not a list of"),
    (lambda d: d["tracks"][0].update(original_markers=[[0.0, 0.0, 0.0]]), "original_markers is not a list of"),
    (lambda d: d.update(tracks=[7]), "track 0 is not an object"),
])
def test_a_malformed_record_fails_clearly(case, mutate, message):
    forge(case, mutate)
    fails(case, re.escape(message))


@pytest.mark.parametrize("text, message", [
    ("{not json", "cannot be read"),
    ("", "cannot be read"),
    ("[]", "is not a JSON object"),
    ('"grid-phase/1"', "is not a JSON object"),
    ("null", "is not a JSON object"),
    ('{"schema": NaN}', "cannot be read: NaN is not a number"),
    ('{"schema": Infinity}', "cannot be read: Infinity is not a number"),
    pytest.param("[" * 100000, "cannot be read", id="nested-too-deep"),
])
def test_an_unreadable_record_fails_clearly(case, text, message):
    case.record.write_text(text, encoding="ascii")
    fails(case, message)


def test_a_missing_record_file_fails_clearly(case):
    case.record.unlink()
    fails(case, "grid phase record grid_phase.json cannot be read")


def test_a_record_with_a_huge_or_infinite_marker_number_fails_not_crashes(case):
    text = case.record.read_text()
    first = re.search(r'"original_markers": \[\s*\[\s*([-0-9.e]+),', text)
    case.record.write_text(text[:first.start(1)] + "1e999" + text[first.end(1):], encoding="ascii")
    fails(case, "grid phase record")
    forge(case, lambda d: d["tracks"][0].update(original_markers=[[10 ** 400, 0.0], [1.0, 1.0]]))
    fails(case, "grid phase record markers are not a warp grid")


def test_a_record_markers_list_that_is_too_short_or_has_no_duration_fails(case):
    forge(case, lambda d: d["tracks"][0].update(original_markers=[[0.0, 0.0]]))
    fails(case, "fewer than two markers")
    forge(case, lambda d: d["tracks"][0].update(original_markers=[[5.0, 0.0], [1.0, 4.0]]))
    fails(case, "non-positive duration")


# ---------------------------------------------------------------- the writer and the file it writes

def test_json_floats_round_trip_bit_for_bit_so_the_record_hashes_like_the_als():
    rng = random.Random(11)
    values = [rng.uniform(-1e6, 1e6) for _ in range(2000)] + [5e-324, 1.7976931348623157e308, 0.1 + 0.2, 1e-7, 123456789.123456789]
    assert json.loads(json.dumps(values)) == values


def test_the_written_record_lists_only_the_changed_tracks_with_exact_original_markers(case):
    data = json.loads(case.record.read_text())
    assert data["schema"] == "grid-phase/1" and data["original_als_sha256"] == sha(case.original)
    assert data["phased_als_sha256"] == sha(case.phased)
    assert [(t["name"], t["doc_index"]) for t in data["tracks"]] == [("Alpha Track", ALPHA)]
    assert data["constants"] == dict(MAX_DRIFT_MS=gpf.MAX_DRIFT_MS, MAX_SLIDE_MS=gpf.MAX_SLIDE_MS)
    assert data["tool_version"] == gpf.TOOL_VERSION
    assert len(data["tracks"][0]["original_markers"]) == 8 and math.isclose(data["tracks"][0]["max_abs_shift_ms"], 5.0, abs_tol=1e-6)
    assert case.record.read_text().isascii() and chr(0x2014) not in case.record.read_text()


def test_the_writer_refuses_a_phased_file_that_differs_outside_the_warp_marker_lines(case):
    text = gzip.open(case.phased).read().decode("utf-8").replace('<Manual Value="128.0" />', '<Manual Value="127.0" />')
    with gzip.open(case.phased, "wb") as fh:
        fh.write(text.encode("utf-8"))
    with pytest.raises(ValueError, match="outside the WarpMarker lines"):
        gpf.build_record(case.original, case.phased, {ALPHA: 8})


def test_the_writer_refuses_a_moved_beat_time_and_an_unreported_track(case):
    altered = case.root / "altered.als"
    altered.write_bytes(case.phased.read_bytes())
    rewrite(altered, ALPHA, set_field(3, "BeatTime", "0.5"))
    with pytest.raises(ValueError, match="moved BeatTime"):
        gpf.build_record(case.original, altered, {ALPHA: 8})
    with pytest.raises(ValueError, match=r"AudioTracks \[0\] differ but the patch did not report"):
        gpf.build_record(case.original, case.phased, {BETA: 8})
    with pytest.raises(ValueError, match="no warp marker differs"):
        gpf.build_record(case.original, case.original, {})
