"""Beatgrid verification gate — does a track's beat grid sit ON its audio?

The 09.06.26 mix shipped with Rekordbox grids ~1% off the actual audio on
several commercial tracks (Todd Edwards, Say My Name): the grid BPM snapped
to a wrong round number, so warp markers swept through the beat and Sam
heard/saw "warping out". This gate catches that BEFORE a mix is built.

Method — WHOLE-TRACK phase-concentration test. Two design lessons from the
2026-06-11 calibration are baked in:
  - Window sampling is luck: a 1%-off grid cycles through alignment every
    ~47s, so any single 20s window can read "locked" (proven on Say My Name).
    The test must be whole-track.
  - Offset MAGNITUDE is biased: librosa onset times carry a constant lag of
    a couple of analysis frames, so even locked grids read ~0.1-0.2 of a
    beat "off". The discriminator must be CONCENTRATION, not magnitude.
So: kick-band onsets (150 Hz lowpass) across the whole track; each onset's
SIGNED offset to its nearest grid entry becomes a phase. TWO verdicts:
  - TEMPO — concentration on the HALF-BEAT circle (phase doubled). House
    music puts kicks ON beats and bass stabs BETWEEN them (offbeats);
    doubling folds both clusters onto one point, so a locked grid reads
    high concentration regardless of bassline style, while a ~1%-wrong
    grid sweeps the circle and reads ~0. A +1% detuned copy of the grid is
    graded as a per-track known-bad control.
  - PHASE — the full-circle mean phase: a grid with the right tempo but
    markers sitting consistently BETWEEN the kicks (what Sam photographed
    on Todd: "markers floating between the transients") clusters far from
    the kicks. Concentration alone is blind to this; the mean catches it.
Both are BPM-independent; n≈1000 onsets makes them stable.

Usage:
  python Source/validate_beatgrid.py "<project dir or Audio dir>"   # table
Library:
  from validate_beatgrid import check_grid, enforce_beatgrid_quality
"""

from __future__ import annotations

import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Thresholds (calibrated 2026-06-11 on 22 real tracks across the 08.06.26 +
# 09.06.26 projects, with +1% detuned twins as per-track known-bad controls.
# Real grids read R 0.32-0.95 with controls at 0.01-0.07; the acapella's
# garbage grid read 0.14; ear-confirmed-bad Todd read phase +0.15 with the
# next-highest real track at +0.11):
PASS_R = 0.40           # half-beat-circle concentration above this = tempo locked
FAIL_R = 0.30           # below this = grid tempo not on the audio
PHASE_TOL = 0.12        # |mean phase| (beats) within this = markers on the kicks
PHASE_FAIL = 0.15       # beyond this = markers consistently off the kicks

# Independent-BPM tiebreaker (added 2026-06-11 evening, Test Mix 11.06.26):
# percussion-heavy material (Latin house, gospel stabs) smears R below the
# absolute thresholds even when the grid is right. If an INDEPENDENT analyzer
# (MIK) agrees with the grid's span-BPM exactly, and the grid is internally
# consistent, and the phase is clean, tempo is confirmed by two independent
# sources — that beats one noisy concentration stat. The rescue floor keeps
# garbage grids (acapellas ≈ 0.14) out, and an internally INCONSISTENT grid
# (span disagreeing with RB's own DB BPM — the La Trumpter case: grid 123.87,
# DB 125.00, MIK 126.00) can never be rescued.
RESCUE_MIN_R = 0.20     # below this no tiebreaker applies — grid is noise
RESCUE_CONTROL_X = 5.0  # must still separate clearly from the +1% twin
BPM_AGREE = 0.002       # MIK vs grid-span agreement (0.2%)
SPAN_DB_TOL = 0.005     # grid-span vs RB-DB internal consistency (0.5%)
MIN_ONSETS = 80         # fewer kick onsets whole-track = cannot judge (acapella)
N_SEGMENTS = 8          # per-segment R, reported for drift diagnosis
KICK_CUTOFF_HZ = 150.0  # lowpass for kick isolation (NOT mel fmax — a 160Hz
                        # mel basis has empty filters and garbage onset times)
LOAD_SR = 22050         # onset hop 512 -> ~23ms resolution; beats are ~470ms


@dataclass
class GridCheck:
    track: str
    verdict: str                  # PASS / WARN / FAIL / SKIP
    resultant: float = -1.0       # half-beat-circle R (1=tempo locked, 0=sweeping)
    mean_phase: float = 0.0       # full-circle mean phase in beats (0=on the kicks)
    detuned_r: float = -1.0       # same metric on the +1% twin (known-bad control)
    seg_r: list[float] = field(default_factory=list)
    n_onsets: int = 0
    detail: str = ""


def _resultant(phases: np.ndarray) -> float:
    """Circular resultant length of beat-circle phases (radians)."""
    if len(phases) == 0:
        return 0.0
    return float(np.abs(np.mean(np.exp(1j * phases))))


def _kick_onsets(audio_path: Path) -> tuple[np.ndarray, float] | None:
    """Whole-track kick onset times: lowpass the audio (kick band), then
    onset-detect on the filtered signal. Returns (onset_times_sec, duration)."""
    import librosa
    from scipy.signal import butter, sosfiltfilt
    try:
        y, sr = librosa.load(str(audio_path), sr=LOAD_SR, mono=True)
    except Exception:
        return None
    if len(y) < sr * 30:
        return None
    sos = butter(4, KICK_CUTOFF_HZ, btype="low", fs=sr, output="sos")
    y_low = sosfiltfilt(sos, y)
    env = librosa.onset.onset_strength(y=np.ascontiguousarray(y_low), sr=sr)
    onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr,
                                        units="time", backtrack=False)
    return onsets, len(y) / sr


def _grade(onsets: np.ndarray, grid_sec: np.ndarray,
           beat_period: float) -> tuple[float, float, list[float]]:
    """Grade onsets against a grid.

    Returns (r_half, mean_phase_beats, seg_r_half):
      r_half           — concentration on the HALF-beat circle (tempo signal;
                         folds on-beat kicks + offbeat bass stabs together)
      mean_phase_beats — full-circle circular mean in beats (phase signal;
                         0 = markers on the kicks, ±0.5 = between them)
      seg_r_half       — per-segment r_half for drift diagnosis
    """
    idx = np.searchsorted(grid_sec, onsets)
    idx = np.clip(idx, 1, len(grid_sec) - 1)
    d_prev = onsets - grid_sec[idx - 1]          # >= 0
    d_next = onsets - grid_sec[idx]              # <= 0
    signed = np.where(np.abs(d_prev) <= np.abs(d_next), d_prev, d_next)
    phases = 2.0 * np.pi * (signed / beat_period)

    half = 2.0 * phases                          # fold offbeats onto beats
    r_half = _resultant(half)
    mean_phase = float(np.angle(np.mean(np.exp(1j * phases)))) / (2.0 * np.pi)

    seg_r: list[float] = []
    n = len(half)
    seg_size = max(1, n // N_SEGMENTS)
    for s in range(0, n, seg_size):
        chunk = half[s:s + seg_size]
        if len(chunk) >= 10:
            seg_r.append(round(_resultant(chunk), 2))
    return r_half, mean_phase, seg_r


def check_grid(audio_path: Path, beat_times_ms: list[int],
               independent_bpm: float | None = None,
               db_bpm: float | None = None) -> GridCheck:
    """Verify a beat grid against its audio. Read-only; one full-track load.

    Self-referencing verdict: the track's own kick onsets are graded against
    (a) the actual grid and (b) the same grid detuned +1% — a known-bad
    control with identical onset quality. A locked grid separates widely
    from its detuned twin; a wrong grid grades like its control. This makes
    the verdict robust to per-track onset noise (busy low end, swing).

    independent_bpm (e.g. MIK's) + db_bpm (RB's stored value) enable the
    tiebreaker for percussion-heavy tracks whose R lands in the ambiguous
    band: two independent analyzers agreeing on tempo + a clean phase beats
    one noisy concentration stat.
    """
    name = Path(audio_path).name
    if not beat_times_ms or len(beat_times_ms) < 32:
        return GridCheck(name, "SKIP", detail="grid too short to judge")

    grid_sec = np.asarray(beat_times_ms, dtype=float) / 1000.0
    ivs = np.diff(grid_sec)
    beat_period = float(np.median(ivs)) if len(ivs) else 0.0
    if beat_period <= 0:
        return GridCheck(name, "SKIP", detail="degenerate grid")

    res = _kick_onsets(audio_path)
    if res is None:
        return GridCheck(name, "SKIP", detail="audio load failed / too short")
    onsets, _dur = res
    n = len(onsets)
    if n < MIN_ONSETS:
        return GridCheck(name, "SKIP", n_onsets=n,
                         detail=f"only {n} kick onsets — acapella/ambient, cannot judge")
    # Constrain to the gridded span (intro/outro tails past the grid would
    # smear phase against extrapolated entries that don't exist).
    onsets = onsets[(onsets >= grid_sec[0] - beat_period)
                    & (onsets <= grid_sec[-1] + beat_period)]
    if len(onsets) < MIN_ONSETS:
        return GridCheck(name, "SKIP", n_onsets=len(onsets),
                         detail="too few kick onsets inside the gridded span")

    r_half, mean_phase, seg_r = _grade(onsets, grid_sec, beat_period)

    # Known-bad control: same onsets, grid stretched +1% around its start —
    # the exact failure mode this gate exists to catch.
    detuned = grid_sec[0] + (grid_sec - grid_sec[0]) * 1.01
    r_detuned, _, _ = _grade(onsets, detuned, beat_period * 1.01)

    # Independent-BPM tiebreaker eligibility: grid internally consistent
    # (span agrees with RB's own DB value) AND an independent analyzer
    # agrees with the span. An internally inconsistent grid can never be
    # tempo-confirmed (its "span" is mush).
    span_bpm = 60000.0 * (len(beat_times_ms) - 1) / (
        beat_times_ms[-1] - beat_times_ms[0])
    internally_consistent = (
        db_bpm is None
        or abs(span_bpm - db_bpm) / db_bpm <= SPAN_DB_TOL
    )
    tempo_confirmed = bool(
        independent_bpm and independent_bpm > 40.0
        and internally_consistent
        and abs(span_bpm - independent_bpm) / independent_bpm <= BPM_AGREE
    )

    verdict, detail = verdict_from(r_half, mean_phase, r_detuned, tempo_confirmed)
    if db_bpm is not None and not internally_consistent:
        detail += (f" [grid INTERNALLY INCONSISTENT: span {span_bpm:.2f} vs "
                   f"RB DB {db_bpm:.2f}]")
    return GridCheck(name, verdict, r_half, mean_phase, r_detuned, seg_r, n, detail)


def verdict_from(r_half: float, mean_phase: float, r_detuned: float,
                 tempo_confirmed: bool = False) -> tuple[str, str]:
    """Pure verdict logic — unit-testable without audio.

    tempo_confirmed = an independent analyzer (MIK) agrees with the grid's
    span BPM and the grid is internally consistent. It rescues percussion-
    heavy tracks from the ambiguous R band, but never rescues noise-floor
    grids (RESCUE_MIN_R) and never overrides a bad PHASE.
    """
    phase_ok = abs(mean_phase) <= PHASE_TOL
    phase_bad = abs(mean_phase) >= PHASE_FAIL

    rescue_eligible = (
        tempo_confirmed
        and r_half >= RESCUE_MIN_R
        and r_half >= r_detuned * RESCUE_CONTROL_X
    )

    if r_half <= FAIL_R or r_half < r_detuned * 2.0:
        if rescue_eligible and phase_ok:
            return "PASS", (
                f"tempo confirmed by MIK (grid span = MIK BPM; R={r_half:.2f} "
                f"is percussion-smeared but {r_half / max(r_detuned, 0.01):.0f}x "
                f"its +1% control, phase {mean_phase:+.2f})")
        if rescue_eligible and phase_bad:
            return "FAIL", (
                f"grid PHASE off the kicks (tempo confirmed by MIK but markers "
                f"sit {mean_phase:+.2f} beats off — fix with --write-override)")
        return "FAIL", (
            f"grid TEMPO off the audio (R={r_half:.2f} vs +1% control "
            f"{r_detuned:.2f} — locked grids separate widely from "
            f"their detuned twin)")
    if phase_bad and (r_half >= PASS_R or rescue_eligible):
        return "FAIL", (
            f"grid PHASE off the kicks (tempo locked R={r_half:.2f} "
            f"but markers sit {mean_phase:+.2f} beats from the kicks "
            f"— 'floating between the transients')")
    if phase_ok and (r_half >= PASS_R or rescue_eligible):
        note = ", tempo confirmed by MIK" if (rescue_eligible and r_half < PASS_R) else ""
        return "PASS", (
            f"grid locked on the audio (R={r_half:.2f}, "
            f"phase {mean_phase:+.2f}, control {r_detuned:.2f}{note})")
    return "WARN", (
        f"borderline (R={r_half:.2f}, phase {mean_phase:+.2f}, "
        f"control {r_detuned:.2f}) — eyeball the DETECT picture")


def _mik_bpm(audio_path) -> float | None:
    """MIK's independently-analyzed BPM for a track (None if unavailable)."""
    try:
        from automated_dj_mixes.mik_reader import read_mik_db_track
        m = read_mik_db_track(Path(audio_path))
        return float(m.bpm) if m and m.bpm else None
    except Exception:
        return None


def enforce_beatgrid_quality(analyses, rb_matches: dict,
                             allow_bad_grids: bool = False) -> list[GridCheck]:
    """Pipeline gate: hard-stop if any track's grid fails verification.

    Mirrors enforce_rekordbox_coverage: loud per-track verdicts, RuntimeError
    on FAIL unless explicitly overridden (--allow-bad-grids).
    """
    checks: list[GridCheck] = []
    print("Beatgrid verification (whole-track onset-vs-grid sweep test)...")
    for a in analyses:
        rb = rb_matches.get(str(a.path))
        if rb is None:
            continue
        c = check_grid(a.path, getattr(rb, "beat_times_ms", []) or [],
                       independent_bpm=_mik_bpm(a.path),
                       db_bpm=getattr(rb, "bpm", None))
        checks.append(c)
        stats = f"R={c.resultant:.2f}" if c.resultant >= 0 else "-"
        print(f"  [{c.verdict:4s}] {c.track[:54]:56s} {stats:8s} {c.detail}")
    fails = [c for c in checks if c.verdict == "FAIL"]
    if fails and not allow_bad_grids:
        names = "\n".join(f"  - {c.track}: {c.detail}" for c in fails)
        raise RuntimeError(
            f"\n{len(fails)} track(s) have beat grids that do NOT sit on their "
            f"audio — warping would drift (the 09.06.26 'Todd' bug):\n{names}\n\n"
            f"Fix: re-grid these tracks (drum-stem grid fallback coming in "
            f"Stage 3), or re-run with --allow-bad-grids to proceed anyway.\n"
        )
    return checks


# ── Grid overrides (phase correction) ────────────────────────────────────────
#
# When the gate finds a tempo-locked grid whose markers sit consistently off
# the kicks (the Todd case), the fix is a phase SLIDE of the whole grid.
# Corrections live in <project>/Hints/grid_overrides.json:
#   { "<wav filename>": {"shift_ms": +70.3, "reason": "...", ...} }
# The orchestrator applies them right after the Rekordbox match, so the warp
# markers, the one-clock section cuts AND this gate all see the corrected
# grid — every later run re-validates the fix automatically.
# (Full drum-stem beat-tracking remains the documented escalation if a track
# ever FAILs on TEMPO; as of the 2026-06-11 calibration none does.)


def overrides_path(project_dir: Path) -> Path:
    return Path(project_dir) / "Hints" / "grid_overrides.json"


def load_grid_overrides(project_dir: Path) -> dict:
    p = overrides_path(project_dir)
    if not p.exists():
        return {}
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  WARNING: could not read {p.name}: {e}")
        return {}


def apply_grid_override(rb_match, override: dict) -> None:
    """Apply a grid override in place. Two kinds:

    shift_ms      — slide the existing grid (phase correction, the Todd fix)
    replace_grid  — synthesize a whole new constant-BPM grid (the escalation
                    for grids that are wrong beyond a slide — first case:
                    La Trumpter, RB grid internally inconsistent, true tempo
                    confirmed by MIK + Sam = 126.00). Fields: bpm, first_ms
                    (grid entry 0 in ms), n_beats, first_downbeat_offset.
    """
    rep = override.get("replace_grid")
    if rep:
        bpm = float(rep["bpm"])
        first = float(rep["first_ms"])
        n = int(rep["n_beats"])
        period_ms = 60000.0 / bpm
        rb_match.beat_times_ms = [int(round(first + k * period_ms)) for k in range(n)]
        rb_match.first_downbeat_offset = int(rep.get("first_downbeat_offset", 0))
        rb_match.bpm = bpm
        return
    shift = float(override.get("shift_ms", 0.0))
    if abs(shift) < 0.5 or not getattr(rb_match, "beat_times_ms", None):
        return
    rb_match.beat_times_ms = [int(round(t + shift)) for t in rb_match.beat_times_ms]


def _fit_anchor(onsets: np.ndarray, bpm: float, duration_sec: float,
                anchor0_sec: float) -> tuple[float, int, int, float, float]:
    """Fit a constant-BPM grid's anchor to kick onsets (pure math).

    Starting from anchor0 (e.g. the old grid's first downbeat — inherits its
    BAR phase), iteratively zeroes the mean kick phase. Returns
    (first_sec, n_beats, first_downbeat_offset, r_half, mean_phase) where
    first_sec is grid entry 0 (within one beat of audio start) and
    first_downbeat_offset marks the bar-beat-1 entry, matching the RB grid
    convention used by the warp markers.
    """
    period = 60.0 / bpm
    anchor = float(anchor0_sec)
    first = anchor
    n = 1
    for _ in range(3):
        k0 = max(0, int(anchor / period))      # whole beats between ~0 and anchor
        first = anchor - k0 * period           # grid entry 0, in [0, period)
        n = max(2, int((duration_sec - first) / period) + 1)
        grid = first + np.arange(n) * period
        r_half, mean_phase, _ = _grade(onsets, grid, period)
        if abs(mean_phase) < 0.005:
            break
        anchor += mean_phase * period
    k0 = max(0, int(round((anchor - first) / period)))
    return first, n, k0 % 4, r_half, mean_phase


def write_grid_replacement(project_dir: Path, wav: Path, rb_match,
                           true_bpm: float) -> dict | None:
    """Fit a constant true_bpm grid to the track's kicks and store it as a
    replace_grid override. Verifies the fit with the gate before writing."""
    import json
    res = _kick_onsets(wav)
    if res is None:
        print(f"  cannot fit {wav.name}: audio load failed / too short")
        return None
    onsets, dur = res
    if len(onsets) < MIN_ONSETS:
        print(f"  cannot fit {wav.name}: only {len(onsets)} kick onsets")
        return None
    old_times = getattr(rb_match, "beat_times_ms", None) or []
    old_off = getattr(rb_match, "first_downbeat_offset", 0)
    anchor0 = (old_times[min(old_off, len(old_times) - 1)] / 1000.0
               if old_times else float(onsets[0]))
    first, n, dboff, r_half, mean_phase = _fit_anchor(onsets, true_bpm, dur, anchor0)

    # Prove the fit before writing anything.
    period = 60.0 / true_bpm
    grid = first + np.arange(n) * period
    r_fit, phase_fit, _ = _grade(onsets, grid, period)
    detuned = grid[0] + (grid - grid[0]) * 1.01
    r_ctrl, _, _ = _grade(onsets, detuned, period * 1.01)
    verdict, detail = verdict_from(r_fit, phase_fit, r_ctrl, tempo_confirmed=True)
    print(f"  fit: {true_bpm:.2f} BPM, first={first:.3f}s, {n} beats, "
          f"downbeat offset {dboff} -> {verdict}: {detail}")
    if verdict == "FAIL":
        print("  NOT writing override — the fitted grid doesn't verify.")
        return None

    overrides = load_grid_overrides(project_dir)
    overrides[wav.name] = {
        "replace_grid": {
            "bpm": round(true_bpm, 2),
            "first_ms": round(first * 1000.0, 1),
            "n_beats": int(n),
            "first_downbeat_offset": int(dboff),
        },
        "fit_R": round(r_fit, 2),
        "fit_phase": round(phase_fit, 3),
        "reason": "RB grid unusable (internally inconsistent); constant grid "
                  "fitted to kicks at the confirmed true BPM",
    }
    p = overrides_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    print(f"  wrote {p.name}: {wav.name} replace_grid @ {true_bpm:.2f} BPM")
    return overrides[wav.name]


def write_phase_override(project_dir: Path, wav: Path, rb_match) -> dict | None:
    """Measure a track's grid phase (with any existing override applied) and
    write/merge the corrective shift into grid_overrides.json."""
    import json
    overrides = load_grid_overrides(project_dir)
    existing = float(overrides.get(wav.name, {}).get("shift_ms", 0.0))

    times = [int(round(t + existing)) for t in rb_match.beat_times_ms]
    c = check_grid(wav, times)
    if c.resultant < 0:
        print(f"  cannot measure {wav.name}: {c.detail}")
        return None
    period_ms = float(np.median(np.diff(np.asarray(times, dtype=float))))
    add = c.mean_phase * period_ms
    total = existing + add
    overrides[wav.name] = {
        "shift_ms": round(total, 1),
        "measured_phase_beats": round(c.mean_phase, 3),
        "tempo_R": round(c.resultant, 2),
        "reason": "phase-correct grid to the kicks (beatgrid gate)",
    }
    p = overrides_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    print(f"  wrote {p.name}: {wav.name} shift_ms={total:+.1f} "
          f"(was {existing:+.1f}, measured phase {c.mean_phase:+.3f} beats)")
    return overrides[wav.name]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fix_substr = None
    if "--write-override" in sys.argv:
        i = sys.argv.index("--write-override")
        if i + 1 < len(sys.argv):
            fix_substr = sys.argv[i + 1]
            args = [a for a in args if a != fix_substr]
    if not args:
        print(__doc__)
        return 1
    target = Path(args[0])
    audio_dir = target / "Audio" if (target / "Audio").exists() else target
    project_dir = audio_dir.parent if audio_dir.name == "Audio" else target
    wavs = sorted(audio_dir.glob("*.wav"))
    if not wavs:
        print(f"No WAVs in {audio_dir}")
        return 1
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo / "Source"))
    from automated_dj_mixes.rekordbox_reader import (
        read_rekordbox_library, find_rekordbox_match,
    )
    lib = read_rekordbox_library()
    overrides = load_grid_overrides(project_dir)
    rc = 0
    for wav in wavs:
        rb = find_rekordbox_match(wav.name, lib)
        if rb is None:
            print(f"  [SKIP] {wav.name[:54]:56s} no RB entry")
            continue
        if fix_substr and fix_substr.lower() in wav.name.lower():
            write_phase_override(project_dir, wav, rb)
            overrides = load_grid_overrides(project_dir)
        if wav.name in overrides:
            apply_grid_override(rb, overrides[wav.name])
        c = check_grid(wav, rb.beat_times_ms,
                       independent_bpm=_mik_bpm(wav), db_bpm=rb.bpm)
        ov = " [override applied]" if wav.name in overrides else ""
        stats = (f"R={c.resultant:.2f} n={c.n_onsets} segs={c.seg_r}"
                 if c.resultant >= 0 else "-")
        print(f"  [{c.verdict:4s}] {wav.name[:54]:56s} {c.detail}{ov}")
        print(f"         {stats}")
        if c.verdict == "FAIL":
            rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
