"""The Home PC memory guard: a gate in code, and a no-op everywhere else.

Sam's rule (2026-11-22): "as long as it only effects this pc, we don't need it on the
work pc". So on every machine other than CARILLONAC-1-PC the guard must do NOTHING:
no preflight, no priority change, no thread cap, no supervision, no extra output, and
the commands must keep their own exit codes.

These tests never create real memory pressure: the memory readings are mocked and the
machine name is mocked too.
"""
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Source"))

import psutil

import memory_guard


def _import_orchestrator():
    """The orchestrator module.

    It imports the sibling Audio Analysis Toolkit (audio_analysis), which lives
    in a separate repo and is not installed everywhere. The guard tests never
    call into the analysis code, so a stub stands in for the toolkit when it is
    missing.
    """
    try:
        from automated_dj_mixes import orchestrator
        return orchestrator
    except ModuleNotFoundError as exc:
        if exc.name != "audio_analysis":
            raise
    stub = types.ModuleType("audio_analysis")
    track = types.ModuleType("audio_analysis.track_analysis")
    for name in ("analyse_track", "analyse_folder", "_read_tags", "_detect_downbeat",
                 "_refine_attack", "_measure_lufs", "_detect_sections", "_detect_last_kick",
                 "_detect_bass_section", "_detect_first_break_phrase_aware"):
        setattr(track, name, lambda *args, **kwargs: None)
    track.TrackAnalysis = type("TrackAnalysis", (), {})
    track.AUDIO_EXTENSIONS = (".wav",)
    stub.track_analysis = track
    sys.modules["audio_analysis"] = stub
    sys.modules["audio_analysis.track_analysis"] = track
    from automated_dj_mixes import orchestrator
    return orchestrator


orchestrator = _import_orchestrator()

GUARD = ROOT / "Source" / "memory_guard.py"
PIPELINE = ROOT / "Tools" / "run_mix_pipeline.sh"
HOME_PC = "CARILLONAC-1-PC"
WORK_PC = "STUDIO-2"

GB = 2 ** 30

# The child the supervisor runs. It counts its runs in a counter file, optionally
# grows the resume folder, optionally spawns a grandchild (to check the tree kill)
# and then exits with the given code.
CHILD = (
    "import subprocess, sys, time\n"
    "counter = sys.argv[1]\n"
    "mode = sys.argv[2]\n"
    "cache = sys.argv[3]\n"
    "sleep_s = float(sys.argv[4])\n"
    "sleep_until = int(sys.argv[5])\n"
    "code = int(sys.argv[6])\n"
    "grand_pid_file = sys.argv[7] if len(sys.argv) > 7 else ''\n"
    "try:\n"
    "    seen = len(open(counter).read().split())\n"
    "except OSError:\n"
    "    seen = 0\n"
    "if grand_pid_file and seen == 0:\n"
    "    grand = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
    "    open(grand_pid_file, 'w').write(str(grand.pid))\n"
    "open(counter, 'a').write('run\\n')\n"
    "if mode == 'grow' or (mode == 'grow-later' and seen > 0):\n"
    "    open(cache, 'a').write('cache\\n')\n"
    "if seen < sleep_until:\n"
    "    time.sleep(sleep_s)\n"
    "sys.exit(code)\n"
)


class FakeMemory:
    def __init__(self, available):
        self.available = available


class FakeProcess:
    def __init__(self, name, pid, rss):
        self.info = {"name": name, "pid": pid, "memory_info": SimpleNamespace(rss=rss)}


class FakePsutil:
    """psutil stand-in: the memory readings are ours, the process tree stays real.

    A collapse (a reading below the default) is held back until the child has
    written its counter, so the kill never races the child's start-up.
    """

    NoSuchProcess = psutil.NoSuchProcess

    def __init__(self, available_values, processes=(), default=8 * GB, ready_path=None):
        self._available = list(available_values)
        self._processes = list(processes)
        self._default = default
        self._ready_path = ready_path
        self._released = 0

    def virtual_memory(self):
        value = self._available[0] if self._available else self._default
        if value < self._default and not self._child_is_up():
            return FakeMemory(self._default)
        if self._available:
            self._available.pop(0)
        return FakeMemory(value)

    def _child_is_up(self):
        """True once the child has written its counter for this attempt."""
        if self._ready_path is None:
            return True
        try:
            runs = len(self._ready_path.read_text().split())
        except OSError:
            return False
        if runs < self._released + 1:
            return False
        self._released += 1
        return True

    def process_iter(self, attrs=None):
        return list(self._processes)

    def Process(self, pid):
        return psutil.Process(pid)


def _child(counter, mode, cache, sleep_s=3.0, sleep_until=2, code=0, grand_pid_file=None):
    """The command line for the supervised child."""
    cmd = [sys.executable, "-c", CHILD, str(counter), mode, str(cache),
           str(sleep_s), str(sleep_until), str(code)]
    if grand_pid_file is not None:
        cmd.append(str(grand_pid_file))
    return cmd


# --------------------------------------------------------------------------
# The guard is off everywhere except the Home PC
# --------------------------------------------------------------------------

@pytest.mark.parametrize("machine", [WORK_PC, "studio-2", "not-the-home-pc"])
def test_guard_is_inactive_off_the_home_pc(monkeypatch, machine):
    monkeypatch.setenv("COMPUTERNAME", machine)
    monkeypatch.delenv("MEMORY_GUARD", raising=False)
    assert memory_guard.guard_active() is False


def test_guard_is_inactive_without_a_machine_name(monkeypatch):
    monkeypatch.delenv("COMPUTERNAME", raising=False)
    monkeypatch.delenv("MEMORY_GUARD", raising=False)
    assert memory_guard.guard_active() is False


def test_guard_is_active_on_the_home_pc(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", HOME_PC)
    monkeypatch.delenv("MEMORY_GUARD", raising=False)
    assert memory_guard.guard_active() is True


def test_memory_guard_off_disables_the_guard_on_the_home_pc(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", HOME_PC)
    monkeypatch.setenv("MEMORY_GUARD", "off")
    assert memory_guard.guard_active() is False


def test_memory_guard_on_forces_the_guard_anywhere(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", WORK_PC)
    monkeypatch.setenv("MEMORY_GUARD", "on")
    assert memory_guard.guard_active() is True


# --------------------------------------------------------------------------
# Off the Home PC the CLI is invisible: the command runs as it always did
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code", [0, 5])
def test_cli_off_the_home_pc_runs_the_command_directly(code):
    env = dict(os.environ, COMPUTERNAME=WORK_PC)
    env.pop("MEMORY_GUARD", None)
    child = "import sys; print('child ran'); sys.exit(int(sys.argv[1]))"
    result = subprocess.run([sys.executable, str(GUARD), "--", sys.executable, "-c", child,
                             str(code)], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == code
    assert result.stdout == "child ran\n"
    assert result.stderr == ""


def test_cli_preflight_only_is_a_no_op_off_the_home_pc():
    env = dict(os.environ, COMPUTERNAME=WORK_PC)
    env.pop("MEMORY_GUARD", None)
    result = subprocess.run([sys.executable, str(GUARD), "--preflight-only"], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_orchestrator_does_not_relaunch_off_the_home_pc(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPUTERNAME", WORK_PC)
    monkeypatch.delenv("MEMORY_GUARD", raising=False)
    monkeypatch.delenv("MEMORY_GUARD_CHILD", raising=False)
    supervise = Mock(return_value=0)
    preflight = Mock(return_value=True)
    monkeypatch.setattr(memory_guard, "supervise", supervise)
    monkeypatch.setattr(memory_guard, "preflight", preflight)
    pipeline = Mock(return_value=None)
    monkeypatch.setattr(orchestrator, "run_pipeline", pipeline)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--input", str(tmp_path / "Audio"),
                                      "--output", str(tmp_path / "Output")])
    orchestrator.main()
    supervise.assert_not_called()
    preflight.assert_not_called()
    pipeline.assert_called_once()


# --------------------------------------------------------------------------
# The preflight refuses to start a run that will not fit
# --------------------------------------------------------------------------

def test_preflight_refuses_below_the_physical_floor(monkeypatch, capsys):
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([3 * GB]))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 12 * GB)
    assert memory_guard.preflight(7.0) is False
    out = capsys.readouterr().out
    assert "REFUSING TO START" in out
    assert "3.0 GB" in out
    assert "Chrome" in out
    assert "Dropbox" in out


def test_preflight_refuses_below_the_commit_floor(monkeypatch, capsys):
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([12 * GB]))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 2 * GB)
    assert memory_guard.preflight(7.0) is False
    out = capsys.readouterr().out
    assert "REFUSING TO START" in out
    assert "commit headroom" in out
    assert "2.0 GB" in out


def test_preflight_passes_above_both_floors(monkeypatch, capsys):
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([9 * GB]))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 12 * GB)
    assert memory_guard.preflight(7.0) is True
    out = capsys.readouterr().out
    assert "REFUSING TO START" not in out
    assert "preflight OK" in out


def test_preflight_prints_the_five_largest_processes(monkeypatch, capsys):
    processes = [FakeProcess(f"proc{index}", 1000 + index, (10 - index) * 2 ** 20)
                 for index in range(8)]
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([12 * GB], processes))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 12 * GB)
    assert memory_guard.preflight(7.0) is True
    lines = [line for line in capsys.readouterr().out.splitlines() if " pid=" in line]
    assert len(lines) == 5
    assert "proc0" in lines[0]
    assert "rss=" in lines[0]
    assert " MB" in lines[0]


def test_the_commit_headroom_is_scaled_by_the_page_size():
    # The PERFORMANCE_INFORMATION counters are in pages, not bytes.
    assert memory_guard._commit_headroom(100, 200, 4096) == 409600
    assert memory_guard._commit_headroom(200, 100, 4096) == 0
    assert memory_guard._commit_headroom(100, 200, 0) == 0


def test_the_start_floor_is_configurable(monkeypatch):
    monkeypatch.delenv(memory_guard.MIN_START_GB_ENV, raising=False)
    assert memory_guard.default_min_start_gb() == 7.0
    monkeypatch.setenv(memory_guard.MIN_START_GB_ENV, "4.5")
    assert memory_guard.default_min_start_gb() == 4.5


# --------------------------------------------------------------------------
# The floor depends on the WORK: a fresh separation needs 7.0 GB, a run that
# re-lays cached analysis needs 2.0 GB
# --------------------------------------------------------------------------

def _make_project(tmp_path, count=9, cached=9):
    """A project folder: <project>/Audio/*.wav + <project>/_Stem Analysis/.

    `cached` tracks get a finished SECTIONS_STEM_<track>.json (the file the
    orchestrator writes and reads back), the rest have none: those would be
    separated from scratch.
    """
    project = tmp_path / "Mix"
    audio = project / "Audio"
    audio.mkdir(parents=True)
    stem_dir = project / "_Stem Analysis"
    stem_dir.mkdir()
    for index in range(count):
        name = f"Track {index + 1} SW V1"
        (audio / f"{name}.wav").write_bytes(b"")
        if index < cached:
            analysis = {"track": name, "bpm": 124.0, "n_bars": 40,
                        "sections": [{"start_sec": 0.0, "end_sec": 10.0}]}
            (stem_dir / f"SECTIONS_STEM_{name}.json").write_text(
                json.dumps(analysis), encoding="ascii")
    return project, audio


def test_all_tracks_cached_uses_the_light_floor_and_passes_at_3_9_gb(monkeypatch, capsys, tmp_path):
    _project, audio = _make_project(tmp_path, count=9, cached=9)
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([int(3.9 * GB)]))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 12 * GB)
    assert memory_guard.preflight(input_dir=audio) is True
    out = capsys.readouterr().out
    assert "2.0 GB floor: all 9 tracks cached" in out
    assert "REFUSING TO START" not in out


def test_one_uncached_track_uses_the_heavy_floor_and_refuses_at_3_9_gb(monkeypatch, capsys, tmp_path):
    _project, audio = _make_project(tmp_path, count=9, cached=8)
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([int(3.9 * GB)]))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 12 * GB)
    assert memory_guard.preflight(input_dir=audio) is False
    out = capsys.readouterr().out
    assert "7.0 GB floor: 1 of 9 tracks need a fresh separation" in out
    assert "REFUSING TO START" in out


def test_a_corrupt_cache_counts_as_a_track_that_needs_a_separation(monkeypatch, capsys, tmp_path):
    project, audio = _make_project(tmp_path, count=2, cached=2)
    (project / "_Stem Analysis" / "SECTIONS_STEM_Track 1 SW V1.json").write_text(
        "{not json", encoding="ascii")
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([9 * GB]))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 12 * GB)
    assert memory_guard.preflight(input_dir=audio) is True
    out = capsys.readouterr().out
    assert "7.0 GB floor: 1 of 2 tracks need a fresh separation" in out


def test_the_cli_picks_the_floor_from_the_input_folder(monkeypatch, capsys, tmp_path):
    _project, audio = _make_project(tmp_path, count=9, cached=9)
    monkeypatch.setenv("COMPUTERNAME", HOME_PC)
    monkeypatch.delenv("MEMORY_GUARD", raising=False)
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([int(3.9 * GB)]))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 12 * GB)
    assert memory_guard.main(["--preflight-only", "--input", str(audio)]) == 0
    out = capsys.readouterr().out
    assert "2.0 GB floor: all 9 tracks cached" in out


def test_the_env_overrides_the_heavy_floor(monkeypatch, capsys, tmp_path):
    _project, audio = _make_project(tmp_path, count=9, cached=8)
    monkeypatch.setenv(memory_guard.MIN_START_GB_ENV, "4.5")
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([5 * GB]))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 12 * GB)
    assert memory_guard.preflight(input_dir=audio) is True
    out = capsys.readouterr().out
    assert "4.5 GB floor: 1 of 9 tracks need a fresh separation" in out


def test_the_env_overrides_the_light_floor(monkeypatch, capsys, tmp_path):
    _project, audio = _make_project(tmp_path, count=9, cached=9)
    monkeypatch.setenv(memory_guard.MIN_START_LIGHT_GB_ENV, "1.0")
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([int(1.2 * GB)]))
    monkeypatch.setattr(memory_guard, "commit_headroom_bytes", lambda: 12 * GB)
    assert memory_guard.preflight(input_dir=audio) is True
    out = capsys.readouterr().out
    assert "1.0 GB floor: all 9 tracks cached" in out


def _pipeline_guard_args():
    """The memory-guard command the pipeline runs, as the shell sees it."""
    text = PIPELINE.read_text(encoding="utf-8")
    match = re.search(r"python\s+Source/memory_guard\.py\s+([^\n|]+)", text)
    assert match, "the pipeline must run the memory guard before its first step"
    return match.group(1).split()


def _stub_psutil_env(tmp_path, available_gb):
    """An environment whose python reports a fixed amount of free memory.

    sitecustomize is imported by every python process, so the stub is in place
    before the guard's own imports run.
    """
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "sitecustomize.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'Source')!r})\n"
        "import psutil\n"
        "class _Memory:\n"
        f"    available = int({available_gb} * {GB})\n"
        "psutil.virtual_memory = lambda: _Memory()\n"
        "import memory_guard\n"
        f"memory_guard.commit_headroom_bytes = lambda: {12 * GB}\n",
        encoding="ascii")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(stub)] + [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part])
    env["COMPUTERNAME"] = HOME_PC
    env.pop("MEMORY_GUARD", None)
    return env


def test_the_pipeline_preflight_call_passes_at_3_9_gb(tmp_path):
    args = _pipeline_guard_args()
    env = _stub_psutil_env(tmp_path, 3.9)
    result = subprocess.run([sys.executable, str(GUARD), *args], env=env,
                            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2.0 GB floor" in result.stdout


def test_the_heavy_floor_still_refuses_a_run_at_1_5_gb(tmp_path):
    """The heavy floor still protects the pipeline: it is the orchestrator's job."""
    env = _stub_psutil_env(tmp_path, 1.5)
    result = subprocess.run([sys.executable, str(GUARD), "--preflight-only"], env=env,
                            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert result.returncode == memory_guard.EXIT_REFUSED
    assert "7.0 GB floor" in result.stdout


def test_the_cli_with_an_input_folder_is_a_no_op_off_the_home_pc(tmp_path):
    env = dict(os.environ, COMPUTERNAME=WORK_PC)
    env.pop("MEMORY_GUARD", None)
    result = subprocess.run([sys.executable, str(GUARD), "--preflight-only",
                             "--input", str(tmp_path)],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


# --------------------------------------------------------------------------
# The supervisor kills the tree, restarts when the cache grew, gives up otherwise
# --------------------------------------------------------------------------

def test_supervise_restarts_when_the_cache_grew_since_the_last_kill(tmp_path, monkeypatch, capsys):
    resume = tmp_path / "_Stem Analysis"
    resume.mkdir()
    counter = tmp_path / "runs.txt"
    # free memory: fine, fine, then a collapse (kill 1), fine, fine, then a
    # collapse again (kill 2). The child only grows the cache on its second run,
    # so the cache grew between the two kills.
    monkeypatch.setattr(memory_guard, "psutil",
                        FakePsutil([8 * GB, 8 * GB, 0.4 * GB, 8 * GB, 8 * GB, 0.4 * GB],
                                   ready_path=counter))
    code = memory_guard.supervise(
        _child(counter, "grow-later", resume / "cache.json"), resume_dir=resume,
        min_free_gb=1.5, max_attempts=6, poll_seconds=0.05)
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("GUARD KILL") == 2
    assert out.count("RESTART") == 2
    assert "GIVE UP" not in out
    assert (resume / "cache.json").is_file()


def test_supervise_gives_up_with_exit_3_when_the_cache_did_not_grow(tmp_path, monkeypatch, capsys):
    resume = tmp_path / "_Stem Analysis"
    resume.mkdir()
    counter = tmp_path / "runs.txt"
    monkeypatch.setattr(memory_guard, "psutil",
                        FakePsutil([8 * GB, 8 * GB, 0.4 * GB, 8 * GB, 8 * GB, 0.4 * GB],
                                   ready_path=counter))
    code = memory_guard.supervise(
        _child(counter, "nogrow", resume / "cache.json"), resume_dir=resume,
        min_free_gb=1.5, max_attempts=6, poll_seconds=0.05)
    out = capsys.readouterr().out
    assert code == memory_guard.EXIT_GUARD_GIVE_UP == 3
    assert out.count("GUARD KILL") == 2
    assert "no new cache file since the last kill" in out
    assert not list(resume.glob("*"))


def test_supervise_passes_the_child_exit_code_through(tmp_path, monkeypatch, capsys):
    counter = tmp_path / "runs.txt"
    monkeypatch.setattr(memory_guard, "psutil", FakePsutil([8 * GB]))
    code = memory_guard.supervise(
        _child(counter, "nogrow", tmp_path / "cache.json", sleep_s=0.2, sleep_until=1, code=7),
        resume_dir=tmp_path, min_free_gb=1.5, max_attempts=6, poll_seconds=0.05)
    out = capsys.readouterr().out
    assert code == 7
    assert "GUARD KILL" not in out


def test_supervise_kills_the_grandchild_too(tmp_path, monkeypatch):
    resume = tmp_path / "_Stem Analysis"
    resume.mkdir()
    counter = tmp_path / "runs.txt"
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setattr(memory_guard, "psutil",
                        FakePsutil([8 * GB, 8 * GB, 0.4 * GB], ready_path=counter))
    memory_guard.supervise(
        _child(counter, "nogrow", resume / "cache.json", grand_pid_file=pid_file),
        resume_dir=resume, min_free_gb=1.5, max_attempts=6, poll_seconds=0.05)
    assert pid_file.is_file()
    pid = int(pid_file.read_text().strip())
    for _ in range(40):
        if not psutil.pid_exists(pid):
            break
        time.sleep(0.05)
    assert not psutil.pid_exists(pid)


# --------------------------------------------------------------------------
# The orchestrator re-launches itself under the guard on the Home PC
# --------------------------------------------------------------------------

def test_orchestrator_relaunches_under_the_guard_on_the_home_pc(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPUTERNAME", HOME_PC)
    monkeypatch.setenv("MEMORY_GUARD", "on")
    monkeypatch.delenv("MEMORY_GUARD_CHILD", raising=False)
    seen = {}

    def fake_supervise(cmd, resume_dir=None, env=None, **kwargs):
        seen["cmd"] = cmd
        seen["resume_dir"] = resume_dir
        seen["env"] = env
        return 7

    monkeypatch.setattr(memory_guard, "preflight", lambda *args, **kwargs: True)
    monkeypatch.setattr(memory_guard, "supervise", fake_supervise)
    monkeypatch.setattr(orchestrator, "run_pipeline", Mock(return_value=None))
    audio = tmp_path / "Audio"
    argv = ["orchestrator", "--input", str(audio), "--output", str(tmp_path / "Output"),
            "--sections-layout", "--stem-sections", "--stem-grid"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        orchestrator.main()
    assert exc.value.code == 7
    assert seen["cmd"] == [sys.executable, "-m", "automated_dj_mixes.orchestrator", *argv[1:]]
    assert seen["env"]["MEMORY_GUARD_CHILD"] == "1"
    assert seen["resume_dir"] == audio.parent / "_Stem Analysis"


def test_orchestrator_does_not_relaunch_the_guarded_child(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPUTERNAME", HOME_PC)
    monkeypatch.setenv("MEMORY_GUARD", "on")
    monkeypatch.setenv("MEMORY_GUARD_CHILD", "1")
    supervise = Mock(return_value=0)
    preflight = Mock(return_value=True)
    monkeypatch.setattr(memory_guard, "supervise", supervise)
    monkeypatch.setattr(memory_guard, "preflight", preflight)
    monkeypatch.setattr(orchestrator, "run_pipeline", Mock(return_value=None))
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--input", str(tmp_path / "Audio"),
                                      "--output", str(tmp_path / "Output")])
    orchestrator.main()
    supervise.assert_not_called()
    preflight.assert_not_called()


def test_orchestrator_refuses_to_start_when_the_preflight_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPUTERNAME", HOME_PC)
    monkeypatch.setenv("MEMORY_GUARD", "on")
    monkeypatch.delenv("MEMORY_GUARD_CHILD", raising=False)
    supervise = Mock(return_value=0)
    monkeypatch.setattr(memory_guard, "preflight", lambda *args, **kwargs: False)
    monkeypatch.setattr(memory_guard, "supervise", supervise)
    monkeypatch.setattr(orchestrator, "run_pipeline", Mock(return_value=None))
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--input", str(tmp_path / "Audio"),
                                      "--output", str(tmp_path / "Output")])
    with pytest.raises(SystemExit) as exc:
        orchestrator.main()
    assert exc.value.code == memory_guard.EXIT_REFUSED
    supervise.assert_not_called()


def test_the_orchestrator_preflight_knows_the_input_folder(monkeypatch, tmp_path):
    """The orchestrator's own guard must see the cache state of --input.

    It is the check that knows whether the re-lay will separate a track, so
    it is the one that applies the heavy floor when it will.
    """
    monkeypatch.setenv("COMPUTERNAME", HOME_PC)
    monkeypatch.setenv("MEMORY_GUARD", "on")
    monkeypatch.delenv("MEMORY_GUARD_CHILD", raising=False)
    seen = {}

    def fake_preflight(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return False

    monkeypatch.setattr(memory_guard, "preflight", fake_preflight)
    monkeypatch.setattr(memory_guard, "supervise", Mock(return_value=0))
    monkeypatch.setattr(orchestrator, "run_pipeline", Mock(return_value=None))
    audio = tmp_path / "Audio"
    audio.mkdir()
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--input", str(audio),
                                      "--output", str(tmp_path / "Output")])
    with pytest.raises(SystemExit) as exc:
        orchestrator.main()
    assert exc.value.code == memory_guard.EXIT_REFUSED
    assert seen["kwargs"].get("input_dir") == audio
