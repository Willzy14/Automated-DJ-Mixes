"""Pins for orchestrator._bpm_still_provisional (2026-09-02, Fable
second-lens review): the "provisional" BPM display flag must key off
whether the DISPLAYED bpm still equals the flagged librosa fallback, not
merely whether that warning is present in the list. A stem-grid or MIK
overwrite leaves the original warning sitting in a.warnings while
replacing the number it described - a presence-only check would flag the
authoritative BPM as provisional in exactly the runs that matter most.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from automated_dj_mixes.orchestrator import _bpm_still_provisional  # noqa: E402


def test_still_provisional_when_bpm_matches_the_warned_value():
    assert _bpm_still_provisional(129.2, ["BPM detected by librosa: 129.2"]) is True


def test_not_provisional_once_an_authoritative_source_overwrote_bpm():
    # The stem-grid/MIK overwrite path: a.bpm now differs from the number
    # the warning named - the warning is stale, not a live caveat.
    assert _bpm_still_provisional(126.0, ["BPM detected by librosa: 129.2"]) is False


def test_not_provisional_when_no_librosa_warning_present():
    assert _bpm_still_provisional(126.0, []) is False
    assert _bpm_still_provisional(126.0, ["some other warning"]) is False


def test_still_provisional_on_an_unparseable_warning():
    # Fail toward the more cautious label rather than silently trusting an
    # unexpected warning format.
    assert _bpm_still_provisional(126.0, ["BPM detected by librosa: not-a-number"]) is True


def test_tolerance_absorbs_rounding_not_a_real_difference():
    # 0.02 BPM apart is float noise from the same underlying value, not an
    # overwrite - must still read as provisional.
    assert _bpm_still_provisional(129.18, ["BPM detected by librosa: 129.2"]) is True
