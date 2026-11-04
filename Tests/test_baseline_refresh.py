"""Tests for Tests/baseline_refresh.py: the hardened `--refresh` of the alignment baseline.

Master Board card 228 (the regression net had become a drift detector, not a
control). SYNTHETIC DATA ONLY: every baseline here is built in tmp_path; the real
`baseline_alignments.json` and the 14.08.26 corpus are never read or written (a test
at the end proves the real file's bytes did not move).

Two kinds of test:
  * in-process tests call the pure functions and `run_refresh_cli` directly;
  * "cli_" tests copy the REAL Tests/test_alignment_baseline.py into a scratch tree
    with a tiny fake corpus and a fake align_engine, then run
    `python <scratch>/Tests/test_alignment_baseline.py --refresh ...` as a subprocess.
    They exercise the real thin `--refresh` branch end to end, and they FAIL against
    the pre-hardening test file (which ignores --reason, writes no record, has no
    guard), because the scratch copy comes from the sibling file of this test.
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import baseline_refresh as BR

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
REAL_BASELINE = (ROOT / "Documentation" / "Plans" / "arranger-signal-rewiring"
                 / "baseline_alignments.json")
NAMED, STANDARD = BR.POLICY_NAMED, BR.POLICY_STANDARD
FIXED_NOW = datetime.datetime(2026, 11, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)
FAKE_COMMIT = "a" * 40
GOOD_REASON = "accepted: the new cue labels move these pairs"


def _sha_or_missing(path):
    return BR.sha256_file(path) if path.is_file() else "missing"


def _dir_listing(path):
    return sorted(p.name for p in path.iterdir()) if path.is_dir() else []


_REAL_SHA_AT_IMPORT = _sha_or_missing(REAL_BASELINE)
_REAL_DIR_AT_IMPORT = _dir_listing(REAL_BASELINE.parent)


@pytest.fixture(scope="module", autouse=True)
def _real_baseline_is_never_touched():
    yield
    assert _sha_or_missing(REAL_BASELINE) == _REAL_SHA_AT_IMPORT, (
        "the REAL baseline_alignments.json changed during this test run")
    assert _dir_listing(REAL_BASELINE.parent) == _REAL_DIR_AT_IMPORT, (
        "files appeared or vanished next to the REAL baseline during this test run")


# ---------------------------------------------------------------------------
# Synthetic baselines
# ---------------------------------------------------------------------------
def _ok_row(out, inn, policy=STANDARD, **over):
    row = {"out": out, "in": inn, "status": "ok", "handoff_bar_out": 64.0,
           "arr_offset_bars": 32.0, "overlap_bars": 16.0, "swap_progress": 0.5,
           "handoff_kind": "k", "alignment_policy": "paired_landmarks_v2",
           "n_paired_cues": 1, "paired_cue_bars": [64], "anchor_bar_in": 8.0,
           "score": 3, "paired_cues": [{"arrangement_bar": 64}], "notes": ["n"],
           "plan": {"status": "ok", "specs": []}, "overlap_policy": policy}
    row.update(over)
    return row


def _raise_row(out, inn, error="ValueError", msg="no paired alignment"):
    return {"out": out, "in": inn, "status": "raise", "error": error, "msg": msg}


def _payload(rows):
    ok = sum(1 for r in rows if r["status"] == "ok")
    names = {r["out"] for r in rows} | {r["in"] for r in rows}
    return {"corpus": "synthetic", "n_tracks": len(names), "n_pairs": len(rows),
            "ok": ok, "raised": len(rows) - ok, "rows": rows}


def _pairs(n_tracks):
    names = [f"T{i:02d}" for i in range(n_tracks)]
    return [(a, b) for a in names for b in names if a != b]


def _grid(pairs, named):
    """One OK row per ordered pair; the pairs in `named` carry named_landmark_64."""
    named = set(named)
    return _payload([_ok_row(a, b, NAMED if (a, b) in named else STANDARD)
                     for a, b in pairs])


class Env:
    """A tmp 'Plans' folder holding a synthetic baseline, and a way to run --refresh on it."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "plans"
        self.dir.mkdir()
        self.baseline = self.dir / "baseline_alignments.json"
        self.called = []

    def write_old(self, payload):
        self.baseline.write_bytes(BR._baseline_bytes(payload))
        return payload

    def files(self):
        return _dir_listing(self.dir)

    def sidecars(self):
        return sorted(self.dir.glob("baseline_alignments.refresh-*.json"))

    def run(self, argv, new_payload, inputs=(), root=None, clock=lambda: FIXED_NOW):
        def build():
            self.called.append(1)
            return new_payload
        return BR.run_refresh_cli(argv, self.baseline, build, inputs, root=root,
                                  clock=clock, head=lambda cwd: FAKE_COMMIT)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def _messages(capsys):
    got = capsys.readouterr()
    return [ln for ln in (got.err + got.out).splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# --reason is required
# ---------------------------------------------------------------------------
def test_refresh_without_reason_exits_nonzero_and_writes_nothing(env, capsys):
    old = env.write_old(_grid(_pairs(5), []))
    before_bytes, before_files = env.baseline.read_bytes(), env.files()
    rc = env.run(["--refresh"], _grid(_pairs(5), [("T00", "T01")]))
    assert rc != 0
    assert env.called == [], "the slow regeneration must not even start without a reason"
    assert env.baseline.read_bytes() == before_bytes
    assert env.files() == before_files
    lines = _messages(capsys)
    assert len(lines) == 1 and "--reason" in lines[0]
    assert json.loads(env.baseline.read_bytes()) == old


@pytest.mark.parametrize("reason", [
    "", "   ", "too short", "a b c d e f g h i j k l m n",  # 14 letters
    "." * 15,                                               # 15 non-space, none alphanumeric
    chr(0x200b) * 15,                                       # 15 zero-width spaces
    "a" * 14 + "-" * 40,                                    # 14 letters among punctuation
])
def test_refresh_with_too_short_reason_exits_nonzero_and_writes_nothing(env, capsys, reason):
    env.write_old(_grid(_pairs(5), []))
    before_bytes, before_files = env.baseline.read_bytes(), env.files()
    rc = env.run(["--refresh", "--reason", reason], _grid(_pairs(5), [("T00", "T01")]))
    assert rc != 0
    assert env.called == []
    assert env.baseline.read_bytes() == before_bytes
    assert env.files() == before_files
    assert len(_messages(capsys)) == 1


def test_reason_of_exactly_15_letters_or_digits_is_enough():
    assert BR.validate_reason("abcde fghij klmno") == "abcde fghij klmno"
    with pytest.raises(BR.RefreshRefused):
        BR.validate_reason("abcde fghij klmn")


def test_reason_counts_letters_and_digits_only():
    assert BR.validate_reason("ab12 cd34, ef56 - gh7") == "ab12 cd34, ef56 - gh7"   # 15 alnum
    assert BR.validate_reason("caf" + chr(0xe9) + " " + "x" * 11)                  # unicode letters count
    for junk in ("." * 15, chr(0x200b) * 15, "!?" * 30, "a" * 14 + "." * 30):
        with pytest.raises(BR.RefreshRefused):
            BR.validate_reason(junk)


@pytest.mark.parametrize("flag,other", [
    ("--reason", ["--reviewer", "Sam"]),
    ("--reviewer", ["--reason", GOOD_REASON]),
])
def test_a_repeated_reason_or_reviewer_is_refused_before_anything_is_computed(
        env, capsys, flag, other):
    env.write_old(_grid(_pairs(5), []))
    before_bytes, before_files = env.baseline.read_bytes(), env.files()
    first = GOOD_REASON if flag == "--reason" else "Sam"
    argv = ["--refresh", flag, first, *other, flag, "a second value that would win silently"]
    rc = env.run(argv, _grid(_pairs(5), [("T00", "T01")]))
    assert rc == 2
    assert env.called == [], "the slow regeneration must not start"
    assert env.baseline.read_bytes() == before_bytes and env.files() == before_files
    lines = _messages(capsys)
    assert len(lines) == 1 and flag in lines[0] and "more than once" in lines[0]


def test_the_equals_form_counts_as_a_repeat_too(env):
    env.write_old(_grid(_pairs(5), []))
    rc = env.run(["--refresh", f"--reason={GOOD_REASON}", "--reason", "another long reason here"],
                 _grid(_pairs(5), []))
    assert rc == 2 and env.called == []


def test_unknown_or_abbreviated_flags_are_refused(env, capsys):
    env.write_old(_grid(_pairs(5), []))
    before = env.files()
    rc = env.run(["--refresh", "--reason", GOOD_REASON, "--accept"], _grid(_pairs(5), []))
    assert rc != 0 and env.files() == before and env.called == []
    assert len(_messages(capsys)) == 1


# ---------------------------------------------------------------------------
# The sidecar and the atomic baseline write
# ---------------------------------------------------------------------------
def test_refresh_with_reason_writes_sidecar_with_every_field(env, tmp_path, capsys):
    pairs = _pairs(6)
    old = env.write_old(_grid(pairs, pairs[:6]))
    old_bytes = env.baseline.read_bytes()
    new = _grid(pairs, pairs[:5])                       # 1 pair named -> standard
    stem = tmp_path / "stem.json"
    stem.write_bytes(b'{"bpm": 128}')
    inputs = [stem, tmp_path / "gone.json"]

    rc = env.run(["--refresh", "--reason", GOOD_REASON, "--reviewer", "Sam"], new,
                 inputs=inputs, root=tmp_path)
    assert rc == 0

    sidecars = env.sidecars()
    assert [p.name for p in sidecars] == ["baseline_alignments.refresh-20261104T120000Z.json"]
    raw = sidecars[0].read_bytes()
    assert all(b < 128 for b in raw), "the sidecar must be ASCII"
    rec = json.loads(raw)
    assert rec["reason"] == GOOD_REASON
    assert rec["reviewer"] == "Sam"
    assert rec["time_utc"] == "2026-11-04T12:00:00Z"
    assert rec["git_commit"] == FAKE_COMMIT
    assert rec["schema_marker"].startswith("derived:")
    assert rec["schema_detail"]["top_level_keys"] == sorted(new)
    assert rec["input_hashes"] == {"stem.json": hashlib.sha256(b'{"bpm": 128}').hexdigest(),
                                   "gone.json": "missing"}
    assert rec["old_baseline_sha256"] == hashlib.sha256(old_bytes).hexdigest()
    assert rec["new_baseline_sha256"] == BR.sha256_file(env.baseline)
    assert rec["diff_summary"] == BR.diff_summary(old, new)
    assert rec["policy_shift"] == BR.policy_shift(old, new)
    assert rec["policy_shift"]["named_landmark_64_to_standard_48"] == 1
    assert rec["accepted_shift"] is False and rec["guards_tripped"] == []
    # the baseline now holds the new payload, and nothing temporary is left behind
    assert json.loads(env.baseline.read_bytes()) == new
    assert env.files() == sorted(["baseline_alignments.json", sidecars[0].name])
    assert any("baseline refreshed" in ln for ln in _messages(capsys))


def test_sidecar_stays_ascii_for_a_reason_with_non_ascii_text(env):
    env.write_old(_grid(_pairs(4), []))
    reason = "caf" + chr(0xe9) + " mix " + chr(0x2013) + " accepted after review"
    assert env.run(["--refresh", "--reason", reason], _grid(_pairs(4), [])) == 0
    raw = env.sidecars()[0].read_bytes()
    assert all(b < 128 for b in raw)
    assert json.loads(raw)["reason"] == reason


def test_first_refresh_with_no_baseline_yet(env):
    new = _grid(_pairs(4), [])
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["old_baseline_sha256"] == "missing"
    assert rec["old_counts"] is None
    assert rec["diff_summary"]["pairs_added"] == len(new["rows"])
    assert json.loads(env.baseline.read_bytes()) == new


def test_a_corrupt_existing_baseline_is_refused_before_the_slow_step(env, capsys):
    env.baseline.write_bytes(b"{ not json")
    rc = env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(4), []))
    assert rc != 0 and env.called == []
    assert env.baseline.read_bytes() == b"{ not json"
    assert env.sidecars() == []
    assert len(_messages(capsys)) == 1


def test_failure_between_sidecar_write_and_replace_keeps_old_baseline(env, monkeypatch, capsys):
    env.write_old(_grid(_pairs(5), []))
    old_bytes = env.baseline.read_bytes()
    real_replace = os.replace

    def fake_replace(src, dst):
        if os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(env.baseline)):
            raise OSError("injected: disk went away")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fake_replace)
    rc = env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(5), [("T00", "T01")]))
    monkeypatch.undo()
    assert rc != 0
    assert env.baseline.read_bytes() == old_bytes, "the old baseline must survive a failed swap"
    assert env.sidecars() == [], "a record of a refresh that did not land must not look live"
    names = env.files()
    assert not any(n.endswith(".tmp") for n in names), names
    assert any(n.endswith(".json.not-applied") for n in names), names
    assert len(_messages(capsys)) == 1


def test_failure_while_staging_the_baseline_keeps_old_baseline(env, monkeypatch):
    env.write_old(_grid(_pairs(5), []))
    old_bytes = env.baseline.read_bytes()

    def boom(fd):
        raise OSError("injected: fsync failed")

    monkeypatch.setattr(os, "fsync", boom)
    rc = env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(5), [("T00", "T01")]))
    monkeypatch.undo()
    assert rc != 0
    assert env.baseline.read_bytes() == old_bytes
    assert env.files() == ["baseline_alignments.json"]


def test_sidecar_exists_before_the_baseline_is_replaced(env, monkeypatch):
    env.write_old(_grid(_pairs(5), []))
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        if os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(env.baseline)):
            seen["sidecars_at_swap"] = [p.name for p in env.sidecars()]
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(5), [("T00", "T01")])) == 0
    monkeypatch.undo()
    assert len(seen["sidecars_at_swap"]) == 1, (
        "the refresh record must already be on disk when the baseline moves")


def test_baseline_is_written_to_a_temp_file_then_swapped_in(env, monkeypatch):
    env.write_old(_grid(_pairs(5), []))
    old_bytes = env.baseline.read_bytes()
    new = _grid(_pairs(5), [("T00", "T01")])
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        if os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(env.baseline)):
            seen["src"] = Path(src)
            seen["baseline_at_swap"] = env.baseline.read_bytes()
            seen["staged_bytes"] = Path(src).read_bytes()
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0
    monkeypatch.undo()
    assert seen, "the baseline must be put in place with os.replace"
    assert seen["src"] != env.baseline and seen["src"].parent == env.baseline.parent
    assert seen["baseline_at_swap"] == old_bytes, "the live file is untouched until the swap"
    assert seen["staged_bytes"] == env.baseline.read_bytes()


def test_two_refreshes_in_the_same_second_keep_both_records(env):
    env.write_old(_grid(_pairs(5), []))
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(5), [("T00", "T01")])) == 0
    first = env.sidecars()
    assert len(first) == 1
    first_bytes = first[0].read_bytes()
    assert env.run(["--refresh", "--reason", "second look, same second, kept"],
                   _grid(_pairs(5), [("T00", "T01"), ("T01", "T02")])) == 0
    after = env.sidecars()
    assert [p.name for p in after] == ["baseline_alignments.refresh-20261104T120000Z-2.json",
                                       "baseline_alignments.refresh-20261104T120000Z.json"]
    assert first[0].read_bytes() == first_bytes, "the first record was overwritten"
    assert json.loads(after[0].read_bytes())["reason"] == "second look, same second, kept"


KILL_AT_SWAP = '''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import baseline_refresh as BR
baseline = os.path.abspath(sys.argv[2])
real_replace = os.replace


def kill_at_the_swap(src, dst):
    if os.path.abspath(dst) == baseline:
        os._exit(9)                      # a hard kill: no except, no finally, no cleanup
    return real_replace(src, dst)


os.replace = kill_at_the_swap
with open(sys.argv[3], encoding="ascii") as fh:
    payload = json.load(fh)
BR.refresh_baseline(baseline, payload, reason="killed on purpose just before the swap",
                    head=lambda cwd: "b" * 40)
'''


def _hard_kill_a_refresh(env, new_payload, tmp_path):
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps(new_payload), encoding="ascii")
    child_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-c", KILL_AT_SWAP, str(TESTS_DIR),
                           str(env.baseline), str(payload_file)],
                          env=child_env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 9, done.stderr + done.stdout


def _age(path, seconds=3600):
    stamp = path.stat().st_mtime - seconds
    os.utime(path, (stamp, stamp))


def test_a_hard_kill_before_the_swap_is_cleaned_up_by_the_next_refresh(env, tmp_path, capsys):
    pairs = _pairs(5)
    old = env.write_old(_grid(pairs, []))
    old_bytes = env.baseline.read_bytes()
    _hard_kill_a_refresh(env, _grid(pairs, [("T00", "T01")]), tmp_path)
    # the junk a kill leaves behind: a full-size temp copy and a record with a live name
    junk_tmp = [n for n in env.files() if n.endswith(".tmp")]
    live_sidecars = env.sidecars()
    assert len(junk_tmp) == 1 and len(live_sidecars) == 1, env.files()
    assert env.baseline.read_bytes() == old_bytes
    rec = json.loads(live_sidecars[0].read_bytes())
    assert rec["new_baseline_sha256"] != BR.sha256_file(env.baseline)
    assert rec["old_baseline_sha256"] == BR.sha256_file(env.baseline)
    for p in list(env.dir.iterdir()):
        if p != env.baseline:
            _age(p)

    new = _grid(pairs, [("T00", "T01"), ("T01", "T02")])
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0
    names = env.files()
    assert not any(n.endswith(".tmp") for n in names), names
    assert live_sidecars[0].name + ".not-applied" in names
    assert live_sidecars[0] not in env.sidecars()
    assert len(env.sidecars()) == 1                                  # only the refresh that landed
    assert json.loads(env.sidecars()[0].read_bytes())["new_baseline_sha256"] == BR.sha256_file(env.baseline)
    assert json.loads(env.baseline.read_bytes()) == new and old != new
    cleanup_lines = [ln for ln in _messages(capsys) if "interrupted" in ln]
    assert len(cleanup_lines) == 1, cleanup_lines


def test_stale_leftovers_are_cleaned_but_only_the_matching_ones(env):
    env.write_old(_grid(_pairs(4), []))
    stale = [env.dir / "baseline_alignments.json.tmp",              # the round-1 spelling
             env.dir / "baseline_alignments.json.4242.tmp",
             env.dir / "baseline_alignments.refresh-20260101T000000Z.json.4242.tmp"]
    keep = [env.dir / "notes.tmp", env.dir / "baseline_alignments.PRE-REBUILD.json",
            env.dir / "baseline_alignments.json.bak"]
    for p in stale + keep:
        p.write_bytes(b"x" * 10)
        _age(p)
    done = BR.cleanup_interrupted(env.baseline)
    assert sorted(done["removed"]) == sorted(p.name for p in stale)
    assert done["marked"] == []
    assert all(p.exists() for p in keep) and not any(p.exists() for p in stale)


def test_young_leftovers_are_left_alone_in_case_another_refresh_is_running(env, tmp_path):
    pairs = _pairs(5)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, [("T00", "T01")]), tmp_path)
    before = env.files()
    done = BR.cleanup_interrupted(env.baseline)                      # nothing is older than the limit
    assert done == {"removed": [], "marked": []} and env.files() == before


def test_a_sidecar_that_did_land_or_belongs_to_older_history_is_never_renamed(env):
    pairs = _pairs(5)
    env.write_old(_grid(pairs, []))
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(pairs, [("T00", "T01")])) == 0
    assert env.run(["--refresh", "--reason", "second one lands too"],
                   _grid(pairs, [("T00", "T01"), ("T01", "T02")])) == 0
    stray = env.dir / "baseline_alignments.refresh-20250101T000000Z.json"
    stray.write_text(json.dumps({"old_baseline_sha256": "x" * 64,
                                 "new_baseline_sha256": "y" * 64}), encoding="ascii")
    for p in env.sidecars():
        _age(p)
    before = env.files()
    assert BR.cleanup_interrupted(env.baseline) == {"removed": [], "marked": []}
    assert env.files() == before


def test_an_empty_record_left_by_a_kill_before_its_content_was_written_is_marked(env):
    env.write_old(_grid(_pairs(4), []))
    husk = env.dir / "baseline_alignments.refresh-20260101T000000Z.json"
    husk.write_bytes(b"")
    _age(husk)
    done = BR.cleanup_interrupted(env.baseline)
    assert done["marked"] == [husk.name] and not husk.exists()
    assert (env.dir / (husk.name + ".not-applied")).exists()


def test_the_temp_file_name_is_unique_to_the_process(env, monkeypatch):
    env.write_old(_grid(_pairs(5), []))
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(5), [("T00", "T01")])) == 0
    monkeypatch.undo()
    assert f"baseline_alignments.json.{os.getpid()}.tmp" in seen
    assert any(n.startswith("baseline_alignments.refresh-") and n.endswith(f".json.{os.getpid()}.tmp")
               for n in seen)


def test_cleanup_does_not_run_when_the_arguments_are_refused(env):
    env.write_old(_grid(_pairs(4), []))
    husk = env.dir / "baseline_alignments.refresh-20260101T000000Z.json"
    husk.write_bytes(b"")
    _age(husk)
    assert env.run(["--refresh"], _grid(_pairs(4), [])) == 2
    assert husk.exists(), "a refused command must not touch any file"


# ---------------------------------------------------------------------------
# The large-shift guard
# ---------------------------------------------------------------------------
def test_a_50_pair_shift_to_standard_is_refused_without_accept_shift(env, capsys):
    pairs = _pairs(11)                                           # 110 pairs
    old = env.write_old(_grid(pairs, pairs[:60]))
    before_bytes, before_files = env.baseline.read_bytes(), env.files()
    new = _grid(pairs, pairs[:10])                               # 50 pairs named -> standard
    assert BR.policy_shift(old, new)["named_landmark_64_to_standard_48"] == 50
    rc = env.run(["--refresh", "--reason", GOOD_REASON], new)
    assert rc != 0
    assert env.baseline.read_bytes() == before_bytes and env.files() == before_files
    lines = _messages(capsys)
    assert len(lines) == 1 and "--accept-shift" in lines[0]


def test_a_50_pair_shift_is_accepted_with_accept_shift_and_the_sidecar_says_so(env):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, pairs[:60]))
    new = _grid(pairs, pairs[:10])
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is True and rec["accept_shift_flag"] is True
    assert len(rec["guards_tripped"]) >= 1
    assert rec["policy_shift"]["named_landmark_64_to_standard_48"] == 50
    assert json.loads(env.baseline.read_bytes()) == new


def test_the_reverse_shift_standard_to_named_is_refused_too(env):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, pairs[:10]))
    rc = env.run(["--refresh", "--reason", GOOD_REASON], _grid(pairs, pairs[:60]))
    assert rc != 0 and env.sidecars() == []


def test_a_3_pair_shift_passes_without_accept_shift(env):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, pairs[:60]))
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(pairs, pairs[:57])) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is False and rec["guards_tripped"] == []
    assert rec["policy_shift"]["total_moved"] == 3


def test_movement_in_both_directions_is_added_together(env):
    pairs = _pairs(11)
    old_named = set(pairs[:6]) | set(pairs[20:26])               # 12 named in the old baseline
    env.write_old(_grid(pairs, old_named))
    # 6 move to standard, 6 other pairs move to named: no single direction reaches 10
    new_named = (old_named - set(pairs[:6])) | set(pairs[40:46])
    new = _grid(pairs, new_named)
    shift = BR.policy_shift(_grid(pairs, old_named), new)
    assert shift["named_landmark_64_to_standard_48"] == 6
    assert shift["standard_48_to_named_landmark_64"] == 6
    rc = env.run(["--refresh", "--reason", GOOD_REASON], new)
    assert rc != 0 and env.sidecars() == []


def test_pairs_removed_above_5_percent_are_refused_unless_accepted(env):
    pairs = _pairs(11)                                           # 110 pairs, 5% = 5.5
    env.write_old(_grid(pairs, []))
    ok_new = _grid(pairs[:-5], [])                               # 5 removed = 4.5 percent
    assert env.run(["--refresh", "--reason", GOOD_REASON], ok_new) == 0
    env.baseline.write_bytes(BR._baseline_bytes(_grid(pairs, [])))
    before_bytes, before_sidecars = env.baseline.read_bytes(), env.sidecars()
    bad_new = _grid(pairs[:-6], [])                              # 6 removed = 5.45 percent
    assert env.run(["--refresh", "--reason", GOOD_REASON], bad_new) != 0
    assert env.baseline.read_bytes() == before_bytes and env.sidecars() == before_sidecars
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], bad_new) == 0
    recs = [json.loads(p.read_bytes()) for p in env.sidecars()]
    rec = next(r for r in recs if r["diff_summary"]["pairs_removed"] == 6)
    assert rec["accepted_shift"] is True
    assert len(recs) == 2 and sum(r["accepted_shift"] for r in recs) == 1


@pytest.mark.parametrize("moved,compared,tripped", [
    (9, 1000, False),     # under the pair count, 0.9 percent
    (10, 1000, True),     # the pair count is reached
    (5, 50, False),       # exactly 10 percent is not "more than 10 percent"
    (6, 50, True),        # 12 percent
    (0, 0, False),
])
def test_shift_guard_edges(moved, compared, tripped):
    shift = {"total_moved": moved, "pairs_compared": compared}
    summary = {"pairs_old": compared, "pairs_removed": 0}
    assert bool(BR.guard_trips(shift, summary)) is tripped


@pytest.mark.parametrize("removed,old_pairs,tripped", [
    (5, 100, False),      # exactly 5 percent is not "more than 5 percent"
    (6, 100, True),
    (0, 0, False),
])
def test_removed_pairs_guard_edges(removed, old_pairs, tripped):
    shift = {"total_moved": 0, "pairs_compared": old_pairs}
    summary = {"pairs_old": old_pairs, "pairs_removed": removed}
    assert bool(BR.guard_trips(shift, summary)) is tripped


def _guard_names(trips):
    return [t.split(":")[0] for t in trips]


def test_all_ok_pairs_flipping_to_raise_is_refused_then_accepted_and_recorded(env, capsys):
    pairs = _pairs(11)                                           # 110 pairs
    old = env.write_old(_grid(pairs, pairs[:60]))
    new = _payload([_raise_row(a, b) for a, b in pairs])         # every pair now raises
    assert BR.policy_shift(old, new)["total_moved"] == 0         # the named-policy guard is blind to this
    before_bytes, before_files = env.baseline.read_bytes(), env.files()
    rc = env.run(["--refresh", "--reason", GOOD_REASON], new)
    assert rc != 0
    assert env.baseline.read_bytes() == before_bytes and env.files() == before_files
    lines = _messages(capsys)
    assert len(lines) == 1 and "status_flips" in lines[0] and "--accept-shift" in lines[0]
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is True
    assert "status_flips" in rec["guards_tripped_names"]
    assert rec["diff_summary"]["changed_by_field"]["status_flip"] == 110
    assert json.loads(env.baseline.read_bytes()) == new


def test_overlap_policy_changes_of_any_kind_are_refused_then_accepted_and_recorded(env, capsys):
    pairs = _pairs(11)
    old = env.write_old(_grid(pairs, pairs[:60]))                # 60 named
    rows = []
    for idx, (a, b) in enumerate(pairs):
        policy = None if idx < 30 else ("short_32" if idx < 60 else STANDARD)
        rows.append(_ok_row(a, b, policy))
    new = _payload(rows)                                         # 30 named->None, 30 named->short_32
    assert BR.policy_shift(old, new)["total_moved"] == 0
    before_bytes = env.baseline.read_bytes()
    rc = env.run(["--refresh", "--reason", GOOD_REASON], new)
    assert rc != 0 and env.baseline.read_bytes() == before_bytes and env.sidecars() == []
    lines = _messages(capsys)
    assert len(lines) == 1 and "overlap_policy_changes" in lines[0]
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is True
    assert rec["guards_tripped_names"] == ["overlap_policy_changes"]
    assert rec["diff_summary"]["changed_by_field"]["overlap_policy"] == 60


def test_a_3_pair_status_flip_passes(env):
    pairs = _pairs(11)
    old = env.write_old(_grid(pairs, pairs[:60]))
    new = _payload([_raise_row(a, b) if (a, b) in pairs[60:63] else row
                    for (a, b), row in zip(pairs, old["rows"])])
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is False and rec["guards_tripped"] == []
    assert rec["diff_summary"]["changed_by_field"]["status_flip"] == 3


@pytest.mark.parametrize("field,guard,other", [
    ("status_flip", "status_flips", "overlap_policy_changes"),
    ("overlap_policy", "overlap_policy_changes", "status_flips"),
])
@pytest.mark.parametrize("count,compared,tripped", [
    (9, 1000, False),     # under the pair count
    (10, 1000, True),     # the pair count is reached
    (5, 50, False),       # exactly 10 percent is not "more than 10 percent"
    (6, 50, True),        # 12 percent
    (0, 0, False),
])
def test_status_and_overlap_guard_edges(field, guard, other, count, compared, tripped):
    shift = {"total_moved": 0, "pairs_compared": compared}
    summary = {"pairs_old": compared, "pairs_removed": 0,
               "changed_by_field": {field: count}}
    names = _guard_names(BR.guard_trips(shift, summary))
    assert (guard in names) is tripped
    assert other not in names and "policy_shift" not in names and "pairs_removed" not in names


def test_each_guard_is_reported_under_its_own_name():
    shift = {"total_moved": 20, "pairs_compared": 100}
    summary = {"pairs_old": 100, "pairs_removed": 10,
               "changed_by_field": {"status_flip": 20, "overlap_policy": 20}}
    assert _guard_names(BR.guard_trips(shift, summary)) == [
        "policy_shift", "status_flips", "overlap_policy_changes", "pairs_removed"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_diff_summary_counts_exactly_on_a_hand_made_pair_of_baselines():
    old = _payload([
        _ok_row("a", "b"),
        _ok_row("a", "c"),
        _ok_row("a", "d"),
        _raise_row("a", "e", "ValueError"),
        _ok_row("a", "f"),
        _ok_row("a", "g"),                                       # removed
    ])
    spec = {"status": "ok", "specs": [{"kind": "outgoing_tail"}]}
    new = _payload([
        _ok_row("a", "b"),                                       # unchanged
        _ok_row("a", "c", alignment_policy="tail_anchor_rescue_v1"),
        _ok_row("a", "d", NAMED, plan=spec),
        _raise_row("a", "e", "KeyError"),
        _ok_row("a", "f", swap_progress=0.75, handoff_kind="k2"),
        _ok_row("a", "h"),                                       # added
    ])
    s = BR.diff_summary(old, new)
    assert (s["pairs_old"], s["pairs_new"]) == (6, 6)
    assert (s["pairs_added"], s["pairs_removed"], s["pairs_changed"], s["pairs_unchanged"]) == (1, 1, 4, 1)
    assert s["changed_by_field"] == {
        "alignment_policy": 1, "overlap_policy": 1, "swap_handoff": 1,
        "spec": 1, "raise_type": 1, "status_flip": 0, "other": 0}
    assert s["first_added"] == ["a -> h"] and s["first_removed"] == ["a -> g"]
    assert [c["id"] for c in s["first_changed"]] == ["a -> c", "a -> d", "a -> e", "a -> f"]
    by_id = {c["id"]: c["changes"] for c in s["first_changed"]}
    assert by_id["a -> c"] == {"alignment_policy": {"old": "paired_landmarks_v2",
                                                    "new": "tail_anchor_rescue_v1"}}
    assert by_id["a -> d"] == {"overlap_policy": {"old": STANDARD, "new": NAMED},
                               "plan": {"old": {"status": "ok", "specs": []}, "new": spec}}
    assert by_id["a -> e"] == {"error": {"old": "ValueError", "new": "KeyError"}}
    assert by_id["a -> f"] == {"handoff_kind": {"old": "k", "new": "k2"},
                               "swap_progress": {"old": 0.5, "new": 0.75}}


def test_diff_summary_lists_only_the_first_20_changed_pairs():
    pairs = [("a", f"p{i:02d}") for i in range(25)]
    old = _payload([_ok_row(a, b) for a, b in pairs])
    new = _payload([_ok_row(a, b, score=99) for a, b in pairs])
    s = BR.diff_summary(old, new)
    assert s["pairs_changed"] == 25 and s["changed_by_field"]["other"] == 25
    assert len(s["first_changed"]) == 20
    assert [c["id"] for c in s["first_changed"]] == [f"a -> p{i:02d}" for i in range(20)]


def test_diff_summary_counts_a_status_flip_and_identical_baselines():
    old = _payload([_ok_row("a", "b"), _ok_row("a", "c")])
    assert BR.diff_summary(old, old)["pairs_changed"] == 0
    new = _payload([_raise_row("a", "b"), _ok_row("a", "c")])
    s = BR.diff_summary(old, new)
    assert s["pairs_changed"] == 1 and s["changed_by_field"]["status_flip"] == 1
    assert s["changed_by_field"]["raise_type"] == 0


def test_policy_shift_counts_both_directions_and_ignores_added_and_raising_pairs():
    old = _payload([_ok_row("a", "b", NAMED), _ok_row("a", "c", NAMED),
                    _ok_row("a", "d", STANDARD), _raise_row("a", "e"),
                    _ok_row("a", "f", NAMED)])
    new = _payload([_ok_row("a", "b", STANDARD), _ok_row("a", "c", STANDARD),
                    _ok_row("a", "d", NAMED), _ok_row("a", "e", NAMED),
                    _ok_row("a", "f", NAMED), _ok_row("a", "g", NAMED)])
    shift = BR.policy_shift(old, new)
    assert shift == {"named_landmark_64_to_standard_48": 2,
                     "standard_48_to_named_landmark_64": 1,
                     "total_moved": 3, "pairs_compared": 5}


def test_input_hashes_records_missing_for_a_path_that_does_not_exist(tmp_path):
    real = tmp_path / "stems" / "a.json"
    real.parent.mkdir()
    real.write_bytes(b"abc")
    outside = tmp_path.parent / "elsewhere.json"
    got = BR.input_hashes([real, tmp_path / "stems" / "nope.json", tmp_path / "stems",
                           outside], root=tmp_path)
    assert got["stems/a.json"] == hashlib.sha256(b"abc").hexdigest()
    assert got["stems/nope.json"] == "missing"
    assert got["stems"] == "missing", "a directory is not an input file"
    assert got[outside.as_posix()] == "missing"
    assert list(got) == sorted(got)


def test_generator_inputs_lists_stems_overrides_and_code(tmp_path):
    stem_dir = tmp_path / "Test Project" / "14.08.26" / "_Stem Analysis"
    stem_dir.mkdir(parents=True)
    (stem_dir / "SECTIONS_STEM_b.json").write_bytes(b"{}")
    (stem_dir / "SECTIONS_STEM_a.json").write_bytes(b"{}")
    (stem_dir / "unrelated.json").write_bytes(b"{}")
    paths = BR.generator_inputs(tmp_path, stem_dir)
    hashes = BR.input_hashes(paths, root=tmp_path)
    stem_keys = [k for k in hashes if "SECTIONS_STEM_" in k]
    assert stem_keys == ["Test Project/14.08.26/_Stem Analysis/SECTIONS_STEM_a.json",
                         "Test Project/14.08.26/_Stem Analysis/SECTIONS_STEM_b.json"]
    assert "Test Project/14.08.26/_Stem Analysis/loop_quality_overrides.json" in hashes
    assert all(rel in hashes for rel in BR.CODE_INPUTS)
    assert not any("unrelated" in k for k in hashes)


def _stem(stem_dir, file_key, track=None):
    (stem_dir / f"SECTIONS_STEM_{file_key}.json").write_text(
        json.dumps({"track": track or file_key, "bpm": 128}), encoding="ascii")


def test_generator_inputs_include_each_tracks_stem_envelope_cache(tmp_path):
    stem_dir = tmp_path / "Test Project" / "14.08.26" / "_Stem Analysis"
    stem_dir.mkdir(parents=True)
    _stem(stem_dir, "Alpha")
    _stem(stem_dir, "Beta")
    _stem(stem_dir, "x", track="Real Name")        # file name and track field disagree
    (stem_dir / "Alpha__stemenv.npz").write_bytes(b"npz-alpha")
    (stem_dir / "Real Name__stemenv.npz").write_bytes(b"npz-real")
    (stem_dir / "x__stemenv.npz").write_bytes(b"decoy named after the file, not the track")
    hashes = BR.input_hashes(BR.generator_inputs(tmp_path, stem_dir), root=tmp_path)
    base = "Test Project/14.08.26/_Stem Analysis/"
    assert hashes[base + "Alpha__stemenv.npz"] == hashlib.sha256(b"npz-alpha").hexdigest()
    assert hashes[base + "Beta__stemenv.npz"] == "missing"
    # align_engine.load_track names the cache from the JSON's `track` field
    assert hashes[base + "Real Name__stemenv.npz"] == hashlib.sha256(b"npz-real").hexdigest()
    assert base + "x__stemenv.npz" not in hashes
    # load_loop_quality_context reads SECTIONS_STEM_<track>.json beside the cache
    assert hashes[base + "SECTIONS_STEM_Real Name.json"] == "missing"
    assert hashes[base + "SECTIONS_STEM_Alpha.json"] != "missing"


def test_generator_inputs_never_raise_on_an_unreadable_stem_json(tmp_path):
    stem_dir = tmp_path / "stems"
    stem_dir.mkdir()
    (stem_dir / "SECTIONS_STEM_bad.json").write_bytes(b"{ not json")
    (stem_dir / "SECTIONS_STEM_list.json").write_bytes(b"[1, 2]")
    hashes = BR.input_hashes(BR.generator_inputs(tmp_path, stem_dir), root=tmp_path)
    assert hashes["stems/bad__stemenv.npz"] == "missing"       # falls back to the file name
    assert hashes["stems/list__stemenv.npz"] == "missing"


def test_every_code_input_exists_in_the_real_tree():
    missing = [rel for rel in BR.CODE_INPUTS if not (ROOT / rel).is_file()]
    assert missing == [], f"baseline_refresh.CODE_INPUTS names files that moved: {missing}"


def test_git_head_is_a_sha_or_unknown_and_never_raises(monkeypatch):
    assert re.fullmatch(r"[0-9a-f]{40}|unknown", BR.git_head(TESTS_DIR))

    def no_git(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", no_git)
    assert BR.git_head(TESTS_DIR) == "unknown"

    def bad_repo(*a, **k):
        return subprocess.CompletedProcess(a, 128, stdout="", stderr="not a git repository")
    monkeypatch.setattr(subprocess, "run", bad_repo)
    assert BR.git_head(TESTS_DIR) == "unknown"


def test_the_guard_limits_are_named_constants_with_the_documented_values():
    assert BR.SHIFT_MAX_PAIRS == 10
    assert BR.SHIFT_MAX_FRACTION == 0.10
    assert BR.REMOVED_MAX_FRACTION == 0.05
    assert BR.STATUS_FLIP_MAX_PAIRS == 10 and BR.STATUS_FLIP_MAX_FRACTION == 0.10
    assert BR.OVERLAP_CHANGE_MAX_PAIRS == 10 and BR.OVERLAP_CHANGE_MAX_FRACTION == 0.10
    assert BR.MIN_REASON_CHARS == 15
    assert BR.STALE_AFTER_SECONDS == 120


# ---------------------------------------------------------------------------
# End to end: the REAL test_alignment_baseline.py --refresh branch, in a scratch tree
# ---------------------------------------------------------------------------
FAKE_ENGINE = '''
import dataclasses, json
from pathlib import Path

CONTROL = Path(__file__).with_name("fake_control.json")


def _control():
    return json.loads(CONTROL.read_text(encoding="utf-8"))


def load_track(path):
    return {"name": path.name.replace("SECTIONS_STEM_", "").replace(".json", "")}


class _Alignment:
    pass


def align_pair(o, i):
    if [o["name"], i["name"]] in _control()["raise"]:
        raise ValueError("fake: no paired alignment for " + o["name"])
    al = _Alignment()
    al.handoff_bar_out = 64.0
    al.arr_offset_bars = 32.0
    al.overlap_bars = 16.0
    al.swap_progress = 0.5
    al.handoff_kind = "fake"
    al.alignment_policy = "fake_v1"
    al.paired_cues = [{"arrangement_bar": 64}]
    al.anchor_bar_in = 8.0
    al.score = 3
    al.notes = ["fake"]
    return al


@dataclasses.dataclass
class Spec:
    kind: str
    reps: int = 0
    partial_bars: float = 0.0
    target_marker_name: str = ""


def plan_fill_or_cut(o, i, al):
    if [o["name"], i["name"]] in _control()["named"]:
        return [Spec("outgoing_tail", reps=1, target_marker_name="landmark:x")]
    return [Spec("incoming_intro", reps=0)]
'''


class Scratch:
    """A throwaway copy of the project layout around the real test_alignment_baseline.py."""

    def __init__(self, tmp_path, n_tracks=11):
        self.root = tmp_path / "scratch"
        self.names = [f"T{i:02d}" for i in range(n_tracks)]
        tests = self.root / "Tests"
        source = self.root / "Source"
        (source / "automated_dj_mixes").mkdir(parents=True)
        tests.mkdir(parents=True)
        for name in ("test_alignment_baseline.py", "baseline_refresh.py"):
            (tests / name).write_bytes((TESTS_DIR / name).read_bytes())
        (source / "align_engine.py").write_text(FAKE_ENGINE, encoding="ascii")
        for rel in ("stem_section_probe.py", "automated_dj_mixes/__init__.py",
                    "automated_dj_mixes/transition_policy.py"):
            (source / rel).write_text("# stub\n", encoding="ascii")
        stems = self.root / "Test Project" / "14.08.26" / "_Stem Analysis"
        stems.mkdir(parents=True)
        self.stems = stems
        for n in self.names:
            (stems / f"SECTIONS_STEM_{n}.json").write_text(
                json.dumps({"track": n, "bpm": 128}), encoding="ascii")
            (stems / f"{n}__stemenv.npz").write_bytes(b"npz " + n.encode("ascii"))
        self.plans = self.root / "Documentation" / "Plans" / "arranger-signal-rewiring"
        self.plans.mkdir(parents=True)
        self.baseline = self.plans / "baseline_alignments.json"
        self.script = tests / "test_alignment_baseline.py"
        self.control(named=[], raising=[])

    def pairs(self):
        return [(a, b) for a in self.names for b in self.names if a != b]

    def control(self, named, raising):
        (self.root / "Source" / "fake_control.json").write_text(
            json.dumps({"named": [list(p) for p in named],
                        "raise": [list(p) for p in raising]}), encoding="ascii")

    def refresh(self, *args):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(self.root / "Source")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run([sys.executable, str(self.script), "--refresh", *args],
                              cwd=str(self.root), env=env, capture_output=True, text=True,
                              timeout=120)

    def tree(self):
        return {p.name: BR.sha256_file(p) for p in self.plans.iterdir() if p.is_file()}

    def sidecars(self):
        return sorted(self.plans.glob("baseline_alignments.refresh-*.json"))


@pytest.fixture
def scratch(tmp_path):
    s = Scratch(tmp_path)
    s.control(named=s.pairs()[:60], raising=[])
    done = s.refresh("--reason", "bootstrap the synthetic baseline")
    assert done.returncode == 0, done.stderr + done.stdout
    return s


def test_cli_refresh_without_reason_exits_nonzero_and_writes_nothing(scratch):
    scratch.control(named=scratch.pairs()[:57], raising=[])      # the code WOULD change rows
    before = scratch.tree()
    done = scratch.refresh()
    assert done.returncode != 0
    assert scratch.tree() == before
    lines = [ln for ln in (done.stderr + done.stdout).splitlines() if ln.strip()]
    assert len(lines) == 1 and "--reason" in lines[0], lines


def test_cli_refresh_with_a_too_short_reason_exits_nonzero_and_writes_nothing(scratch):
    scratch.control(named=scratch.pairs()[:57], raising=[])
    before = scratch.tree()
    done = scratch.refresh("--reason", "fix")
    assert done.returncode != 0
    assert scratch.tree() == before


def test_cli_refresh_with_reason_writes_a_sidecar_and_replaces_the_baseline(scratch):
    scratch.control(named=scratch.pairs()[:57], raising=[(scratch.names[0], scratch.names[1])])
    old_bytes = scratch.baseline.read_bytes()
    before = set(scratch.sidecars())
    done = scratch.refresh("--reason", "three pairs lose their landmark and one now raises",
                           "--reviewer", "Sam")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "baseline refreshed: 110 pairs" in done.stdout
    new_sidecars = [p for p in scratch.sidecars() if p not in before]
    assert len(new_sidecars) == 1
    rec = json.loads(new_sidecars[0].read_bytes())
    assert rec["reason"] == "three pairs lose their landmark and one now raises"
    assert rec["reviewer"] == "Sam"
    assert rec["old_baseline_sha256"] == hashlib.sha256(old_bytes).hexdigest()
    assert rec["new_baseline_sha256"] == BR.sha256_file(scratch.baseline)
    assert rec["new_baseline_sha256"] != rec["old_baseline_sha256"]
    hashes = rec["input_hashes"]
    assert set(BR.CODE_INPUTS) <= set(hashes) and all(hashes[r] != "missing" for r in BR.CODE_INPUTS)
    assert len([k for k in hashes if "SECTIONS_STEM_" in k]) == 11
    assert hashes["Test Project/14.08.26/_Stem Analysis/loop_quality_overrides.json"] == "missing"
    assert rec["diff_summary"]["pairs_changed"] == 4
    assert rec["policy_shift"]["named_landmark_64_to_standard_48"] == 3
    assert rec["accepted_shift"] is False
    assert not any(n.endswith(".tmp") for n in scratch.tree())
    assert json.loads(scratch.baseline.read_bytes())["raised"] == 1


def test_cli_a_50_pair_shift_is_refused_then_accepted_with_the_flag(scratch):
    scratch.control(named=scratch.pairs()[:10], raising=[])
    before = scratch.tree()
    refused = scratch.refresh("--reason", "trying a sweeping change without the flag")
    assert refused.returncode != 0
    assert scratch.tree() == before
    assert "--accept-shift" in (refused.stderr + refused.stdout)
    accepted = scratch.refresh("--reason", "sweeping change read through and accepted",
                               "--accept-shift")
    assert accepted.returncode == 0, accepted.stderr + accepted.stdout
    newest = [p for p in scratch.sidecars()
              if json.loads(p.read_bytes())["accepted_shift"]]
    assert len(newest) == 1


def test_cli_a_3_pair_shift_needs_no_flag(scratch):
    scratch.control(named=scratch.pairs()[:57], raising=[])
    done = scratch.refresh("--reason", "three pairs moved, nothing sweeping")
    assert done.returncode == 0, done.stderr + done.stdout


def test_cli_removing_a_stem_cache_after_a_refresh_changes_the_recorded_hashes(scratch):
    npz_label = "Test Project/14.08.26/_Stem Analysis/T00__stemenv.npz"
    first = scratch.sidecars()
    assert len(first) == 1                                   # the fixture's bootstrap refresh
    h1 = json.loads(first[0].read_bytes())["input_hashes"]
    assert h1[npz_label] == hashlib.sha256(b"npz T00").hexdigest()
    assert sum(1 for k in h1 if k.endswith("__stemenv.npz")) == 11
    (scratch.stems / "T00__stemenv.npz").unlink()
    done = scratch.refresh("--reason", "the T00 envelope cache was deleted, re-pin the inputs")
    assert done.returncode == 0, done.stderr + done.stdout
    second = [p for p in scratch.sidecars() if p not in first]
    assert len(second) == 1
    h2 = json.loads(second[0].read_bytes())["input_hashes"]
    assert h2[npz_label] == "missing"
    assert h2 != h1
    assert {k: v for k, v in h2.items() if k != npz_label} == {
        k: v for k, v in h1.items() if k != npz_label}


def test_cli_every_pair_raising_is_refused_then_accepted_with_the_flag(scratch):
    scratch.control(named=scratch.pairs()[:60], raising=scratch.pairs())
    before = scratch.tree()
    refused = scratch.refresh("--reason", "everything raises now, trying without the flag")
    assert refused.returncode != 0
    assert scratch.tree() == before
    assert "status_flips" in (refused.stderr + refused.stdout)
    accepted = scratch.refresh("--reason", "everything raises, read through and accepted",
                               "--accept-shift")
    assert accepted.returncode == 0, accepted.stderr + accepted.stdout
    recs = [json.loads(p.read_bytes()) for p in scratch.sidecars()]
    flagged = [r for r in recs if r["accepted_shift"]]
    assert len(flagged) == 1 and "status_flips" in flagged[0]["guards_tripped_names"]


def test_cli_dropping_a_track_removes_more_than_5_percent_and_is_refused(scratch):
    before = scratch.tree()
    gone = scratch.names[-1]
    (scratch.root / "Test Project" / "14.08.26" / "_Stem Analysis"
     / f"SECTIONS_STEM_{gone}.json").unlink()
    refused = scratch.refresh("--reason", "a track left the corpus, read through")
    assert refused.returncode != 0
    assert scratch.tree() == before


def test_cli_an_empty_corpus_cannot_wipe_the_baseline_without_accept_shift(scratch):
    """A machine without the corpus regenerates zero rows; the old --refresh then
    overwrote the baseline with an empty one. The removed-pairs guard stops that."""
    before = scratch.tree()
    stems = scratch.root / "Test Project" / "14.08.26" / "_Stem Analysis"
    for f in stems.glob("SECTIONS_STEM_*.json"):
        f.unlink()
    refused = scratch.refresh("--reason", "the corpus folder is empty on this machine")
    assert refused.returncode != 0
    assert scratch.tree() == before
    assert json.loads(scratch.baseline.read_bytes())["n_pairs"] == 110


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_the_module_under_test_is_the_sibling_copy_in_this_folder():
    print("baseline_refresh imported from:", BR.__file__)
    assert Path(BR.__file__).resolve() == TESTS_DIR / "baseline_refresh.py"


def test_the_existing_baseline_tests_still_collect_with_the_same_names():
    import test_alignment_baseline as TAB
    names = sorted(n for n in dir(TAB) if n.startswith("test_"))
    assert names == ["test_alignment_decisions_match_baseline",
                     "test_baseline_is_self_consistent"]
    for helper in ("compute_rows", "_diff_rows", "_failure_report", "_normalize",
                   "_populate_ok", "PINNED", "RAISE_PINNED", "BASELINE", "STEM_DIR"):
        assert hasattr(TAB, helper), helper
    assert "--reason" in TAB.__doc__
    assert TAB.BASELINE == REAL_BASELINE


def test_the_failing_test_report_tells_the_reader_to_give_a_reason(tmp_path, monkeypatch):
    import test_alignment_baseline as TAB
    row = _ok_row("A", "B")
    baseline = tmp_path / "baseline_alignments.json"
    baseline.write_text(json.dumps(_payload([row])), encoding="ascii")
    monkeypatch.setattr(TAB, "BASELINE", baseline)
    monkeypatch.setattr(TAB, "compute_rows", lambda: [dict(row, score=99)])
    with pytest.raises(pytest.fail.Exception) as info:
        TAB.test_alignment_decisions_match_baseline()
    text = str(info.value)
    assert "--refresh" in text and "--reason" in text, text


def test_the_new_files_are_ascii_only():
    for path in (TESTS_DIR / "baseline_refresh.py", Path(__file__)):
        bad = [b for b in path.read_bytes() if b > 127]
        assert not bad, f"{path.name} has {len(bad)} non-ASCII bytes (no em dashes either)"


def test_the_real_baseline_bytes_are_unchanged_by_this_run():
    assert _sha_or_missing(REAL_BASELINE) == _REAL_SHA_AT_IMPORT
    assert _dir_listing(REAL_BASELINE.parent) == _REAL_DIR_AT_IMPORT
