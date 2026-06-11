"""One-clock regression tests (2026-06-11 warp/cut bug).

Pins the contract that section cuts and warp markers share a single clock:
the track's beat grid. sec_to_clip_beats must match the warp-marker
convention exactly (grid entry i -> clip beat i - first_downbeat_offset),
and segments_from_stem_sections must place boundaries on the grid even when
the detector's constant-BPM clock disagrees (the 09.06.26 regression).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

from automated_dj_mixes.warping import (
    calculate_warp_markers_from_beat_grid,
    grid_bpm_and_downbeat,
    sec_to_clip_beats,
)
from automated_dj_mixes.phrase_viz import segments_from_stem_sections


def make_grid(bpm: float, n: int, start_ms: int = 500) -> list[int]:
    iv = 60000.0 / bpm
    return [int(round(start_ms + i * iv)) for i in range(n)]


# --- sec_to_clip_beats ---

def test_converter_matches_warp_markers_exactly():
    grid = make_grid(128.0, 64)
    offset = 3
    markers = calculate_warp_markers_from_beat_grid(grid, 128.0, 60.0, offset)
    for m in markers:
        beat = sec_to_clip_beats(m.sample_time, grid, offset)
        assert abs(beat - m.beat_time) < 1e-9


def test_converter_interpolates_between_entries():
    grid = make_grid(128.0, 16)
    mid_sec = (grid[4] + grid[5]) / 2 / 1000.0
    assert abs(sec_to_clip_beats(mid_sec, grid, 0) - 4.5) < 0.01


def test_converter_extrapolates_past_ends():
    grid = make_grid(120.0, 16)  # 500ms beats
    before = (grid[0] - 500) / 1000.0
    after = (grid[-1] + 1000) / 1000.0
    assert abs(sec_to_clip_beats(before, grid, 0) - (-1.0)) < 0.01
    assert abs(sec_to_clip_beats(after, grid, 0) - 17.0) < 0.01


# --- grid_bpm_and_downbeat ---

def test_grid_bpm_prefers_agreeing_db_bpm():
    grid = make_grid(128.0, 600)
    bpm, downbeat = grid_bpm_and_downbeat(grid, 0, db_bpm=128.01)
    assert abs(bpm - 128.01) < 1e-9          # DB value wins when it agrees
    assert abs(downbeat - grid[0] / 1000.0) < 1e-9


def test_grid_bpm_rejects_disagreeing_db_bpm():
    grid = make_grid(128.0, 600)
    bpm, _ = grid_bpm_and_downbeat(grid, 0, db_bpm=64.0)  # halved — wrong
    assert abs(bpm - 128.0) < 0.1            # falls back to grid span


def test_downbeat_uses_offset_entry_not_grid_start():
    grid = make_grid(128.0, 64)
    _, downbeat = grid_bpm_and_downbeat(grid, 3)
    assert abs(downbeat - grid[3] / 1000.0) < 1e-9


# --- segments_from_stem_sections: the one-clock bridge ---

def stem_result_at(bpm: float, downbeat: float, bars: list[tuple[str, int, int]]):
    spb = 4 * 60.0 / bpm
    return {"bpm": bpm, "sections": [
        {"label": lab, "start_bar": b0, "end_bar": b1,
         "start_sec": round(downbeat + b0 * spb, 2),
         "end_sec": round(downbeat + b1 * spb, 2)}
        for (lab, b0, b1) in bars
    ]}


BARS = [("intro", 0, 8), ("drop", 8, 40), ("break", 40, 48), ("drop", 48, 80), ("outro", 80, 96)]


def test_grid_mode_equals_legacy_when_clocks_agree():
    grid = make_grid(128.0, 700)
    bpm, downbeat = grid_bpm_and_downbeat(grid, 0)
    res = stem_result_at(bpm, downbeat, BARS)
    legacy = segments_from_stem_sections(res)
    grid_mode = segments_from_stem_sections(res, beat_times_ms=grid, first_downbeat_offset=0)
    for a, b in zip(legacy, grid_mode):
        assert a.source_start_beats == b.source_start_beats
        assert a.source_end_beats == b.source_end_beats


def test_grid_mode_corrects_wrong_detector_clock():
    """THE 09.06.26 regression: detector ran at librosa's 129.2 on a 128.0
    track. Section TIMES are right; bar*4 beats are ~1% off. Grid mode must
    place boundaries at the grid-true bars of those times."""
    grid = make_grid(128.0, 700)
    true_bpm, downbeat = grid_bpm_and_downbeat(grid, 0)
    spb_true = 4 * 60.0 / true_bpm
    # Detected at the WRONG constant clock, but boundary TIMES are the real
    # musical moments (every 8 true bars in the audio):
    wrong = {"bpm": 129.199, "sections": [
        {"label": "drop", "start_bar": 0, "end_bar": 81,  # wrong-clock bars
         "start_sec": downbeat, "end_sec": downbeat + 80 * spb_true},
    ]}
    legacy = segments_from_stem_sections(wrong)
    grid_mode = segments_from_stem_sections(wrong, beat_times_ms=grid, first_downbeat_offset=0)
    assert legacy[0].source_end_beats == 81 * 4.0          # off the audio
    assert grid_mode[0].source_end_beats == 80 * 4.0       # on the audio


# --- beatgrid gate (validate_beatgrid) ---

def test_gate_separates_locked_from_detuned():
    import numpy as np
    from validate_beatgrid import _grade
    rng = np.random.default_rng(7)
    grid = np.array([0.5 + i * (60.0 / 128.0) for i in range(700)])
    # Kicks on beats (15ms jitter) + offbeat bass stabs (the house confound)
    kicks = grid[::1] + rng.normal(0.0, 0.015, len(grid))
    offbeats = grid[:-1] + (60.0 / 128.0) / 2 + rng.normal(0.0, 0.02, len(grid) - 1)
    onsets = np.sort(np.concatenate([kicks, offbeats[::2]]))
    period = 60.0 / 128.0
    r_good, phase_good, _ = _grade(onsets, grid, period)
    detuned = grid[0] + (grid - grid[0]) * 1.01
    r_bad, _, _ = _grade(onsets, detuned, period * 1.01)
    assert r_good > 0.6, r_good          # locked despite offbeat bass
    assert abs(phase_good) < 0.05
    assert r_bad < 0.15, r_bad           # +1% twin reads as sweeping
    assert r_good > r_bad * 3


def test_gate_catches_phase_shifted_grid():
    import numpy as np
    from validate_beatgrid import _grade, verdict_from
    rng = np.random.default_rng(11)
    period = 60.0 / 128.0
    grid = np.array([0.5 + i * period for i in range(700)])
    onsets = np.sort(grid + rng.normal(0.0, 0.015, len(grid)))
    shifted = grid + 0.18 * period       # the Todd failure: tempo right, phase off
    r, phase, _ = _grade(onsets, shifted, period)
    verdict, detail = verdict_from(r, phase, 0.02)
    assert r > 0.6                       # tempo still locked
    assert abs(phase) > 0.12
    assert verdict == "FAIL" and "PHASE" in detail


def test_verdict_boundaries():
    from validate_beatgrid import verdict_from
    assert verdict_from(0.70, 0.02, 0.02)[0] == "PASS"
    assert verdict_from(0.14, 0.01, 0.02)[0] == "FAIL"   # tempo (Kelly)
    assert verdict_from(0.56, 0.15, 0.02)[0] == "FAIL"   # phase (Todd)
    assert verdict_from(0.32, 0.03, 0.01)[0] == "WARN"   # borderline (Samm)
    assert verdict_from(0.50, 0.01, 0.30)[0] == "FAIL"   # too close to control


def test_grid_mode_guards_zero_length_and_monotonic():
    grid = make_grid(128.0, 700)
    _, downbeat = grid_bpm_and_downbeat(grid, 0)
    spb = 4 * 60.0 / 128.0
    near = downbeat + 8 * spb
    res = {"bpm": 128.0, "sections": [
        {"label": "intro", "start_bar": 0, "end_bar": 8,
         "start_sec": downbeat, "end_sec": near},
        {"label": "fill", "start_bar": 8, "end_bar": 8,   # collapses to same bar
         "start_sec": near, "end_sec": near + 0.05},
        {"label": "drop", "start_bar": 8, "end_bar": 16,
         "start_sec": near + 0.05, "end_sec": downbeat + 16 * spb},
    ]}
    segs = segments_from_stem_sections(res, beat_times_ms=grid, first_downbeat_offset=0)
    prev_end = None
    for s in segs:
        assert s.source_end_beats > s.source_start_beats   # never zero/negative
        if prev_end is not None:
            assert s.source_start_beats >= prev_end        # monotonic
        prev_end = s.source_end_beats
