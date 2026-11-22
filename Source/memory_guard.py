"""Memory guard for Sam's Home PC (CARILLONAC-1-PC): a gate in code, not a note.

Why: on 2026-11-21 the unguarded Phase 1a analysis filled the 16 GB of the Home PC
and froze it (the machine also hosts the MT5 trading desks). The stem beat-grid step
right after a fresh Demucs separation spikes to 6-7 GB for one 5-minute track, so with
Dropbox and Chrome open a single track does not fit. Two memory notes already said
"check memory first" and the PC still froze, so the check has to live in code.

Sam's rule (2026-11-22): "as long as it only effects this pc, we don't need it on the
work pc". The repo also runs on STUDIO-2 (the work PC, RTX 3050), so on every machine
other than CARILLONAC-1-PC this module does NOTHING: no preflight, no priority change,
no thread cap, no supervision, no extra output. Commands run exactly as they run
today, with the same exit codes.

Knobs (environment):
  MEMORY_GUARD=off       disable the guard on the Home PC.
  MEMORY_GUARD=on        force the guard on anywhere (tests only).
  MEMORY_GUARD_CHILD=1   set on the re-launched child; the child never re-launches.
  MEMORY_GUARD_MIN_START_GB       the HEAVY start floor (a fresh separation is needed).
  MEMORY_GUARD_MIN_START_LIGHT_GB the LIGHT start floor (every track is cached).

The start floor depends on the WORK, not on the machine: a FRESH Demucs
separation (and the beat-grid spike right after it) needs 7.0 GB of free
memory, while a run that reuses the finished stem analysis of every track
needs only 2.0 GB. So the floor is picked from the cache state of the
--input folder, and the floor and the reason for it are printed on every
preflight.

usage:
  python Source/memory_guard.py [--min-start-gb N] [--min-start-light-gb N]
      [--input DIR] [--min-free-gb N] [--resume-dir DIR]
      [--max-attempts N] [--threads N] [--log FILE] [--preflight-only] -- <command...>
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:  # the work PC may not have psutil; the guard must not break it
    psutil = None


# The machines the guard protects. Compared case-insensitively with COMPUTERNAME.
GUARDED_MACHINES = {"CARILLONAC-1-PC"}

MEMORY_GUARD_ENV = "MEMORY_GUARD"
MEMORY_GUARD_CHILD_ENV = "MEMORY_GUARD_CHILD"
MIN_START_GB_ENV = "MEMORY_GUARD_MIN_START_GB"
MIN_START_LIGHT_GB_ENV = "MEMORY_GUARD_MIN_START_LIGHT_GB"

# The HEAVY start floor is the beat-grid spike (6-7 GB for one 5-minute track)
# plus headroom. It applies when the run will do a FRESH separation.
DEFAULT_MIN_START_GB = 7.0
# The LIGHT start floor applies when every track already has a finished stem
# analysis: no separation, no beat-grid spike, just the cached re-lay.
DEFAULT_MIN_START_LIGHT_GB = 2.0
DEFAULT_MIN_FREE_GB = 1.5
DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_THREADS = 6

# Exit codes the guard itself produces. Anything else is the child's own exit code.
EXIT_GUARD_GIVE_UP = 3   # the guard killed the run and could not make progress
EXIT_REFUSED = 4         # the preflight refused to start the run

GB = 2 ** 30
MB = 2 ** 20

_commit_notice_shown = False


# --------------------------------------------------------------------------
# Is the guard active on this machine?
# --------------------------------------------------------------------------

def current_machine() -> str:
    """The machine name (Windows COMPUTERNAME), upper-cased; "" when it is unset."""
    return os.environ.get("COMPUTERNAME", "").strip().upper()


def _override() -> str:
    """The MEMORY_GUARD override: "on", "off" or "" (no override)."""
    value = os.environ.get(MEMORY_GUARD_ENV, "").strip().lower()
    if value in ("off", "0", "no", "false"):
        return "off"
    if value in ("on", "1", "yes", "true"):
        return "on"
    return ""


def guard_forced() -> bool:
    """True when MEMORY_GUARD explicitly turns the guard on (tests only)."""
    return _override() == "on"


def guard_active() -> bool:
    """True only on the guarded machines, unless MEMORY_GUARD overrides it.

    Everywhere else this is False, and every caller must then do nothing at all:
    no preflight, no priority change, no thread cap, no supervision, no output.
    """
    override = _override()
    if override == "off":
        return False
    if override == "on":
        return True
    return current_machine() in GUARDED_MACHINES


def in_test_process() -> bool:
    """True inside a pytest process.

    The repo's own test suite calls orchestrator.main() in-process; the guard must
    never re-launch the pipeline there. A real run (python -m
    automated_dj_mixes.orchestrator) has no pytest imported.
    """
    return "pytest" in sys.modules


def require_psutil():
    """psutil, or a clear error (the guard cannot work without it)."""
    if psutil is None:
        raise RuntimeError("psutil is not installed; the memory guard cannot run")
    return psutil


def default_min_start_gb() -> float:
    """The HEAVY start floor: 7.0 GB (the beat-grid spike), or the env override."""
    raw = os.environ.get(MIN_START_GB_ENV, "").strip()
    if not raw:
        return DEFAULT_MIN_START_GB
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_MIN_START_GB


def default_min_start_light_gb() -> float:
    """The LIGHT start floor: 2.0 GB (cached run), or the env override."""
    raw = os.environ.get(MIN_START_LIGHT_GB_ENV, "").strip()
    if not raw:
        return DEFAULT_MIN_START_LIGHT_GB
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_MIN_START_LIGHT_GB


# --------------------------------------------------------------------------
# The cache state: does this run need a FRESH separation?
# --------------------------------------------------------------------------

# The audio extensions the pipeline reads (the sibling Audio Analysis Toolkit
# uses the same set).
AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".aiff", ".aif", ".ogg")

# The folder the finished stem analysis lives in, next to the project.
STEM_ANALYSIS_DIR = "_Stem Analysis"


def stem_analysis_cache_path(project_dir, stem: str) -> Path:
    """The finished stem analysis of one track: SECTIONS_STEM_<track>.json.

    The name is the orchestrator's own: stem_detector.detect writes
    `<project>/_Stem Analysis/SECTIONS_STEM_<track>.json` and the orchestrator
    reads the same file back (Source/stem_detector.py, orchestrator.py).
    """
    return Path(project_dir) / STEM_ANALYSIS_DIR / f"SECTIONS_STEM_{stem}.json"


def finished_stem_analysis(project_dir, stem: str) -> bool:
    """True when the track has a FINISHED stem analysis beside the project.

    "Finished" means the analysis file exists and holds a real analysis: a
    JSON object with a non-empty "sections" list (the same test the hints
    gate uses on the cached file). A missing, empty or malformed file means
    the track is not cached, so the run would separate it again.
    """
    path = stem_analysis_cache_path(project_dir, stem)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    sections = data.get("sections")
    return isinstance(sections, list) and bool(sections)


def separation_state(input_dir) -> tuple[int, int] | None:
    """(tracks, tracks that need a fresh separation) for an --input folder.

    None when the folder cannot be read: the caller then assumes the worst
    (the heavy floor) instead of guessing.
    """
    folder = Path(input_dir)
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    tracks = sorted(path for path in entries
                    if path.is_file()
                    and path.suffix.lower() in AUDIO_EXTENSIONS
                    and not path.name.startswith("."))
    project = folder.parent
    fresh = sum(1 for path in tracks if needs_fresh_separation(project, path.stem))
    return len(tracks), fresh


# Demucs is skipped only when BOTH of these sit beside the finished analysis
# (kick_model_adapter: "CASE A - warm: envelopes + drums stem both cached").
SEPARATION_SIDECARS = ("__stemenv.npz", "__drumsstem.npz")


def needs_fresh_separation(project_dir, stem: str) -> bool:
    """True when the run would separate this track again: no finished analysis,
    or a missing envelope or drums-stem sidecar (either one sends it to Demucs)."""
    if not finished_stem_analysis(project_dir, stem):
        return True
    stem_dir = Path(project_dir) / "_Stem Analysis"
    return any(not (stem_dir / f"{stem}{suffix}").is_file() for suffix in SEPARATION_SIDECARS)


def choose_start_floor(min_start_gb: float | None = None, input_dir=None,
                       min_start_light_gb: float | None = None) -> tuple[float, str]:
    """The start floor for this run, and the reason it was chosen.

    The HEAVY floor applies when the run will do a FRESH separation for at
    least one track (the Demucs separation plus the beat-grid spike). The
    LIGHT floor applies when every track in --input already has a finished
    stem analysis, so the run only re-lays cached analysis. An explicit
    --min-start-gb wins over both.
    """
    heavy = default_min_start_gb() if min_start_gb is None else float(min_start_gb)
    light = (default_min_start_light_gb() if min_start_light_gb is None
             else float(min_start_light_gb))
    if min_start_gb is not None:
        return heavy, "requested for this run"
    if input_dir is None:
        return heavy, "no input folder given, assuming a fresh separation"
    state = separation_state(input_dir)
    if state is None:
        return heavy, f"cannot read {input_dir}, assuming a fresh separation"
    total, fresh = state
    if total == 0:
        return light, f"no audio files in {input_dir}, nothing to separate"
    if fresh:
        return heavy, f"{fresh} of {total} tracks need a fresh separation"
    return light, f"all {total} tracks cached"


# --------------------------------------------------------------------------
# Memory readings
# --------------------------------------------------------------------------

class _PerformanceInformation(ctypes.Structure):
    """The Windows PERFORMANCE_INFORMATION struct (psapi.GetPerformanceInfo)."""

    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.c_ulong),
        ("ProcessCount", ctypes.c_ulong),
        ("ThreadCount", ctypes.c_ulong),
    ]


def commit_headroom_bytes():
    """Commit headroom in bytes (Windows: CommitLimit minus CommitTotal).

    None when the platform does not expose it; the caller then checks physical
    memory only and says so once.
    """
    if os.name != "nt":
        return None
    try:
        get_info = ctypes.WinDLL("psapi").GetPerformanceInfo
    except (AttributeError, OSError):
        return None
    try:
        get_info.restype = ctypes.c_int
        get_info.argtypes = [ctypes.POINTER(_PerformanceInformation), ctypes.c_ulong]
        info = _PerformanceInformation()
        info.cb = ctypes.sizeof(info)
        if not get_info(ctypes.byref(info), info.cb):
            return None
    except (AttributeError, OSError, ValueError):
        return None
    return _commit_headroom(info.CommitTotal, info.CommitLimit, info.PageSize)


def _commit_headroom(commit_total, commit_limit, page_size) -> int:
    """Commit headroom in bytes.

    The PERFORMANCE_INFORMATION memory counters are in PAGES, not bytes (the
    struct carries the page size), so the difference has to be scaled. Measured
    on the Home PC: CommitLimit 31.9 GB, CommitTotal 24.0 GB, 7.4 GB headroom.
    """
    if not page_size:
        return 0
    return max(0, (commit_limit - commit_total) * page_size)


def available_bytes() -> int:
    """Free physical memory in bytes."""
    return require_psutil().virtual_memory().available


def largest_processes(count: int = 5):
    """The `count` biggest processes by resident memory: [(name, pid, rss_bytes)]."""
    psutil = require_psutil()
    rows = []
    for proc in psutil.process_iter(["name", "pid", "memory_info"]):
        try:
            info = proc.info
            rss = info["memory_info"].rss
            name = info.get("name") or "?"
            pid = info.get("pid") or 0
        except Exception:
            continue
        rows.append((name, pid, rss))
    rows.sort(key=lambda row: row[2], reverse=True)
    return rows[:count]


def print_largest_processes(count: int = 5) -> None:
    """Always print the biggest memory hogs, so the reason for a refusal is visible."""
    print(f"[memory-guard] the {count} largest processes by memory:")
    for name, pid, rss in largest_processes(count):
        print(f"  {name} pid={pid} rss={rss // MB} MB")


def _note_commit_unavailable() -> None:
    """Say once that the commit check is not available on this platform."""
    global _commit_notice_shown
    if _commit_notice_shown:
        return
    _commit_notice_shown = True
    print("[memory-guard] commit headroom is unavailable on this platform; "
          "the start floor is checked against physical memory only.")


# --------------------------------------------------------------------------
# The preflight: refuse to start a run that will not fit
# --------------------------------------------------------------------------

def preflight(min_start_gb: float | None = None, input_dir=None,
              min_start_light_gb: float | None = None) -> bool:
    """True when there is enough memory to start, False when the run is refused.

    The floor is the heavy one when the run will do a fresh separation and
    the light one when every track is cached (see choose_start_floor); the
    floor and the reason for it are printed first, so the log says which one
    applied. Refuses when the free physical memory OR the commit headroom is
    below the floor. The biggest memory hogs are printed either way, so the
    reason (and what to close) is visible in the log.
    """
    psutil = require_psutil()
    min_start_gb, floor_reason = choose_start_floor(min_start_gb, input_dir,
                                                     min_start_light_gb)
    print(f"[memory-guard] {min_start_gb:.1f} GB floor: {floor_reason}")
    floor = min_start_gb * GB
    available = psutil.virtual_memory().available
    commit = commit_headroom_bytes()
    if commit is None:
        _note_commit_unavailable()
    print_largest_processes()
    if available < floor:
        _refuse(f"only {available / GB:.1f} GB of physical memory is free "
                f"(the floor is {min_start_gb:.1f} GB)", min_start_gb)
        return False
    if commit is not None and commit < floor:
        _refuse(f"only {commit / GB:.1f} GB of commit headroom is left "
                f"(the floor is {min_start_gb:.1f} GB)", min_start_gb)
        return False
    print(f"[memory-guard] preflight OK: {available / GB:.1f} GB physical memory free"
          + (f", {commit / GB:.1f} GB commit headroom" if commit is not None else "")
          + f" (floor {min_start_gb:.1f} GB)")
    return True


def _refuse(reason: str, min_start_gb: float) -> None:
    """The refusal message: what to close and the numbers."""
    print(f"[memory-guard] REFUSING TO START: {reason}.")
    print(f"[memory-guard] Close Chrome and Dropbox (and any other big app) and retry; "
          f"this run needs about {min_start_gb:.1f} GB of free memory.")


# --------------------------------------------------------------------------
# Supervision: run a command, kill it before it freezes the machine
# --------------------------------------------------------------------------

def kill_tree(proc) -> None:
    """Kill the parent first (so it cannot start new children), then every child.

    Each kill is guarded on its own, so one already-dead process cannot stop the
    rest (Kimi + MiniMax review of the prototype).
    """
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        parent, children = None, []
    for victim in ([parent] if parent else []) + children:
        try:
            victim.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        pass


def cache_file_count(resume_dir) -> int:
    """How many cache files the resume folder holds (any cache file counts)."""
    if resume_dir is None:
        return 0
    try:
        return len(list(Path(resume_dir).glob("*")))
    except OSError:
        return 0


def _log(log_path, message: str) -> None:
    """Print a guard message and append it to the guard log (when asked for)."""
    line = f"[memory-guard] {time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    if log_path:
        try:
            with open(log_path, "a", encoding="ascii", errors="replace") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def supervise(cmd, resume_dir=None, min_free_gb: float = DEFAULT_MIN_FREE_GB,
              max_attempts: int = DEFAULT_MAX_ATTEMPTS, threads: int = DEFAULT_THREADS,
              env=None, log_path=None, poll_seconds: float = 2.0):
    """Run `cmd` under the memory guard and return its exit code.

    The child runs at below-normal priority with capped threads; the whole
    process tree is killed when free memory falls below `min_free_gb`. With a
    `resume_dir` the run is restarted after a kill, but only when the cache
    folder grew since the last kill (any cache file counts as progress) -
    otherwise the guard gives up (exit 3). Without a resume folder a kill is
    final (exit 3). The child's own exit code is passed through otherwise.
    """
    psutil = require_psutil()
    floor = min_free_gb * GB
    child_env = dict(os.environ if env is None else env)
    # Cap the thread pools: the beat-grid spike is one big BLAS/torch pool.
    # Torch's intra-op pool follows OMP_NUM_THREADS.
    child_env["OMP_NUM_THREADS"] = str(threads)
    child_env["MKL_NUM_THREADS"] = str(threads)
    child_env["TORCH_NUM_THREADS"] = str(threads)
    creationflags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    progress_at_last_kill = None
    low = psutil.virtual_memory().available
    for attempt in range(1, max_attempts + 1):
        _log(log_path, f"attempt {attempt} of {max_attempts}: starting "
                       f"{' '.join(str(part) for part in cmd)} "
                       f"({low / GB:.1f} GB free, floor {min_free_gb:.1f} GB)")
        proc = subprocess.Popen(cmd, env=child_env, creationflags=creationflags)
        killed = False
        try:
            while proc.poll() is None:
                available = psutil.virtual_memory().available
                low = min(low, available)
                if available < floor:
                    kill_tree(proc)
                    killed = True
                    break
                time.sleep(poll_seconds)
        finally:
            if proc.poll() is None:
                kill_tree(proc)
        if not killed:
            _log(log_path, f"exit {proc.returncode}, lowest free memory {low / GB:.1f} GB")
            return proc.returncode
        progress = cache_file_count(resume_dir) if resume_dir is not None else None
        _log(log_path, f"GUARD KILL: free memory fell to {low / GB:.2f} GB"
                       + (f", {progress} cache files in {resume_dir}"
                          if progress is not None else ""))
        if progress is None:
            _log(log_path, "GIVE UP: no resume folder, a guard kill is final")
            return EXIT_GUARD_GIVE_UP
        if progress_at_last_kill is not None and progress <= progress_at_last_kill:
            _log(log_path, "GIVE UP: no new cache file since the last kill, "
                           "one stage does not fit in memory")
            return EXIT_GUARD_GIVE_UP
        progress_at_last_kill = progress
        _log(log_path, "RESTART: the cache folder grew, restarting the run")
    _log(log_path, "GIVE UP: attempts used up")
    return EXIT_GUARD_GIVE_UP


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    """The CLI. Off the Home PC it runs the command directly, with no output of its own."""
    parser = argparse.ArgumentParser(
        description="Run a command under the Home PC memory guard.")
    parser.add_argument("--min-start-gb", type=float, default=None,
                        help="the start floor for this run (GB); overrides the "
                             "automatic heavy/light choice")
    parser.add_argument("--min-start-light-gb", type=float, default=None,
                        help="the light start floor (GB), used when every track "
                             "in --input is already cached")
    parser.add_argument("--input", type=Path, default=None,
                        help="the audio folder; the guard reads its cache state "
                             "to pick the heavy or the light start floor")
    parser.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB,
                        help="kill the run below this much free memory (GB)")
    parser.add_argument("--resume-dir", type=Path, default=None,
                        help="cache folder; the run restarts after a kill only if it grew")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS,
                        help="how many times the run may be restarted after a kill")
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS,
                        help="thread cap for the child (OMP/MKL/torch)")
    parser.add_argument("--log", default=None, help="append the guard log to this file")
    parser.add_argument("--preflight-only", action="store_true",
                        help="only run the start preflight, then exit")
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else list(args.cmd)

    if not guard_active():
        # Not the Home PC: the command runs exactly as it runs today, and the
        # guard prints nothing of its own.
        if args.preflight_only:
            return 0
        if not cmd:
            parser.error("no command given")
        return subprocess.call(cmd)

    if not cmd and not args.preflight_only:
        parser.error("no command given")
    require_psutil()
    if not preflight(args.min_start_gb, input_dir=args.input,
                     min_start_light_gb=args.min_start_light_gb):
        return EXIT_REFUSED
    if args.preflight_only:
        return 0
    return supervise(cmd, resume_dir=args.resume_dir, min_free_gb=args.min_free_gb,
                     max_attempts=args.max_attempts, threads=args.threads,
                     log_path=args.log)


if __name__ == "__main__":
    sys.exit(main())
