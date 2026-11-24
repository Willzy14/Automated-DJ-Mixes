"""MixPlan reconciliation of a phased ALS against the grid phase record (validate_mix_plan_als.py --grid-phase).

The ALS, the plan and the pins come from the reconcile fixture; the phased file is made by the real text patch
(grid_phase_fix.patch_als) and the record by the real writer (grid_phase_fix.build_record), so the validator is shown the
files the tool writes. Every tamper test makes the record agree with the tampered ALS first (a forger would), so only the
check under test can catch it."""
import builtins
import gzip
import hashlib
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


# a 128 BPM grid with ugly floats (they must survive the json record bit for bit), 1.875 s apart
RICH = _grid([(i * 4 * 60 / 128 + JITTER[i], i * 4.0) for i in range(8)])
# markers 20 ms apart: a 25 ms pull (inside the 30 ms cap) can push one past its neighbour
TIGHT = _grid([(1.0 + i * 0.02, i * 0.0625) for i in range(8)])


def _pretty(text):
    return text.replace("><", ">\n<")           # one element per line, as the text patch needs


def make_case(tmp_path, monkeypatch, markers=RICH, shifts=None):
    """plan, report, original ALS, phased ALS, record: shifts maps a track position to ms (a number or a function of the
    BeatTime); the default moves the first track by +5 ms."""
    monkeypatch.setattr(fx, "WARP_MARKER_PAIRS", markers)
    plan, report, als = fx.build_reconcile_fixture(tmp_path)
    original = tmp_path / "original.als"
    with gzip.open(original, "wb") as fh:
        fh.write(_pretty(gzip.open(als).read().decode("utf-8")).encode("utf-8"))
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


def reconcile(case, als=None, record=True):
    return vmp.reconcile(case.plan, case.report, als or case.phased, case.record if record is True else record)


def fails(case, message, **kwargs):
    with pytest.raises(ValueError) as caught:
        reconcile(case, **kwargs)
    assert re.search(message, str(caught.value)), str(caught.value)


def forge(case, mutate=None, rehash=True):
    """Rewrite the record: mutate(dict), then make its phased hash agree with the phased file (as a forger would)."""
    data = json.loads(case.record.read_text())
    if rehash:
        data["phased_als_sha256"] = sha(case.phased)
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


# ---------------------------------------------------------------- the good path and the report

def test_the_original_still_reconciles_and_the_phased_file_without_the_record_still_fails_as_before(case):
    assert vmp.reconcile(case.plan, case.report, case.original)["status"] == "PASS"
    with pytest.raises(ValueError, match="Alpha Track: warp grid mismatch"):
        vmp.reconcile(case.plan, case.report, case.phased)


def test_the_phased_file_passes_with_its_record_and_says_so(case):
    result = reconcile(case)
    assert result["status"] == "PASS" and "warp_grid_phased:trk_a" in result["checks"] and "warp_grid:trk_b" in result["checks"]
    phase = result["grid_phase"]
    assert phase["track_count"] == 1 and phase["max_allowed_shift_ms"] == 30.0
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
    done = cli(case, case.phased, "--grid-phase", str(case.record), "--output", str(out))
    assert done.returncode == 0, done.stderr
    assert re.fullmatch(r"PASS: \d+ MixPlan-to-ALS checks, grid_phase: 1 tracks, max shift 5\.000 ms\n", done.stdout), done.stdout
    assert json.loads(out.read_text())["grid_phase"]["track_count"] == 1


def test_a_failed_phased_reconciliation_is_a_clear_message_not_a_trace_and_writes_no_output(case):
    forge(case, lambda d: d.update(schema="grid-phase/0"))
    out = case.root / "recon.json"
    done = cli(case, case.phased, "--grid-phase", str(case.record), "--output", str(out))
    assert done.returncode == 1 and done.stderr.startswith("FAIL: ") and "Traceback" not in done.stderr
    assert not out.exists() and done.stdout == ""


def test_the_validator_with_a_record_reads_only_the_plan_the_report_the_als_and_the_record(case, monkeypatch):
    opened = []
    real_open, real_gzip = builtins.open, gzip.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: opened.append(Path(f).name) or real_open(f, *a, **k))
    monkeypatch.setattr(gzip, "open", lambda f, *a, **k: opened.append(Path(f).name) or real_gzip(f, *a, **k))
    reconcile(case)
    assert set(opened) <= {case.plan.name, case.report.name, case.phased.name, case.record.name}, opened
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

@pytest.mark.parametrize("shift, passes", [(29.9, True), (30.1, False), (31.0, False)])
def test_a_shift_over_30_ms_fails_and_under_it_passes(tmp_path, monkeypatch, shift, passes):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: shift})
    if passes:
        assert reconcile(case)["status"] == "PASS"
    else:
        fails(case, "Alpha Track: phased SecTime moved .* ms at marker \\d+, more than 30 ms")


def test_one_marker_over_the_cap_fails_even_when_the_others_are_inside(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: lambda beat: 30.1 if beat == 12.0 else 2.0})
    fails(case, "moved 30.1\\d* ms at marker 3, more than 30 ms")


def test_the_record_cannot_widen_the_cap(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, shifts={ALPHA: 31.0})

    def widen(data):
        data["constants"] = dict(MAX_DRIFT_MS=1000.0, MAX_SLIDE_MS=1000.0, MAX_PHASE_SHIFT_MS=1000.0)
        data["MAX_PHASE_SHIFT_MS"] = 1000.0
    forge(case, widen)
    fails(case, "more than 30 ms")
    assert vmp.MAX_PHASE_SHIFT_MS == 30.0


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
