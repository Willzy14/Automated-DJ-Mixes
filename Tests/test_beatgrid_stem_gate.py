"""Stem-grid beatgrid-gate holes (24.06.26 Afro/Latin + jackin' regression).

A stem grid that's structurally off its own kicks (88ms+) must FAIL the gate —
provenance ('drum-stem-kicks') is NOT a blanket pass. These pin verdict_from's
stem_fitted branch so the silent-bad-grid hole can't reopen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))
from validate_beatgrid import verdict_from, STEM_KF_FAIL_MS


def _verdict(stem_kf_ms, r_half=0.01):
    # r_half noise-floor (percussion-heavy house) + stem_fitted: R is bypassed,
    # so the verdict turns purely on grid_vs_kick.
    v, _ = verdict_from(r_half, 0.0, 0.5, tempo_confirmed=True,
                        phase_advisory=True, stem_fitted=True, stem_kf_ms=stem_kf_ms)
    return v


def test_stem_grid_on_kicks_passes():
    assert _verdict(0.8) == "PASS"
    assert _verdict(4.4) == "PASS"


def test_stem_grid_off_kicks_fails():
    # the 24.06.26 Afro/Latin + jackin' case
    assert _verdict(88.0) == "FAIL"
    assert _verdict(122.6) == "FAIL"


def test_threshold_boundary():
    assert _verdict(STEM_KF_FAIL_MS - 0.1) == "PASS"
    assert _verdict(STEM_KF_FAIL_MS + 0.1) == "FAIL"


def test_missing_kf_falls_back_to_pass():
    # no grid_vs_kick supplied (e.g. legacy override) -> don't block on it
    assert _verdict(None) == "PASS"
