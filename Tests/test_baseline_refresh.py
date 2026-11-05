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
import stat
import subprocess
import sys
import time
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


def _short_refusal(lines):
    """Check the shape of a refusal about the guards or --accept-shift and return its
    lines: the first is `refresh refused: <what is wrong>` of at most 70 characters,
    every other line is at most 70 characters unless it is only a command, and one line
    says nothing was written."""
    assert lines and lines[0].startswith("refresh refused: ") and len(lines[0]) <= 70, lines
    for ln in lines[1:]:
        assert ln.startswith("--accept-shift ") or len(ln) <= 70, ln
    assert "nothing was written" in lines, lines
    return lines


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
if len(sys.argv) > 4:
    os.getpid = lambda: int(sys.argv[4])   # a process id that the system has handed out before
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


def _hard_kill_a_refresh(env, new_payload, tmp_path, pid=None):
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps(new_payload), encoding="ascii")
    child_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    argv = [sys.executable, "-c", KILL_AT_SWAP, str(TESTS_DIR),
            str(env.baseline), str(payload_file)]
    if pid is not None:
        argv.append(str(pid))
    done = subprocess.run(argv, env=child_env, capture_output=True, text=True, timeout=120)
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


def test_the_temp_file_name_carries_the_process_id_and_a_random_token(env, monkeypatch):
    # (round 4: the name used to end in .<pid>.tmp only; process ids are reused, so a
    # random token was added and this test now pins the new shape)
    env.write_old(_grid(_pairs(5), []))
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(5), [("T00", "T01")])) == 0
    assert env.run(["--refresh", "--reason", "a second refresh, a different token"],
                   _grid(_pairs(5), [("T00", "T01"), ("T01", "T02")])) == 0
    monkeypatch.undo()
    pid = os.getpid()
    shape = re.compile(rf"^baseline_alignments\.json\.{pid}\.[0-9a-f]{{8}}\.tmp$")
    baseline_temps = [n for n in seen if shape.match(n)]
    assert len(baseline_temps) == 2 and baseline_temps[0] != baseline_temps[1], seen
    assert any(re.match(rf"^baseline_alignments\.refresh-\S+\.json\.{pid}\.[0-9a-f]{{8}}\.tmp$", n)
               for n in seen)
    recorded = [json.loads(p.read_bytes())["staged_temp"] for p in env.sidecars()]
    assert sorted(recorded) == sorted(baseline_temps), "the record must name the temp file it really used"


def test_cleanup_does_not_run_when_the_arguments_are_refused(env):
    env.write_old(_grid(_pairs(4), []))
    husk = env.dir / "baseline_alignments.refresh-20260101T000000Z.json"
    husk.write_bytes(b"")
    _age(husk)
    assert env.run(["--refresh"], _grid(_pairs(4), [])) == 2
    assert husk.exists(), "a refused command must not touch any file"


# ---------------------------------------------------------------------------
# Round 3: which leftovers are marked, and when
# ---------------------------------------------------------------------------
def _snapshot(directory):
    return {p.name: BR.sha256_file(p) for p in sorted(directory.iterdir()) if p.is_file()}


def _age_everything_but_the_baseline(env):
    for p in list(env.dir.iterdir()):
        if p != env.baseline:
            _age(p)


def _killed_records(env):
    return [p for p in env.sidecars()
            if json.loads(p.read_bytes())["reason"].startswith("killed on purpose")]


def test_a_killed_refresh_is_still_marked_after_a_later_refresh_landed(env, tmp_path, capsys):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, [pairs[0]]), tmp_path)
    killed = _killed_records(env)
    assert len(killed) == 1
    # a later refresh lands while the leftovers are still young, so it leaves them alone
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(pairs, pairs[:2])) == 0
    assert not [ln for ln in _messages(capsys) if "interrupted" in ln]
    assert killed[0].exists()
    _age_everything_but_the_baseline(env)
    # a third refresh, long after: the killed record is still caught
    assert env.run(["--refresh", "--reason", "third refresh, long after the kill"],
                   _grid(pairs, pairs[:3])) == 0
    lines = [ln for ln in _messages(capsys) if "interrupted" in ln]
    assert len(lines) == 1, lines
    assert "removed 1 leftover temp file(s), marked 1 unfinished record(s)" in lines[0]
    assert not killed[0].exists()
    assert (env.dir / (killed[0].name + ".not-applied")).exists()
    assert not any(n.endswith(".tmp") for n in env.files())
    assert len(env.sidecars()) == 2 and not _killed_records(env)    # the two that landed stay live


def test_a_refresh_that_landed_is_not_marked_after_the_baseline_is_put_back(env, capsys):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    old_bytes = env.baseline.read_bytes()
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(pairs, pairs[:1])) == 0
    landed = env.sidecars()
    assert len(landed) == 1
    env.baseline.write_bytes(old_bytes)                  # git checkout -- baseline_alignments.json
    _age_everything_but_the_baseline(env)
    _messages(capsys)
    assert env.run(["--refresh", "--reason", "a later refresh after the revert"],
                   _grid(pairs, pairs[1:2])) == 0
    assert landed[0].exists(), "the record of a refresh that landed was renamed"
    assert not any(n.endswith(".not-applied") for n in env.files())
    assert not [ln for ln in _messages(capsys) if "interrupted" in ln]


def test_a_record_whose_temp_file_was_deleted_by_hand_cannot_be_proved_unlanded(env, tmp_path):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:1]), tmp_path)
    for p in env.dir.glob("*.tmp"):
        p.unlink()
    _age_everything_but_the_baseline(env)
    assert BR.cleanup_interrupted(env.baseline) == {"removed": [], "marked": []}
    assert len(_killed_records(env)) == 1, "a record that cannot be proved unlanded stays as it is"


def test_two_killed_refreshes_around_a_landed_one_are_both_marked(env, tmp_path):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:1]), tmp_path)
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(pairs, pairs[:2])) == 0
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:3]), tmp_path)
    killed = _killed_records(env)
    assert len(killed) == 2
    _age_everything_but_the_baseline(env)
    done = BR.cleanup_interrupted(env.baseline)
    assert sorted(done["marked"]) == sorted(p.name for p in killed)
    assert len(done["removed"]) == 2
    assert not any(n.endswith(".tmp") for n in env.files())
    assert len(env.sidecars()) == 1 and not _killed_records(env)    # only the one that landed


def test_a_refresh_refused_by_a_guard_changes_no_file_even_with_stale_leftovers(env, tmp_path):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, pairs[:60]))
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:59]), tmp_path)
    _age_everything_but_the_baseline(env)
    before = _snapshot(env.dir)
    assert any(n.endswith(".tmp") for n in before) and len(_killed_records(env)) == 1
    rc = env.run(["--refresh", "--reason", GOOD_REASON], _grid(pairs, pairs[:10]))   # 50 pairs move
    assert rc == 2
    assert _snapshot(env.dir) == before, "a refused refresh wrote or deleted something"


def test_the_module_docstring_states_the_rule_that_is_actually_applied():
    assert "differs from the baseline's current hash" not in BR.__doc__
    assert "staged_temp" in BR.__doc__ and "raw bytes" in BR.__doc__


# ---------------------------------------------------------------------------
# Round 4: a failed rename, reused process ids, records younger than their temp files
# ---------------------------------------------------------------------------
def _set_age(path, seconds):
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _the_temp_files(env):
    return sorted(p for p in env.dir.iterdir() if p.name.endswith(".tmp"))


def test_a_failed_rename_keeps_the_temp_file_that_proves_the_record_never_landed(
        env, tmp_path, monkeypatch):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:1]), tmp_path)
    _age_everything_but_the_baseline(env)
    real_rename = os.rename

    def locked(src, dst):                           # Dropbox or an editor holds the record open
        if str(dst).endswith(".not-applied"):
            raise PermissionError(32, "The process cannot access the file")
        return real_rename(src, dst)

    with monkeypatch.context() as m:
        m.setattr(os, "rename", locked)
        first = BR.cleanup_interrupted(env.baseline)
    assert first == {"removed": [], "marked": []}
    assert len(_the_temp_files(env)) == 1, "the only proof was deleted although the rename failed"
    assert len(_killed_records(env)) == 1
    second = BR.cleanup_interrupted(env.baseline)   # the rename works now
    assert len(second["marked"]) == 1 and len(second["removed"]) == 1
    assert _the_temp_files(env) == [] and _killed_records(env) == []


def test_other_stale_temp_files_are_still_deleted_when_one_rename_fails(env, tmp_path, monkeypatch):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:1]), tmp_path)
    junk = env.dir / "baseline_alignments.json.31337.tmp"
    junk.write_bytes(b"junk with no record")
    _age_everything_but_the_baseline(env)
    real_rename = os.rename

    def locked(src, dst):
        if str(dst).endswith(".not-applied"):
            raise PermissionError(32, "in use")
        return real_rename(src, dst)

    with monkeypatch.context() as m:
        m.setattr(os, "rename", locked)
        first = BR.cleanup_interrupted(env.baseline)
    assert first == {"removed": [junk.name], "marked": []}
    assert len(_the_temp_files(env)) == 1


def _land_with_pid(env, monkeypatch, pid, payload, reason=GOOD_REASON):
    with monkeypatch.context() as m:
        m.setattr(os, "getpid", lambda: pid)
        return env.run(["--refresh", "--reason", reason], payload)


def test_a_reused_pid_cannot_destroy_the_proof_of_an_earlier_killed_refresh(
        env, tmp_path, monkeypatch):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:1]), tmp_path, pid=4242)
    killed = _killed_records(env)
    assert len(killed) == 1
    assert _land_with_pid(env, monkeypatch, 4242, _grid(pairs, pairs[:2])) == 0   # same pid lands
    _age_everything_but_the_baseline(env)
    done = BR.cleanup_interrupted(env.baseline)
    assert done["marked"] == [killed[0].name] and len(done["removed"]) == 1
    assert len(env.sidecars()) == 1 and not _killed_records(env)


def test_two_killed_refreshes_with_the_same_pid_are_both_marked(env, tmp_path):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:1]), tmp_path, pid=4242)
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:2]), tmp_path, pid=4242)
    killed = _killed_records(env)
    assert len(killed) == 2 and len(_the_temp_files(env)) == 2
    _age_everything_but_the_baseline(env)
    done = BR.cleanup_interrupted(env.baseline)
    assert sorted(done["marked"]) == sorted(p.name for p in killed)
    assert len(done["removed"]) == 2 and _the_temp_files(env) == []


def test_a_landed_record_is_not_marked_by_a_later_kill_with_the_same_pid_and_payload(
        env, tmp_path, monkeypatch):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    payload = _grid(pairs, pairs[:1])
    assert _land_with_pid(env, monkeypatch, 4242, payload) == 0
    landed = env.sidecars()
    assert len(landed) == 1
    _hard_kill_a_refresh(env, payload, tmp_path, pid=4242)         # same pid, same bytes
    killed = _killed_records(env)
    assert len(killed) == 1
    _age_everything_but_the_baseline(env)
    done = BR.cleanup_interrupted(env.baseline)
    assert done["marked"] == [killed[0].name], "the record of the refresh that landed was marked"
    assert landed[0].exists() and len(env.sidecars()) == 1


def test_new_format_temp_names_are_recognised_as_stale_leftovers(env):
    env.write_old(_grid(_pairs(4), []))
    stale = [env.dir / "baseline_alignments.json.4242.0123abcd.tmp",
             env.dir / "baseline_alignments.refresh-20260101T000000Z.json.4242.89abcdef.tmp"]
    keep = [env.dir / "baseline_alignments.json.4242.0123abcdeff.tmp",    # token of the wrong length
            env.dir / "baseline_alignments.json.4242.FEDCBA98.tmp",        # not hex in lower case
            env.dir / "baseline_alignments.json.notapid.0123abcd.tmp"]
    for p in stale + keep:
        p.write_bytes(b"x" * 10)
        _age(p)
    done = BR.cleanup_interrupted(env.baseline)
    assert sorted(done["removed"]) == sorted(p.name for p in stale)
    assert all(p.exists() for p in keep)


def test_a_young_record_keeps_its_older_temp_file(env, tmp_path):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:1]), tmp_path)
    record, temp = _killed_records(env)[0], _the_temp_files(env)[0]
    _set_age(record, 60)
    _set_age(temp, 200)                           # the temp is always older than its record
    assert BR.cleanup_interrupted(env.baseline) == {"removed": [], "marked": []}
    assert temp.exists() and record.exists(), "an unjudged record lost the temp file that proves it"
    _set_age(record, 200)                         # now the record is stale too
    done = BR.cleanup_interrupted(env.baseline)
    assert done["marked"] == [record.name] and done["removed"] == [temp.name]


def test_an_old_record_whose_temp_file_is_young_is_not_judged_yet(env, tmp_path):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    _hard_kill_a_refresh(env, _grid(pairs, pairs[:1]), tmp_path)
    record, temp = _killed_records(env)[0], _the_temp_files(env)[0]
    _set_age(record, 200)
    _set_age(temp, 60)
    assert BR.cleanup_interrupted(env.baseline) == {"removed": [], "marked": []}
    assert temp.exists() and record.exists()


# ---------------------------------------------------------------------------
# Round 4: docstrings that list the guards, and the string check on out / in
# ---------------------------------------------------------------------------
def test_the_guard_docs_name_every_guard_and_the_duplicate_refusal():
    import test_alignment_baseline as TAB
    for guard in ("policy_shift", "status_flips", "overlap_policy_changes",
                  "pairs_changed", "pairs_added", "pairs_removed"):
        assert guard in BR.guard_trips.__doc__, guard
    assert "middle two" not in BR.guard_trips.__doc__
    assert "duplicate" in BR.guard_trips.__doc__
    doc = TAB.__doc__
    assert "25+ pairs" in doc and "duplicate" in doc, doc


@pytest.mark.parametrize("bad_row", [
    dict(out=7), dict(**{"in": None}),
], ids=["out-is-an-int", "in-is-none"])
def test_a_row_whose_out_or_in_is_not_a_string_is_refused_and_nothing_changes(env, capsys, bad_row):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    before = _snapshot(env.dir)
    rows = _grid(pairs, [])["rows"]
    rows[3] = dict(rows[3], **bad_row)
    rc = env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], _payload(rows))
    assert rc == 2
    assert _snapshot(env.dir) == before
    lines = _messages(capsys)
    assert len(lines) == 1 and "not a baseline payload" in lines[0], lines


# ---------------------------------------------------------------------------
# Round 3: guards on changed and added pairs, duplicate pairs
# ---------------------------------------------------------------------------
SWEEPS = {
    "alignment_policy": dict(alignment_policy="tail_anchor_rescue_v1"),
    "plan_specs": dict(plan={"status": "ok", "specs": [{"kind": "outgoing_tail"}]}),
    "swap_progress": dict(swap_progress=0.75),
}


@pytest.mark.parametrize("sweep", sorted(SWEEPS))
def test_one_field_changed_on_every_pair_is_refused_then_accepted_and_recorded(env, capsys, sweep):
    pairs = _pairs(15)[:200]
    env.write_old(_grid(pairs, []))
    new = _payload([_ok_row(a, b, **SWEEPS[sweep]) for a, b in pairs])
    before = env.baseline.read_bytes()
    rc = env.run(["--refresh", "--reason", GOOD_REASON], new)
    assert rc == 2 and env.baseline.read_bytes() == before and env.sidecars() == []
    text = "\n".join(_short_refusal(_messages(capsys)))
    assert "pairs_changed" in text and "--accept-shift pairs_changed" in text
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is True and rec["guards_tripped_names"] == ["pairs_changed"]
    assert rec["diff_summary"]["pairs_changed"] == 200


def test_a_baseline_that_grows_tenfold_between_old_tracks_is_refused_then_accepted_and_recorded(
        env, capsys):
    # (round 5: growth by NEW tracks is expected now, so this grows between the 15 tracks
    # the old baseline already has: 21 of their 210 pairs become all 210. Round 6 renamed
    # it: the name used to claim the refusal for any tenfold growth.)
    env.write_old(_grid(_pairs(15)[:21], []))
    new = _grid(_pairs(15), [])                                  # 210 pairs, 189 of them new
    before = env.baseline.read_bytes()
    rc = env.run(["--refresh", "--reason", GOOD_REASON], new)
    assert rc == 2 and env.baseline.read_bytes() == before and env.sidecars() == []
    text = "\n".join(_short_refusal(_messages(capsys)))
    assert "pairs_added" in text and "--accept-shift pairs_added" in text
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is True and rec["guards_tripped_names"] == ["pairs_added"]
    assert rec["diff_summary"]["pairs_added"] == 189
    assert rec["diff_summary"]["pairs_added_unexplained"] == 189


def test_a_baseline_that_grows_from_15_to_45_tracks_needs_no_flag(env):
    # the original shape of the tenfold test: 30 new tracks, 210 pairs become 1980
    env.write_old(_grid(_pairs(15), []))
    new = _grid(_pairs(45), [])
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    summary = rec["diff_summary"]
    assert summary["pairs_added"] == 1770 == summary["pairs_added_new_track"]
    assert summary["pairs_added_unexplained"] == 0 and summary["old_tracks_gone"] == 0
    assert rec["guards_tripped"] == [] and rec["guards_accepted"] == []
    assert rec["accept_shift_flag"] is False
    assert json.loads(env.baseline.read_bytes()) == new


def test_the_very_first_baseline_is_not_held_back_by_the_added_pairs_guard(env):
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(30), [])) == 0


@pytest.mark.parametrize("count,compared,tripped", [
    (24, 1000, False),    # under the pair count
    (25, 1000, True),     # the pair count is reached
    (10, 40, False),      # exactly 25 percent is not "more than 25 percent"
    (11, 40, True),       # 27.5 percent
    (0, 0, False),
])
@pytest.mark.parametrize("key,guard", [("pairs_changed", "pairs_changed"),
                                       ("pairs_added", "pairs_added")])
def test_changed_and_added_guard_edges(key, guard, count, compared, tripped):
    shift = {"total_moved": 0, "pairs_compared": compared}
    summary = {"pairs_old": compared, "pairs_removed": 0, "pairs_changed": 0,
               "pairs_added": 0, "changed_by_field": {}}
    summary[key] = count
    names = _guard_names(BR.guard_trips(shift, summary))
    assert names == ([guard] if tripped else [])


def test_duplicate_pairs_are_refused_and_no_flag_overrides_that(env, capsys):
    pairs = _pairs(15)[:200]
    env.write_old(_grid(pairs, []))
    before = env.baseline.read_bytes()
    dup = _payload([_ok_row(a, b) for a, b in pairs] * 2)         # every (out, in) twice
    for flags in ([], ["--accept-shift"]):
        assert env.run(["--refresh", "--reason", GOOD_REASON, *flags], dup) == 2
        lines = _messages(capsys)
        assert len(lines) == 1 and "duplicate" in lines[0], lines
    assert env.baseline.read_bytes() == before and env.sidecars() == []


def test_an_existing_baseline_with_duplicate_pairs_is_refused_before_the_slow_step(env, capsys):
    env.baseline.write_bytes(BR._baseline_bytes(
        _payload([_ok_row("a", "b"), _ok_row("a", "b"), _ok_row("a", "c")])))
    before = env.baseline.read_bytes()
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(4), [])) == 2
    assert env.called == [] and env.baseline.read_bytes() == before
    lines = _messages(capsys)
    assert len(lines) == 1 and "duplicate" in lines[0]


TABLE = [
    # kind, pairs in the old baseline, how many change, expected exit code
    ("flips", 200, 9, 0), ("flips", 200, 10, 2),
    ("overlap", 200, 9, 0), ("overlap", 200, 10, 2),
    ("removed", 200, 10, 0), ("removed", 200, 11, 2),
    ("removed", 140, 7, 0), ("removed", 20, 2, 2), ("removed", 19, 1, 2),
]


@pytest.mark.parametrize("kind,total,n,expected", TABLE)
def test_the_boundary_table_keeps_its_exit_codes(env, kind, total, n, expected):
    pairs = _pairs(15)[:total]
    old = env.write_old(_grid(pairs, []))
    rows = old["rows"]
    if kind == "flips":
        new = _payload([_raise_row(a, b) if i < n else rows[i] for i, (a, b) in enumerate(pairs)])
    elif kind == "overlap":
        new = _payload([dict(rows[i], overlap_policy="short_32") if i < n else rows[i]
                        for i in range(total)])
    else:
        new = _grid(pairs[:total - n], [])
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == expected


def test_reason_edge_cases_keep_their_exit_codes(env):
    pairs = _pairs(5)
    env.write_old(_grid(pairs, []))
    new = _grid(pairs, [])
    assert env.run(["--refresh", "--reason", " " * 15 + "a"], new) == 2
    assert env.sidecars() == []
    reason = "tab\tand newline\nin the middle of a reason"
    assert env.run(["--refresh", "--reason", reason], new) == 0
    assert json.loads(env.sidecars()[0].read_bytes())["reason"] == reason
    assert env.run(["--refresh", "--reason", "-3 dB after the new bass cue weights"], new) == 0


# ---------------------------------------------------------------------------
# Round 3: the read-only baseline message and the hash basis
# ---------------------------------------------------------------------------
def test_a_read_only_baseline_is_named_in_the_failure_message(env, capsys):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, []))
    old_bytes = env.baseline.read_bytes()
    os.chmod(env.baseline, stat.S_IREAD)
    try:
        rc = env.run(["--refresh", "--reason", GOOD_REASON], _grid(pairs, pairs[:1]))
    finally:
        os.chmod(env.baseline, stat.S_IREAD | stat.S_IWRITE)
    assert rc == 1
    lines = _messages(capsys)
    assert len(lines) == 1 and "read-only" in lines[0], lines
    assert env.baseline.read_bytes() == old_bytes
    assert not any(n.endswith(".tmp") for n in env.files())


def test_the_sidecar_says_what_the_hashes_are_taken_over(env, tmp_path):
    files = {"crlf.py": b"a\r\nb\r\n", "lf.json": b"{}\n[]\n", "mixed.py": b"a\r\nb\n",
             "none.json": b"{}", "cache.npz": b"npz-bytes"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    inputs = [tmp_path / n for n in files] + [tmp_path / "gone.py"]
    env.write_old(_grid(_pairs(4), []))
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(4), []),
                   inputs=inputs, root=tmp_path) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["hash_basis"] == "raw bytes as stored on this machine"
    assert rec["input_line_endings"] == {
        "crlf.py": "crlf", "lf.json": "lf", "mixed.py": "mixed", "none.json": "none",
        "cache.npz": "binary", "gone.py": "missing"}
    assert rec["baseline_line_ending"] == BR.line_ending(env.baseline.read_bytes())
    assert rec["baseline_line_ending"] in ("crlf", "lf")


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
    text = "\n".join(_short_refusal(_messages(capsys)))
    assert "policy_shift" in text and "--accept-shift" in text


def test_a_50_pair_shift_is_accepted_with_accept_shift_and_the_sidecar_says_so(env):
    pairs = _pairs(11)
    env.write_old(_grid(pairs, pairs[:60]))
    new = _grid(pairs, pairs[:10])
    # (round 5: three guards trip here, so the bare flag is not enough; name them)
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    "policy_shift,overlap_policy_changes,pairs_changed"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is True and rec["accept_shift_flag"] is True
    assert rec["guards_tripped_names"] == [
        "policy_shift", "overlap_policy_changes", "pairs_changed"]
    assert rec["guards_accepted"] == [
        "policy_shift", "overlap_policy_changes", "pairs_changed"]
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
    text = "\n".join(_short_refusal(_messages(capsys)))
    assert "status_flips" in text and "--accept-shift" in text
    # (round 5: three guards trip here, so the bare flag is not enough; name them)
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    "status_flips,overlap_policy_changes,pairs_changed"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is True
    assert rec["guards_tripped_names"] == [
        "status_flips", "overlap_policy_changes", "pairs_changed"]
    assert rec["guards_accepted"] == [
        "status_flips", "overlap_policy_changes", "pairs_changed"]
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
    text = "\n".join(_short_refusal(_messages(capsys)))
    assert "overlap_policy_changes" in text
    # (round 5: two guards trip here, so the bare flag is not enough; name them)
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    "overlap_policy_changes,pairs_changed"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["accepted_shift"] is True
    # (60 changed pairs trips pairs_changed too)
    assert rec["guards_tripped_names"] == ["overlap_policy_changes", "pairs_changed"]
    assert rec["guards_accepted"] == ["overlap_policy_changes", "pairs_changed"]
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
    assert BR.CHANGED_MAX_PAIRS == 25 and BR.CHANGED_MAX_FRACTION == 0.25
    assert BR.ADDED_MAX_PAIRS == 25 and BR.ADDED_MAX_FRACTION == 0.25
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
    # (round 5: three guards trip here, so the bare flag is not enough; name them)
    accepted = scratch.refresh("--reason", "sweeping change read through and accepted",
                               "--accept-shift",
                               "policy_shift,overlap_policy_changes,pairs_changed")
    assert accepted.returncode == 0, accepted.stderr + accepted.stdout
    newest = [p for p in scratch.sidecars()
              if json.loads(p.read_bytes())["accepted_shift"]]
    assert len(newest) == 1
    rec = json.loads(newest[0].read_bytes())          # (round 3, MiniMax: pin the exact guards, as the API twin does)
    assert rec["guards_tripped_names"] == ["policy_shift", "overlap_policy_changes", "pairs_changed"]
    assert rec["guards_accepted"] == rec["guards_tripped_names"]


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
    # (round 5: three guards trip here, so the bare flag is not enough; name them)
    accepted = scratch.refresh("--reason", "everything raises, read through and accepted",
                               "--accept-shift",
                               "status_flips,overlap_policy_changes,pairs_changed")
    assert accepted.returncode == 0, accepted.stderr + accepted.stdout
    recs = [json.loads(p.read_bytes()) for p in scratch.sidecars()]
    flagged = [r for r in recs if r["accepted_shift"]]
    assert len(flagged) == 1
    assert flagged[0]["guards_tripped_names"] == [
        "status_flips", "overlap_policy_changes", "pairs_changed"]
    assert flagged[0]["guards_accepted"] == flagged[0]["guards_tripped_names"]


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
# Round 5: a new track's pairs are expected; --accept-shift names what it accepts
# ---------------------------------------------------------------------------
ALL_GUARDS = ("policy_shift", "status_flips", "overlap_policy_changes",
              "pairs_changed", "pairs_added", "pairs_removed")


def _refusal(capsys):
    """The refusal text (its lines joined), after checking it is short (_short_refusal)."""
    return "\n".join(_short_refusal(_messages(capsys)))


def _plain_refusal(capsys):
    """The one line of a refusal that is not about the guards or --accept-shift."""
    lines = _messages(capsys)
    assert len(lines) == 1, lines
    return lines[0]


def _counts_of_added(summary):
    return (summary["pairs_added"], summary["pairs_added_new_track"],
            summary["pairs_added_unexplained"])


def _one_new_track(env):
    """20 old tracks (380 pairs) and one new track, T20. Its 40 pairs are the only change."""
    old = env.write_old(_grid(_pairs(20), []))
    new = _grid(_pairs(21), [])
    assert len(new["rows"]) - len(old["rows"]) == 40
    return old, new


def _two_guards(env):
    """80 old pairs between 11 tracks. 26 of them change a score (pairs_changed) and the
    30 other pairs between the same tracks are added (pairs_added). Exactly those two
    guards trip."""
    pairs = _pairs(11)
    old = env.write_old(_grid(pairs[:80], []))
    rows = [_ok_row(a, b, score=99) if i < 26 else _ok_row(a, b)
            for i, (a, b) in enumerate(pairs)]
    return old, _payload(rows)


def _refresh_api(env, payload, accept_shift):
    done = BR.refresh_baseline(env.baseline, payload, reason=GOOD_REASON,
                               accept_shift=accept_shift, clock=lambda: FIXED_NOW,
                               head=lambda cwd: FAKE_COMMIT)
    return done, json.loads(done["sidecar"].read_bytes())


def test_a_new_track_and_its_40_pairs_need_no_flag(env, capsys):
    old, new = _one_new_track(env)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert _counts_of_added(rec["diff_summary"]) == (40, 40, 0)
    assert rec["guards_tripped"] == [] and rec["guards_accepted"] == []
    assert rec["accepted_shift"] is False and rec["accept_shift_flag"] is False
    assert json.loads(env.baseline.read_bytes()) == new
    lines = _messages(capsys)
    assert any("40 added (40 expected as growth, 0 unexplained)" in ln for ln in lines), lines


def test_a_remix_next_to_its_original_is_a_new_track_so_its_pairs_are_explained(env):
    def pairs_of(names):
        return [(a, b) for a in names for b in names if a != b]
    originals = [f"Artist {i:02d} - Title {i:02d}" for i in range(20)]
    old = env.write_old(_grid(pairs_of(originals), []))
    names = originals + [originals[0] + " (Some Remixer Remix)"]
    new = _grid(pairs_of(names), [])
    assert _counts_of_added(BR.diff_summary(old, new)) == (40, 40, 0)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0


def test_diff_summary_splits_added_pairs_into_new_track_and_unexplained():
    old = _payload([_ok_row("a", "b"), _ok_row("a", "c"), _ok_row("b", "c")])
    new = _payload([_ok_row("a", "b"), _ok_row("a", "c"), _ok_row("b", "c"),
                    _ok_row("a", "d"),           # d is in no old row: explained
                    _ok_row("d", "a"),           # explained
                    _ok_row("c", "a"),           # a and c are both old tracks: unexplained
                    _ok_row("b", "a")])          # unexplained
    s = BR.diff_summary(old, new)
    assert _counts_of_added(s) == (4, 2, 2)
    assert s["pairs_added"] == len(s["first_added"]) == 4, "the record keeps every added pair"
    first = BR.diff_summary(None, new)
    assert _counts_of_added(first) == (7, 7, 0), "with no old baseline every track is new"


@pytest.mark.parametrize("sink_is_an_in", [True, False], ids=["only-an-in", "only-an-out"])
def test_a_track_that_is_only_an_in_or_only_an_out_in_the_old_baseline_is_an_old_track(
        env, capsys, sink_is_an_in):
    names = [f"a{i:02d}" for i in range(30)]
    if sink_is_an_in:
        old_pairs, added = [(n, "SINK") for n in names], [("SINK", n) for n in names]
    else:
        old_pairs, added = [("SRC", n) for n in names], [(n, "SRC") for n in names]
    old = env.write_old(_payload([_ok_row(a, b) for a, b in old_pairs]))
    new = _payload([_ok_row(a, b) for a, b in old_pairs + added])
    assert _counts_of_added(BR.diff_summary(old, new)) == (30, 0, 30)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    line = _refusal(capsys)
    assert "pairs_added" in line and "unexplained" in line, line


def test_30_added_pairs_between_two_old_tracks_still_trip_pairs_added(env, capsys):
    pairs = _pairs(11)
    old = env.write_old(_grid(pairs[:80], []))
    new = _grid(pairs, [])
    assert _counts_of_added(BR.diff_summary(old, new)) == (30, 0, 30)
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    line = _refusal(capsys)
    assert "pairs_added" in line and "unexplained" in line and "--accept-shift" in line, line
    assert _snapshot(env.dir) == before, "a refused refresh wrote something"
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_tripped_names"] == ["pairs_added"]
    assert rec["guards_accepted"] == ["pairs_added"]
    assert _counts_of_added(rec["diff_summary"]) == (30, 0, 30)


def test_a_new_track_does_not_hide_a_policy_shift_among_the_old_tracks(env, capsys):
    pairs = _pairs(20)
    old = env.write_old(_grid(pairs, pairs[:12]))                # 12 pairs named
    new = _grid(_pairs(21), [])                                  # all standard, T20 is new
    shift, summary = BR.policy_shift(old, new), BR.diff_summary(old, new)
    assert shift["named_landmark_64_to_standard_48"] == 12
    assert _counts_of_added(summary) == (40, 40, 0)
    assert _guard_names(BR.guard_trips(shift, summary)) == [
        "policy_shift", "overlap_policy_changes"]
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    line = _refusal(capsys)
    assert "policy_shift" in line and "pairs_added" not in line, line
    assert _snapshot(env.dir) == before
    assert env.run(["--refresh", "--reason", GOOD_REASON,
                    "--accept-shift", "policy_shift,overlap_policy_changes"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_accepted"] == ["policy_shift", "overlap_policy_changes"]
    assert _counts_of_added(rec["diff_summary"]) == (40, 40, 0)


@pytest.mark.parametrize("kind", ["changed", "removed"])
def test_pairs_changed_or_removed_among_old_tracks_still_trip_beside_a_new_track(
        env, capsys, kind):
    pairs = _pairs(20)
    old = env.write_old(_grid(pairs, []))
    rows = old["rows"]
    if kind == "changed":                                        # 30 pairs change a score
        rows = [dict(r, score=99) if i < 30 else r for i, r in enumerate(rows)]
    else:                                                        # 30 of 380 pairs are gone
        rows = rows[30:]
    extra = [_ok_row(a, b) for a, b in _pairs(21) if "T20" in (a, b)]
    new = _payload(rows + extra)
    # (round 7: a removed old pair means nothing is expected growth, so beside removed
    # pairs the new track's 40 pairs count too and pairs_added trips with pairs_removed)
    expected = (40, 40, 0) if kind == "changed" else (40, 0, 40)
    assert _counts_of_added(BR.diff_summary(old, new)) == expected
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    line = _refusal(capsys)
    if kind == "changed":
        assert "pairs_changed" in line and "pairs_added" not in line, line
    else:
        assert "pairs_removed" in line and "pairs_added" in line, line


def test_two_guards_and_the_bare_flag_are_refused_with_a_few_short_lines(env, capsys):
    old, new = _two_guards(env)
    shift, summary = BR.policy_shift(old, new), BR.diff_summary(old, new)
    assert _guard_names(BR.guard_trips(shift, summary)) == ["pairs_changed", "pairs_added"]
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], new) == 2
    line = _refusal(capsys)
    assert "2 guards tripped" in line, line
    assert "pairs_changed: 26 of 80 pairs" in line and "pairs_added: 30 unexplained" in line
    assert "--accept-shift pairs_changed,pairs_added" in line and "all" in line, line
    assert "a bare --accept-shift is for one guard only" in line, line
    assert _snapshot(env.dir) == before and env.sidecars() == []


@pytest.mark.parametrize("given,missing", [
    ("pairs_changed", ["pairs_added"]),
    ("pairs_added", ["pairs_changed"]),
    ("policy_shift", ["pairs_changed", "pairs_added"]),
])
def test_accept_shift_with_names_refuses_every_guard_it_does_not_name(
        env, capsys, given, missing):
    old, new = _two_guards(env)
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift", given], new) == 2
    lines = _short_refusal(_messages(capsys))
    count = f"{len(missing)} guard" + ("" if len(missing) == 1 else "s")
    assert lines[0] == f"refresh refused: not accepted: {count}", lines
    facts = {"pairs_changed": "26 of 80 pairs", "pairs_added": "30 unexplained"}
    assert [ln for ln in lines if ln.startswith("  ")] == [
        f"  {g}: {facts[g]}" for g in missing]
    assert "--accept-shift pairs_changed,pairs_added" in lines, lines
    assert "To accept, run:" in lines and "Read the diff, then run:" not in lines
    assert _snapshot(env.dir) == before and env.sidecars() == []


@pytest.mark.parametrize("flag_args,accepted", [
    (["--accept-shift", "pairs_changed,pairs_added"], ["pairs_changed", "pairs_added"]),
    (["--accept-shift", "pairs_added,pairs_changed"], ["pairs_changed", "pairs_added"]),
    (["--accept-shift=pairs_added, pairs_changed"], ["pairs_changed", "pairs_added"]),
    (["--accept-shift", "pairs_added,pairs_added,pairs_changed,policy_shift"],
     ["pairs_changed", "pairs_added"]),
    (["--accept-shift", "all"], ["all"]),
])
def test_accept_shift_with_every_tripped_name_or_all_passes_and_the_record_says_which(
        env, capsys, flag_args, accepted):
    old, new = _two_guards(env)
    assert env.run(["--refresh", "--reason", GOOD_REASON, *flag_args], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_accepted"] == accepted
    assert rec["guards_tripped_names"] == ["pairs_changed", "pairs_added"]
    assert rec["accepted_shift"] is True and rec["accept_shift_flag"] is True
    assert json.loads(env.baseline.read_bytes()) == new
    lines = _messages(capsys)
    if accepted == ["all"]:
        assert any("all guards accepted" in ln for ln in lines), lines
    else:
        assert any(ln.startswith("guards accepted: pairs_changed, pairs_added")
                   for ln in lines), lines


@pytest.mark.parametrize("value", [
    "bogus", "pairs_added,bogus", "all,pairs_added", "pairs_added,all", "",
    "pairs_added,,pairs_changed", "PAIRS_ADDED", "pairs-added"])
def test_an_unknown_guard_name_is_a_usage_error_that_lists_the_valid_names(
        env, capsys, value):
    old, new = _two_guards(env)
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift", value], new) == 2
    assert env.called == [], "a usage error must come before the slow regeneration"
    line = _refusal(capsys)
    assert all(name in line for name in ALL_GUARDS) and "all" in line, line
    assert _snapshot(env.dir) == before and env.sidecars() == []


def test_an_unknown_guard_name_is_refused_even_when_no_guard_trips(env, capsys):
    env.write_old(_grid(_pairs(5), []))
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift", "pairs_adde"],
                   _grid(_pairs(5), [])) == 2
    assert env.called == [] and env.sidecars() == []
    assert "'pairs_adde'" in _refusal(capsys)


def test_a_repeated_accept_shift_is_refused_before_anything_is_computed(env, capsys):
    env.write_old(_grid(_pairs(5), []))
    argv = ["--refresh", "--reason", GOOD_REASON, "--accept-shift", "pairs_added",
            "--accept-shift", "pairs_changed"]
    assert env.run(argv, _grid(_pairs(5), [])) == 2
    assert env.called == [] and env.sidecars() == []
    line = _plain_refusal(capsys)
    assert "--accept-shift" in line and "more than once" in line, line


def test_the_api_takes_false_true_all_a_string_or_any_iterable_of_names(env):
    old, new = _two_guards(env)
    wanted = ["pairs_changed", "pairs_added"]
    cases = [("all", ["all"]),
             (("pairs_added", "pairs_changed"), wanted),
             (["pairs_changed", "pairs_added", "policy_shift"], wanted),   # untripped: not recorded
             ("pairs_changed,pairs_added", wanted),
             ({"pairs_added", "pairs_changed"}, wanted),
             (iter(("pairs_added", "pairs_changed")), wanted)]
    for given, expected in cases:
        env.write_old(old)
        done, rec = _refresh_api(env, new, given)
        assert rec["guards_accepted"] == expected and done["guards_accepted"] == expected
        assert rec["accepted_shift"] is True and rec["accept_shift_flag"] is True
    for refused in (False, True, [], ("pairs_changed",)):
        env.write_old(old)
        before = _snapshot(env.dir)
        with pytest.raises(BR.RefreshRefused):
            _refresh_api(env, new, refused)
        assert _snapshot(env.dir) == before, refused
    env.write_old(old)
    before = _snapshot(env.dir)
    with pytest.raises(BR.RefreshRefused) as info:
        _refresh_api(env, new, ["no_such_guard"])
    assert "no_such_guard" in str(info.value)
    assert _snapshot(env.dir) == before


def test_the_bare_flag_still_works_when_exactly_one_guard_tripped(env):
    old = env.write_old(_grid(_pairs(15)[:21], []))
    new = _grid(_pairs(15), [])
    assert _counts_of_added(BR.diff_summary(old, new)) == (189, 0, 189)
    done, rec = _refresh_api(env, new, True)
    assert rec["guards_tripped_names"] == ["pairs_added"]
    assert rec["guards_accepted"] == ["pairs_added"] and done["guards_accepted"] == ["pairs_added"]
    assert rec["accepted_shift"] is True and rec["accept_shift_flag"] is True


@pytest.mark.parametrize("empty", [[], (), set()], ids=["list", "tuple", "set"])
def test_an_empty_list_of_names_is_no_flag_even_when_exactly_one_guard_tripped(env, empty):
    # (round 6) an empty iterable must not read as the bare flag, which WOULD pass here
    old = env.write_old(_grid(_pairs(15)[:21], []))
    new = _grid(_pairs(15), [])
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new),
                                       BR.diff_summary(old, new))) == ["pairs_added"]
    before = _snapshot(env.dir)
    with pytest.raises(BR.RefreshRefused):
        _refresh_api(env, new, empty)
    assert _snapshot(env.dir) == before and env.sidecars() == []
    done, rec = _refresh_api(env, new, True)             # the bare flag does pass
    assert rec["guards_accepted"] == ["pairs_added"]


def test_the_record_says_guards_accepted_is_empty_when_nothing_tripped(env):
    pairs = _pairs(11)
    for given in (False, True, "all", ["pairs_added"]):
        env.write_old(_grid(pairs, pairs[:60]))
        done, rec = _refresh_api(env, _grid(pairs, pairs[:57]), given)       # 3 pairs move
        assert rec["guards_tripped"] == [] and rec["guards_accepted"] == [], given
        assert rec["accepted_shift"] is False
        assert rec["accept_shift_flag"] is bool(given)
        assert done["guards_accepted"] == []


def test_an_api_refusal_for_a_missing_name_or_the_bare_flag_writes_nothing(env):
    old, new = _two_guards(env)
    before = _snapshot(env.dir)
    with pytest.raises(BR.RefreshRefused, match="not accepted: 1 guard"):
        _refresh_api(env, new, ["pairs_changed"])
    with pytest.raises(BR.RefreshRefused, match="2 guards tripped"):
        _refresh_api(env, new, True)
    assert _snapshot(env.dir) == before and env.sidecars() == []


def test_all_does_not_override_duplicate_pairs(env, capsys):
    pairs = _pairs(15)[:200]
    env.write_old(_grid(pairs, []))
    before = env.baseline.read_bytes()
    dup = _payload([_ok_row(a, b) for a, b in pairs] * 2)
    for flag in ("all", "pairs_added,pairs_changed"):
        assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift", flag], dup) == 2
        line = _plain_refusal(capsys)
        assert "duplicate" in line, line
    assert env.baseline.read_bytes() == before and env.sidecars() == []


@pytest.mark.parametrize("count,compared,tripped", [
    (24, 1000, False),    # under the pair count
    (25, 1000, True),     # the pair count is reached
    (10, 40, False),      # exactly 25 percent is not "more than 25 percent"
    (11, 40, True),       # 27.5 percent
    (0, 0, False),
])
def test_the_added_guard_counts_only_the_unexplained_pairs(count, compared, tripped):
    shift = {"total_moved": 0, "pairs_compared": compared}
    summary = {"pairs_old": compared, "pairs_removed": 0, "pairs_changed": 0,
               "pairs_added": count + 500, "pairs_added_new_track": 500,
               "pairs_added_unexplained": count, "changed_by_field": {}}
    names = _guard_names(BR.guard_trips(shift, summary))
    assert names == (["pairs_added"] if tripped else [])


def test_a_summary_without_the_split_counts_every_added_pair_as_unexplained():
    shift = {"total_moved": 0, "pairs_compared": 1000}
    summary = {"pairs_old": 1000, "pairs_removed": 0, "pairs_changed": 0,
               "pairs_added": 30, "changed_by_field": {}}
    assert _guard_names(BR.guard_trips(shift, summary)) == ["pairs_added"]


def test_the_docs_describe_the_new_rules():
    for doc in (BR.__doc__, BR.guard_trips.__doc__):
        assert "unexplained" in doc and "new track" in doc, doc
        assert "--accept-shift" in doc and "all" in doc
    assert "guards_accepted" in BR.__doc__ and "not accepted" in BR.__doc__
    assert "duplicate" in BR.__doc__
    assert "clears all of them at once" not in BR.guard_trips.__doc__
    for doc in (BR.__doc__, BR.guard_trips.__doc__, BR.diff_summary.__doc__):
        flat = " ".join(doc.lower().split())
        assert "replacing a track is not growth" in flat, doc
        assert "when any old pair is removed" in flat, doc
    assert "a few short lines" in BR.run_refresh_cli.__doc__
    assert "one plain line on stderr" not in BR.run_refresh_cli.__doc__
    import test_alignment_baseline as TAB
    for doc in (BR.__doc__, BR.guard_trips.__doc__, TAB.__doc__):
        flat = " ".join(doc.split())
        assert "unless any old pair was removed" in flat, doc
        assert "replacing a track always trips" in flat.lower(), doc
    assert "replacing a track always trips" in " ".join(BR.diff_summary.__doc__.split()).lower()
    # (round 8) removing a track always trips pairs_removed; the old sentence is gone
    for doc in (BR.__doc__, BR.guard_trips.__doc__, BR.diff_summary.__doc__, TAB.__doc__):
        assert "removing a track always trips" in " ".join(doc.split()).lower(), doc
    assert "removing a track always trips pairs_removed" in " ".join(BR.__doc__.split())
    assert "pure removal follows pairs_removed only" not in " ".join(BR.__doc__.split())
    assert "old_tracks_gone" in BR.guard_trips.__doc__
    reason = BR.guard_trips({"total_moved": 0, "pairs_compared": 1000},
                            {"pairs_old": 1000, "pairs_removed": 0, "pairs_changed": 0,
                             "pairs_added": 30, "changed_by_field": {}})[0]
    assert "unless any old pair was removed" in reason, reason
    assert "ONE plain line" not in BR.RefreshRefused.__doc__
    assert "70 characters" in BR.RefreshRefused.__doc__ and "70 characters" in BR.__doc__
    help_text = " ".join(BR._build_parser().format_help().split())
    assert "--accept-shift" in help_text and "pairs_added" in help_text, help_text
    assert "all" in help_text and "exactly one" in help_text, help_text


def test_cli_a_new_stem_needs_no_flag_and_a_second_guard_needs_its_name(tmp_path):
    s = Scratch(tmp_path, n_tracks=13)                           # 156 pairs; a 14th track adds 26
    first = s.refresh("--reason", "bootstrap the synthetic baseline")
    assert first.returncode == 0, first.stderr + first.stdout
    (s.stems / "SECTIONS_STEM_T13.json").write_text(
        json.dumps({"track": "T13", "bpm": 128}), encoding="ascii")
    (s.stems / "T13__stemenv.npz").write_bytes(b"npz T13")
    grown = s.refresh("--reason", "a 14th track joined the corpus, its pairs are new")
    assert grown.returncode == 0, grown.stderr + grown.stdout
    assert "26 added (26 expected as growth, 0 unexplained)" in grown.stdout, grown.stdout
    assert json.loads(s.baseline.read_bytes())["n_pairs"] == 182
    # 12 pairs among the old tracks now take a landmark: two guards trip
    s.control(named=s.pairs()[:12], raising=[])
    before = s.tree()
    bare = s.refresh("--reason", "twelve pairs take a landmark, bare flag", "--accept-shift")
    assert bare.returncode == 2 and s.tree() == before
    lines = [ln for ln in (bare.stderr + bare.stdout).splitlines() if ln.strip()]
    assert "2 guards tripped" in _short_refusal(lines)[0], lines
    named = s.refresh("--reason", "twelve pairs take a landmark, named guards",
                      "--accept-shift", "policy_shift,overlap_policy_changes")
    assert named.returncode == 0, named.stderr + named.stdout
    recs = [json.loads(p.read_bytes()) for p in s.sidecars()]
    accepted = [r for r in recs if r["guards_accepted"]]
    assert len(accepted) == 1
    assert accepted[0]["guards_accepted"] == ["policy_shift", "overlap_policy_changes"]


# ---------------------------------------------------------------------------
# Round 6: replacing a track is not growth; refusals are a few short lines
# ---------------------------------------------------------------------------
def _pairs_of(names):
    return [(a, b) for a in names for b in names if a != b]


def _track_names(count, replace=None):
    """T00, T01, ... and, when `replace` names one of them, that track under a new name."""
    names = [f"T{i:02d}" for i in range(count)]
    return [f"{n} renamed" if n == replace else n for n in names]


def test_renaming_1_of_41_tracks_is_not_growth_and_needs_a_flag(env, capsys):
    old = env.write_old(_grid(_pairs_of(_track_names(41)), []))
    new = _grid(_pairs_of(_track_names(41, replace="T20")), [])
    summary = BR.diff_summary(old, new)
    assert _counts_of_added(summary) == (80, 0, 80)
    assert summary["pairs_removed"] == 80 and summary["old_tracks_gone"] == 1
    shift = BR.policy_shift(old, new)
    # 80 of 1640 is 4.9 percent removed, under the limit, but the old track is gone: the
    # added pairs notice the rename, and (round 8) so does pairs_removed
    assert _guard_names(BR.guard_trips(shift, summary)) == ["pairs_added", "pairs_removed"]
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: 2 guards tripped", lines
    assert "  pairs_added: 80 unexplained" in lines, lines
    assert "  pairs_removed: 1 old track is gone" in lines, lines
    assert "    80 of 1640 pairs are gone (4.9%)" in lines, lines
    assert "--accept-shift pairs_added,pairs_removed" in lines, lines
    assert "old pairs are gone: replacing a track is not growth" in lines, lines
    assert "diff: 80 added (80 unexplained), 80 removed, 0 changed" in lines, lines
    assert _snapshot(env.dir) == before
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    "pairs_added,pairs_removed"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_accepted"] == ["pairs_added", "pairs_removed"]
    assert rec["diff_summary"]["old_tracks_gone"] == 1


def test_renaming_1_of_50_tracks_whose_pairs_now_raise_is_not_growth(env, capsys):
    old = env.write_old(_grid(_pairs_of(_track_names(50)), []))
    renamed = _track_names(50, replace="T20")
    new = _payload([_raise_row(a, b) if "T20 renamed" in (a, b) else _ok_row(a, b)
                    for a, b in _pairs_of(renamed)])
    summary = BR.diff_summary(old, new)
    assert _counts_of_added(summary) == (98, 0, 98) and summary["old_tracks_gone"] == 1
    # (round 8: the renamed track's old name is gone, so pairs_removed trips beside it)
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == [
        "pairs_added", "pairs_removed"]
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert "  pairs_added: 98 unexplained" in lines, lines
    assert "  pairs_removed: 1 old track is gone" in lines, lines
    assert env.sidecars() == []


def test_a_half_rename_where_only_the_in_name_changes_is_not_growth(env, capsys):
    # (round 7) T20 keeps its name as an out, but as an in it gains a trailing space: 40
    # old pairs are re-keyed and now raise. No whole track is gone, but 40 old pairs are.
    names = _track_names(41)
    old = env.write_old(_grid(_pairs_of(names), []))
    new = _payload([_raise_row(a, "T20 ") if b == "T20" else _ok_row(a, b)
                    for a, b in _pairs_of(names)])
    summary = BR.diff_summary(old, new)
    assert _counts_of_added(summary) == (40, 0, 40)
    assert summary["pairs_removed"] == 40 and summary["old_tracks_gone"] == 0
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == ["pairs_added"]
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert "  pairs_added: 40 unexplained" in lines, lines
    assert env.sidecars() == []


def test_a_rename_that_keeps_one_stale_row_of_the_old_name_is_not_growth(env, capsys):
    # (round 7) T20 is renamed in every pair but one stale row (T20 -> T00) is left, so no
    # whole track is gone; 79 old pairs are, and the 80 pairs of the new name all raise.
    names = _track_names(41)
    renamed = _track_names(41, replace="T20")
    old = env.write_old(_grid(_pairs_of(names), []))
    rows = [_raise_row(a, b) if "T20 renamed" in (a, b) else _ok_row(a, b)
            for a, b in _pairs_of(renamed)]
    rows.append(_ok_row("T20", "T00"))
    new = _payload(rows)
    summary = BR.diff_summary(old, new)
    assert _counts_of_added(summary) == (80, 0, 80)
    assert summary["pairs_removed"] == 79 and summary["old_tracks_gone"] == 0
    # 79 of 1640 is 4.8 percent removed: only the added pairs notice the rename
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == ["pairs_added"]
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert "  pairs_added: 80 unexplained" in lines, lines
    assert env.sidecars() == []


def test_a_removed_pair_makes_every_added_pair_unexplained_even_with_every_track_present():
    old = _payload([_ok_row(a, b) for a, b in _pairs_of(["a", "b", "c"])])
    rows = [_ok_row(a, b) for a, b in _pairs_of(["a", "b", "c", "d"])
            if (a, b) != ("a", "b")]                       # d is new and one old pair is gone
    s = BR.diff_summary(old, _payload(rows))
    assert s["old_tracks_gone"] == 0 and s["pairs_removed"] == 1
    assert _counts_of_added(s) == (6, 0, 6)


def test_swapping_a_track_for_its_newer_version_trips_added_and_removed(env, capsys):
    names = _track_names(20)
    swapped = _track_names(20, replace="T05")
    old = env.write_old(_grid(_pairs_of(names), []))
    new = _grid(_pairs_of(swapped), [])
    summary = BR.diff_summary(old, new)
    assert _counts_of_added(summary) == (38, 0, 38) and summary["pairs_removed"] == 38
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == [
        "pairs_added", "pairs_removed"]
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: 2 guards tripped", lines
    # naming only one of the two still refuses and says which is not accepted
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    "pairs_added"], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: not accepted: 1 guard", lines
    # (round 8: the track is gone, so the line names it first, then the pairs and share)
    assert [ln for ln in lines if ln.startswith("  ")] == [
        "  pairs_removed: 1 old track is gone",
        "    38 of 380 pairs are gone (10.0%, over the 5% limit)"], lines
    assert env.sidecars() == []
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    "pairs_added,pairs_removed"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_accepted"] == ["pairs_added", "pairs_removed"]
    assert _counts_of_added(rec["diff_summary"]) == (38, 0, 38)
    assert rec["diff_summary"]["old_tracks_gone"] == 1
    assert json.loads(env.baseline.read_bytes()) == new


def test_pure_growth_of_one_track_in_a_50_track_corpus_still_needs_no_flag(env):
    old = env.write_old(_grid(_pairs_of(_track_names(50)), []))
    new = _grid(_pairs_of(_track_names(51)), [])
    summary = BR.diff_summary(old, new)
    assert _counts_of_added(summary) == (100, 100, 0) and summary["old_tracks_gone"] == 0
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_tripped"] == [] and rec["guards_accepted"] == []


def test_a_track_that_is_gone_makes_every_added_pair_unexplained():
    old = _payload([_ok_row(a, b) for a, b in _pairs_of(["a", "b", "c"])])
    grown = _payload([_ok_row(a, b) for a, b in _pairs_of(["a", "b", "c", "d"])])
    swapped = _payload([_ok_row(a, b) for a, b in _pairs_of(["a", "b", "d"])])
    s = BR.diff_summary(old, grown)
    assert _counts_of_added(s) == (6, 6, 0) and s["old_tracks_gone"] == 0
    s = BR.diff_summary(old, swapped)
    assert _counts_of_added(s) == (4, 0, 4) and s["old_tracks_gone"] == 1
    assert BR.diff_summary(None, swapped)["old_tracks_gone"] == 0


def _all_six(env):
    """20 tracks. 350 old pairs: 12 of them move named -> standard, 12 flip to raise, 26
    change a score; 30 pairs between the same tracks are added and 30 other pairs are
    gone. All six guards trip."""
    every = _pairs(20)
    old_pairs = every[30:]
    old_index = {pair: i for i, pair in enumerate(old_pairs)}
    old = env.write_old(_grid(old_pairs, set(old_pairs[:12])))
    rows = []
    for a, b in every[:-30]:
        i = old_index.get((a, b))                    # None: an added pair
        if i is not None and 12 <= i < 24:
            rows.append(_raise_row(a, b))
        elif i is not None and 24 <= i < 50:
            rows.append(_ok_row(a, b, score=99))
        else:
            rows.append(_ok_row(a, b))
    return old, _payload(rows)


def test_every_guard_refusal_is_a_few_short_lines_even_with_all_six_guards(env, capsys):
    old, new = _all_six(env)
    shift, summary = BR.policy_shift(old, new), BR.diff_summary(old, new)
    assert _guard_names(BR.guard_trips(shift, summary)) == list(ALL_GUARDS)
    before = _snapshot(env.dir)
    facts = {"policy_shift": "12 of 320 pairs", "status_flips": "12 of 320 pairs",
             "overlap_policy_changes": "24 of 320 pairs", "pairs_changed": "50 of 320 pairs",
             "pairs_added": "30 unexplained", "pairs_removed": "30 of 350 pairs"}
    note = "a bare --accept-shift is for one guard only"
    cases = [
        ([], "refresh refused: 6 guards tripped", ALL_GUARDS, False),
        (["--accept-shift"], "refresh refused: bare --accept-shift, 6 guards tripped",
         ALL_GUARDS, True),
        (["--accept-shift", "policy_shift"], "refresh refused: not accepted: 5 guards",
         ALL_GUARDS[1:], False),
    ]
    for flag_args, first, listed, has_note in cases:
        assert env.run(["--refresh", "--reason", GOOD_REASON, *flag_args], new) == 2
        lines = _short_refusal(_messages(capsys))
        assert lines[0] == first, lines
        assert [ln for ln in lines if ln.startswith("  ")] == [
            f"  {g}: {facts[g]}" for g in listed]
        assert "--accept-shift " + ",".join(ALL_GUARDS) in lines, lines
        assert lines[-1] == "--accept-shift all", lines
        # the exact diff line: "unexplained" is pairs_added_unexplained (30), not the
        # new-track count (0, because old pairs are gone)
        assert "diff: 30 added (30 unexplained), 30 removed, 50 changed" in lines, lines
        assert (note in lines) is has_note, lines
        assert "To accept, run:" in lines and "Read the diff, then run:" not in lines
        assert _snapshot(env.dir) == before and env.sidecars() == []


def test_guard_lines_stay_short_with_all_six_names_and_5_digit_counts():
    shift = {"total_moved": 12345, "pairs_compared": 99999}
    summary = {"pairs_old": 99999, "pairs_new": 99999, "pairs_added": 55555,
               "pairs_added_new_track": 0, "pairs_added_unexplained": 55555,
               "pairs_removed": 44444, "pairs_changed": 33333,
               "changed_by_field": {"status_flip": 22222, "overlap_policy": 11111}}
    found = BR._guard_findings(shift, summary)
    assert [name for name, _, _ in found] == list(ALL_GUARDS)
    for name, text, fact in found:
        assert fact in text, "the short count must come from the same numbers as the reason"
    for plan in (("none", ()), ("bare", ()), ("names", ("policy_shift",))):
        with pytest.raises(BR.RefreshRefused) as info:
            BR._accepted_guards(plan, found, summary)
        lines = _short_refusal(str(info.value).splitlines())
        assert "  overlap_policy_changes: 11111 of 99999 pairs" in lines, lines
        assert "  pairs_added: 55555 unexplained" in lines, lines
        assert max(len(ln) for ln in lines if not ln.startswith("--accept-shift ")) <= 70


def test_the_refusal_for_one_guard_is_short_and_names_the_exact_flag(env, capsys):
    old = env.write_old(_grid(_pairs(15)[:21], []))
    assert env.run(["--refresh", "--reason", GOOD_REASON], _grid(_pairs(15), [])) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: 1 guard tripped", lines
    assert lines[1] == "  pairs_added: 189 unexplained", lines
    assert lines[-1] == "--accept-shift pairs_added" and "To accept, run:" in lines, lines
    assert not any("every guard" in ln or ln == "--accept-shift all" for ln in lines)


@pytest.mark.parametrize("value,facts", [
    ("bogus", ["  unknown name 'bogus'"]),
    ("x" * 100, ["  unknown name '" + "x" * 37 + "...'"]),
    ("all,pairs_added", ["  all cannot be mixed with names"]),
    ("", ["  an empty name"]),
    ("a,,all", ["  unknown name 'a'", "  an empty name", "  all cannot be mixed with names"]),
])
def test_a_usage_error_is_a_few_short_lines_with_the_valid_names(env, capsys, value, facts):
    env.write_old(_grid(_pairs(5), []))
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift", value],
                   _grid(_pairs(5), [])) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: bad --accept-shift value", lines
    assert lines[1:1 + len(facts)] == facts, lines
    assert lines[1 + len(facts):] == ["valid names:", *[f"  {g}" for g in ALL_GUARDS],
                                      "  all", "nothing was written"], lines


def test_the_api_type_error_is_a_few_short_lines():
    with pytest.raises(BR.RefreshRefused) as info:
        BR._accept_plan(5)
    lines = _short_refusal(str(info.value).splitlines())
    assert lines[0] == "refresh refused: bad accept_shift value"
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# Round 7: replacing a track always trips; odd names cannot break the refusal layout
# ---------------------------------------------------------------------------
def _half_rename(env, count):
    """`count` tracks. The LAST one keeps its name as an out, but as an in it gains a
    trailing space: its count-1 pairs as an in are re-keyed (all new, all raising) and
    the old ones are gone. No whole track is gone."""
    names = _track_names(count)
    victim = names[-1]
    old = env.write_old(_grid(_pairs_of(names), []))
    new = _payload([_raise_row(a, victim + " ") if b == victim else _ok_row(a, b)
                    for a, b in _pairs_of(names)])
    return old, new


@pytest.mark.parametrize("count,fact", [
    (20, "19 replaced pairs"),     # 19 of 380 removed is exactly 5 percent: not over
    (21, "20 replaced pairs"),     # 4.8 percent removed, 20 added: under every limit
    (25, "24 replaced pairs"),
    (41, "40 unexplained"),        # 40 added is over the 25 pair limit anyway
])
def test_a_half_rename_of_one_track_trips_pairs_added_at_any_corpus_size(
        env, capsys, count, fact):
    old, new = _half_rename(env, count)
    summary = BR.diff_summary(old, new)
    assert summary["pairs_added"] == summary["pairs_removed"] == count - 1
    assert summary["pairs_added_new_track"] == 0 and summary["old_tracks_gone"] == 0
    assert summary["pairs_replaced"] == count - 1
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == ["pairs_added"]
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: 1 guard tripped", lines
    assert f"  pairs_added: {fact}" in lines, lines
    assert "--accept-shift pairs_added" in lines, lines
    assert _snapshot(env.dir) == before and env.sidecars() == []
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    "pairs_added"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_accepted"] == ["pairs_added"]
    assert rec["diff_summary"]["pairs_replaced"] == count - 1
    env.write_old(old)                                   # the bare flag works for one guard
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], new) == 0


@pytest.mark.parametrize("count,expected", [
    (20, ["pairs_added", "pairs_removed"]),
    (21, ["pairs_added", "pairs_removed"]),
    (25, ["pairs_added", "pairs_removed"]),
    (41, ["pairs_added", "pairs_removed"]),    # 80 of 1640 is 4.9 percent, but the track is gone
])
def test_a_full_rename_of_one_track_trips_at_any_corpus_size(env, capsys, count, expected):
    names = _track_names(count)
    renamed = _track_names(count, replace=names[-1])
    old = env.write_old(_grid(_pairs_of(names), []))
    new = _payload([_raise_row(a, b) if renamed[-1] in (a, b) else _ok_row(a, b)
                    for a, b in _pairs_of(renamed)])
    summary = BR.diff_summary(old, new)
    assert summary["pairs_added"] == summary["pairs_removed"] == 2 * (count - 1)
    assert summary["old_tracks_gone"] == 1 and summary["pairs_replaced"] == 2 * (count - 1)
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == expected
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert f"  pairs_added: {2 * (count - 1)} unexplained" in lines, lines
    assert "  pairs_removed: 1 old track is gone" in lines, lines
    assert "--accept-shift " + ",".join(expected) in lines, lines
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    ",".join(expected)], new) == 0


def test_a_replacement_of_one_track_in_a_100_track_corpus_trips(env, capsys):
    old, new = _half_rename(env, 100)
    summary = BR.diff_summary(old, new)
    assert summary["pairs_replaced"] == 99 and summary["pairs_removed"] == 99
    # 99 of 9900 pairs is 1 percent removed: only pairs_added notices
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == ["pairs_added"]
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert "  pairs_added: 99 unexplained" in lines, lines


def test_pure_removal_follows_pairs_removed_only_and_is_not_a_replacement(env, capsys):
    names = _track_names(20)
    old = env.write_old(_grid(_pairs_of(names), []))
    new = _grid(_pairs_of(names[:-1]), [])               # the last track is gone, nothing added
    summary = BR.diff_summary(old, new)
    assert summary["pairs_added"] == 0 and summary["pairs_replaced"] == 0
    assert summary["pairs_removed"] == 38 and summary["old_tracks_gone"] == 1
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == ["pairs_removed"]
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert "  pairs_removed: 1 old track is gone" in lines, lines   # (round 8: names the track)
    assert "    38 of 380 pairs are gone (10.0%, over the 5% limit)" in lines, lines
    assert not any(ln.startswith("  pairs_added") for ln in lines), lines


def test_pairs_replaced_counts_added_pairs_with_a_new_name_only_beside_a_removal():
    abc = ["a", "b", "c"]
    old = _payload([_ok_row(a, b) for a, b in _pairs_of(abc)])
    grown = _payload([_ok_row(a, b) for a, b in _pairs_of(abc + ["d"])])
    s = BR.diff_summary(old, grown)                      # d is new and nothing is removed
    assert s["pairs_replaced"] == 0 and _counts_of_added(s) == (6, 6, 0)
    half = _payload([_ok_row(a, b) for a, b in _pairs_of(abc + ["d"]) if (a, b) != ("a", "b")])
    s = BR.diff_summary(old, half)                       # d is new and ("a", "b") is gone
    assert s["pairs_replaced"] == 6 and _counts_of_added(s) == (6, 0, 6)
    has_ab = _payload([_ok_row(a, b) for a, b in _pairs_of(abc) if (a, b) != ("b", "a")])
    has_ba = _payload([_ok_row(a, b) for a, b in _pairs_of(abc) if (a, b) != ("a", "b")])
    s = BR.diff_summary(has_ab, has_ba)                  # a pair swaps places, no new name
    assert s["pairs_removed"] == 1 and _counts_of_added(s) == (1, 0, 1)
    assert s["pairs_replaced"] == 0
    assert BR.diff_summary(None, grown)["pairs_replaced"] == 0


@pytest.mark.parametrize("name,shown", [
    ("bad" + chr(10) + "name", "bad?name"),
    ("x" + chr(9) * 35 + "y", "x" + "?" * 35 + "y"),
    ("x" + chr(9) * 60 + "y", "x" + "?" * 36 + "..."),
    ("a" + chr(27) + "[31mb", "a?[31mb"),
    ("caf" + chr(0xe9), "caf?"),
], ids=["newline", "35-tabs", "60-tabs-cut", "escape", "accent"])
def test_an_unknown_name_with_odd_characters_cannot_break_the_refusal_layout(
        env, capsys, name, shown):
    env.write_old(_grid(_pairs(5), []))
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift", name],
                   _grid(_pairs(5), [])) == 2
    got = capsys.readouterr()
    text = got.err + got.out
    assert all(ch == "\n" or " " <= ch <= "~" for ch in text), repr(text)
    lines = _short_refusal([ln for ln in text.splitlines() if ln.strip()])
    assert lines[1] == f"  unknown name '{shown}'", lines
    assert lines[2] == "valid names:", lines
    # the same through the API: _accept_plan and refresh_baseline
    for call in (lambda: BR._accept_plan([name]),
                 lambda: _refresh_api(env, _grid(_pairs(5), []), [name])):
        with pytest.raises(BR.RefreshRefused) as info:
            call()
        api_lines = _short_refusal(str(info.value).splitlines())
        assert api_lines[1] == f"  unknown name '{shown}'", api_lines
    assert env.sidecars() == []


# ---------------------------------------------------------------------------
# Round 8: removing a track always trips pairs_removed (Sam, 2026-11-05: "Yes, always warn")
# ---------------------------------------------------------------------------
def _drop_tracks(env, count, drop):
    """`count` tracks with every ordered pair, written as the old baseline. Returns (old,
    new): new is the same grid without the last `drop` tracks, so every pair of those
    tracks is gone and nothing else changes."""
    names = _track_names(count)
    old = env.write_old(_grid(_pairs_of(names), []))
    return old, _grid(_pairs_of(names[:count - drop]), [])


@pytest.mark.parametrize("flag_args", [["--accept-shift", "pairs_removed"], ["--accept-shift"]],
                         ids=["named", "bare"])
@pytest.mark.parametrize("count,share", [
    (50, "98 of 2450 pairs are gone (4.0%)"),       # 4.0 percent: under the limit, passed before
    (100, "198 of 9900 pairs are gone (2.0%)"),     # 2.0 percent: under the limit, passed before
    (21, "40 of 420 pairs are gone (9.5%, over the 5% limit)"),  # over the limit: tripped before too
])
def test_dropping_one_track_trips_pairs_removed_and_the_flag_accepts_it(
        env, capsys, count, share, flag_args):
    old, new = _drop_tracks(env, count, 1)
    summary = BR.diff_summary(old, new)
    assert summary["pairs_removed"] == 2 * (count - 1) and summary["old_tracks_gone"] == 1
    assert summary["pairs_added"] == 0 and summary["pairs_changed"] == 0
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == ["pairs_removed"]
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: 1 guard tripped", lines
    at = lines.index("  pairs_removed: 1 old track is gone")
    assert lines[at + 1] == "    " + share, lines
    assert lines[-2:] == ["To accept, run:", "--accept-shift pairs_removed"], lines
    assert _snapshot(env.dir) == before and env.sidecars() == []
    assert env.run(["--refresh", "--reason", GOOD_REASON, *flag_args], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_tripped_names"] == ["pairs_removed"]
    assert rec["guards_accepted"] == ["pairs_removed"]
    assert json.loads(env.baseline.read_bytes()) == new


@pytest.mark.parametrize("count,share", [
    (100, "394 of 9900 pairs are gone (4.0%)"),
    (50, "194 of 2450 pairs are gone (7.9%, over the 5% limit)"),
])
def test_dropping_two_tracks_is_one_pairs_removed_entry_and_the_count_says_2(
        env, capsys, count, share):
    old, new = _drop_tracks(env, count, 2)
    summary = BR.diff_summary(old, new)
    assert summary["old_tracks_gone"] == 2
    trips = BR.guard_trips(BR.policy_shift(old, new), summary)
    assert _guard_names(trips) == ["pairs_removed"], "one entry, not one per track"
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: 1 guard tripped", lines
    at = lines.index("  pairs_removed: 2 old tracks are gone")
    assert lines[at + 1] == "    " + share, lines
    assert sum(1 for ln in lines if ln.startswith("  pairs_removed")) == 1, lines
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    "pairs_removed"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_tripped_names"] == ["pairs_removed"]
    assert rec["guards_accepted"] == ["pairs_removed"]
    assert rec["diff_summary"]["old_tracks_gone"] == 2


def test_pairs_removed_among_tracks_that_remain_keep_the_5_percent_rule(env, capsys):
    old = env.write_old(_grid(_pairs_of(_track_names(50)), []))      # 2450 pairs
    rows = old["rows"]
    # the first pairs go: every track stays as an in, so no track is gone. 5 percent of
    # 2450 is 122.5: 1 and 122 pairs pass as before, 123 trips with its old one line
    for gone_pairs, tripped in ((1, False), (122, False), (123, True)):
        env.write_old(old)
        new = _payload(rows[gone_pairs:])
        summary = BR.diff_summary(old, new)
        assert summary["pairs_removed"] == gone_pairs and summary["old_tracks_gone"] == 0
        names = _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary))
        assert names == (["pairs_removed"] if tripped else []), gone_pairs
        before = _snapshot(env.dir)
        rc = env.run(["--refresh", "--reason", GOOD_REASON], new)
        if tripped:
            assert rc == 2 and _snapshot(env.dir) == before
            lines = _short_refusal(_messages(capsys))
            assert "  pairs_removed: 123 of 2450 pairs" in lines, lines
            assert not any("old track" in ln for ln in lines), lines
        else:
            assert rc == 0, gone_pairs
            _messages(capsys)


def test_the_full_replacement_of_one_track_in_a_100_track_corpus_trips_added_and_removed(
        env, capsys):
    names = _track_names(100)
    old = env.write_old(_grid(_pairs_of(names), []))
    new = _grid(_pairs_of(_track_names(100, replace="T50")), [])
    summary = BR.diff_summary(old, new)
    assert summary["pairs_added"] == summary["pairs_removed"] == 198
    assert summary["old_tracks_gone"] == 1 and summary["pairs_replaced"] == 198
    # 198 of 9900 is 2 percent: only the old track being gone makes pairs_removed trip
    assert _guard_names(BR.guard_trips(BR.policy_shift(old, new), summary)) == [
        "pairs_added", "pairs_removed"]
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: 2 guards tripped", lines
    assert "  pairs_added: 198 unexplained" in lines, lines
    assert "  pairs_removed: 1 old track is gone" in lines, lines
    assert "    198 of 9900 pairs are gone (2.0%)" in lines, lines
    assert "--accept-shift pairs_added,pairs_removed" in lines, lines
    assert "--accept-shift all" in lines, lines
    # the bare flag is for one guard only
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift"], new) == 2
    lines = _short_refusal(_messages(capsys))
    assert lines[0] == "refresh refused: bare --accept-shift, 2 guards tripped", lines
    # naming one guard is not enough: the other is named in the refusal
    for given, missing in (("pairs_added", ["  pairs_removed: 1 old track is gone",
                                            "    198 of 9900 pairs are gone (2.0%)"]),
                           ("pairs_removed", ["  pairs_added: 198 unexplained"])):
        assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift", given],
                       new) == 2
        lines = _short_refusal(_messages(capsys))
        assert lines[0] == "refresh refused: not accepted: 1 guard", lines
        assert [ln for ln in lines if ln.startswith("  ")] == missing, lines
        assert "--accept-shift pairs_added,pairs_removed" in lines, lines
    assert _snapshot(env.dir) == before and env.sidecars() == []
    assert env.run(["--refresh", "--reason", GOOD_REASON, "--accept-shift",
                    "pairs_added,pairs_removed"], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_accepted"] == ["pairs_added", "pairs_removed"]
    assert json.loads(env.baseline.read_bytes()) == new


def test_the_very_first_baseline_is_never_tripped_by_the_removed_track_rule(env):
    new = _grid(_pairs_of(_track_names(5)), [])
    summary = BR.diff_summary(None, new)
    assert summary["old_tracks_gone"] == 0 and summary["pairs_removed"] == 0
    assert BR.guard_trips(BR.policy_shift(None, new), summary) == []
    # even a summary that claims tracks are gone, with no old pairs, is a first baseline
    claims = dict(summary, old_tracks_gone=3)
    assert BR.guard_trips(BR.policy_shift(None, new), claims) == []
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0     # no baseline on disk
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["old_baseline_sha256"] == "missing" and rec["guards_tripped"] == []
    # an old baseline with no rows at all has no track to lose either
    assert BR.diff_summary(_payload([]), new)["old_tracks_gone"] == 0
    env.write_old(_payload([]))
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 0


def test_a_dropped_track_with_odd_characters_in_its_name_cannot_break_the_refusal(env, capsys):
    # the refusal names no track, only counts, so no name can add a line or widen one
    odd = "T" + chr(10) + "x" + chr(9) + "y" + chr(27) + "[31m" + chr(0xe9)
    names = _track_names(49)
    old = env.write_old(_grid(_pairs_of(names + [odd]), []))
    new = _grid(_pairs_of(names), [])
    assert BR.diff_summary(old, new)["old_tracks_gone"] == 1
    plain = ["refresh refused: 1 guard tripped",
             "  pairs_removed: 1 old track is gone",
             "    98 of 2450 pairs are gone (4.0%)",
             "diff: 0 added (0 unexplained), 98 removed, 0 changed",
             "nothing was written", "To accept, run:", "--accept-shift pairs_removed"]
    before = _snapshot(env.dir)
    assert env.run(["--refresh", "--reason", GOOD_REASON], new) == 2
    got = capsys.readouterr()
    text = got.err + got.out
    assert all(ch == "\n" or " " <= ch <= "~" for ch in text), repr(text)
    assert _short_refusal([ln for ln in text.splitlines() if ln.strip()]) == plain
    with pytest.raises(BR.RefreshRefused) as info:
        _refresh_api(env, new, False)
    assert _short_refusal(str(info.value).splitlines()) == plain
    assert _snapshot(env.dir) == before and env.sidecars() == []


@pytest.mark.parametrize("flag_args,accepted", [
    (["--accept-shift", "pairs_removed"], ["pairs_removed"]),
    (["--accept-shift"], ["pairs_removed"]),
    (["--accept-shift", "all"], ["all"]),
], ids=["named", "bare", "all"])
def test_the_record_says_pairs_removed_was_accepted_for_a_dropped_track(
        env, capsys, flag_args, accepted):
    old, new = _drop_tracks(env, 50, 1)
    assert env.run(["--refresh", "--reason", GOOD_REASON, *flag_args], new) == 0
    rec = json.loads(env.sidecars()[0].read_bytes())
    assert rec["guards_tripped_names"] == ["pairs_removed"]
    assert rec["guards_accepted"] == accepted
    assert rec["accepted_shift"] is True and rec["accept_shift_flag"] is True
    assert rec["guards_tripped"][0].startswith("pairs_removed: 1 old track is gone;")
    assert rec["diff_summary"]["old_tracks_gone"] == 1
    assert rec["diff_summary"]["pairs_removed"] == 98
    assert json.loads(env.baseline.read_bytes()) == new
    lines = _messages(capsys)
    if accepted == ["all"]:
        assert any("all guards accepted" in ln for ln in lines), lines
    else:
        assert "guards accepted: pairs_removed" in lines, lines


@pytest.mark.parametrize("gone,removed,old_pairs,fact", [
    (0, 5, 100, None),                  # exactly 5 percent, no track gone: no trip
    (0, 6, 100, "6 of 100 pairs"),      # over the limit only: the old one line
    (1, 2, 100, "1 old track is gone\n2 of 100 pairs are gone (2.0%)"),
    (1, 5, 100, "1 old track is gone\n5 of 100 pairs are gone (5.0%)"),
    (1, 6, 100, "1 old track is gone\n6 of 100 pairs are gone (6.0%, over the 5% limit)"),
    (3, 12, 100, "3 old tracks are gone\n12 of 100 pairs are gone (12.0%, over the 5% limit)"),
    (2, 0, 0, None),                    # no old pairs: the first baseline, nothing to lose
])
def test_the_removed_track_guard_edges(gone, removed, old_pairs, fact):
    shift = {"total_moved": 0, "pairs_compared": old_pairs}
    summary = {"pairs_old": old_pairs, "pairs_removed": removed, "old_tracks_gone": gone}
    found = BR._guard_findings(shift, summary)
    if fact is None:
        assert found == []
        return
    assert [name for name, _, _ in found] == ["pairs_removed"], "one entry"
    (_, text, got), = found
    assert got == fact
    assert all(line in text for line in fact.split("\n")), (text, fact)
    assert BR.guard_trips(shift, summary) == [text]
    if gone == 1 and removed == 2:
        assert text == ("pairs_removed: 1 old track is gone; 2 of 100 pairs are gone (2.0%); "
                        "removing a track always trips, whatever the percentage")


def test_the_removed_track_lines_stay_short_with_5_digit_counts():
    shift = {"total_moved": 12345, "pairs_compared": 99999}
    summary = {"pairs_old": 99999, "pairs_new": 99999, "pairs_added": 55555,
               "pairs_added_new_track": 0, "pairs_added_unexplained": 55555,
               "pairs_removed": 44444, "pairs_changed": 33333, "old_tracks_gone": 12345,
               "changed_by_field": {"status_flip": 22222, "overlap_policy": 11111}}
    found = BR._guard_findings(shift, summary)
    assert [name for name, _, _ in found] == list(ALL_GUARDS)
    for plan in (("none", ()), ("bare", ()), ("names", ("policy_shift",))):
        with pytest.raises(BR.RefreshRefused) as info:
            BR._accepted_guards(plan, found, summary)
        lines = _short_refusal(str(info.value).splitlines())
        assert "  pairs_removed: 12345 old tracks are gone" in lines, lines
        assert "    44444 of 99999 pairs are gone (44.4%, over the 5% limit)" in lines, lines
        assert max(len(ln) for ln in lines if not ln.startswith("--accept-shift ")) <= 70


def test_cli_dropping_one_of_50_tracks_is_refused_then_accepted_by_name(tmp_path):
    s = Scratch(tmp_path, n_tracks=50)
    first = s.refresh("--reason", "bootstrap the synthetic baseline")
    assert first.returncode == 0, first.stderr + first.stdout
    assert json.loads(s.baseline.read_bytes())["n_pairs"] == 2450
    (s.stems / "SECTIONS_STEM_T49.json").unlink()
    before = s.tree()
    refused = s.refresh("--reason", "one track left the corpus, read through")
    assert refused.returncode == 2 and s.tree() == before
    lines = [ln for ln in (refused.stderr + refused.stdout).splitlines() if ln.strip()]
    _short_refusal(lines)
    assert lines[0] == "refresh refused: 1 guard tripped", lines
    assert "  pairs_removed: 1 old track is gone" in lines, lines
    assert "    98 of 2450 pairs are gone (4.0%)" in lines, lines
    assert lines[-1] == "--accept-shift pairs_removed", lines
    done = s.refresh("--reason", "one track left the corpus, accepted by name",
                     "--accept-shift", "pairs_removed")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "guards accepted: pairs_removed" in done.stdout, done.stdout
    assert json.loads(s.baseline.read_bytes())["n_pairs"] == 2352
    accepted = [json.loads(p.read_bytes()) for p in s.sidecars()]
    accepted = [r for r in accepted if r["guards_accepted"]]
    assert len(accepted) == 1 and accepted[0]["guards_accepted"] == ["pairs_removed"]


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
