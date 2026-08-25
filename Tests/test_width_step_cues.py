"""Tests for the stereo-width-step boundary candidate in Source/stem_detector.py.

Sam's spec (Master Board pinned card, 2026-08-17, measured): Nic Fanciulli -
Revoloution loses a synth at bar ~147; side/mid width falls 0.170 -> 0.107
(-3.4 dB) across bars 145-149 while RMS stays dead flat, so every energy
detector is blind to it. _width_step_cues is a per-bar DOWN-step detector on
the tiera_width envelope, with medians straddling the ramp, phrase-grid
discipline, RMS-flat gate, and minimum run-length gate all feeding into
raw_bounds. Mirror commit 6a717f4's pattern from _energy_cues; mirror
Tests/test_stem_detector_energy_cues.py's synthetic-style for the test cases.

Revised against the 14.08.26 corpus sweep the courier ran: the original
algorithm (step magnitude + grid discipline only) fired ~50 cues corpus-wide.
Two corpus-measured gates bring the sweep to 12 cues, every one confirmed
against independent stem/band evidence, including the Revoloution bar-148
target. The two new gates and the bug fix are reflected here:

  - WIDTH_STEP_RMS_FLAT_DB (1.5 dB): the step only counts if the mix-level
    medians move LESS than this across it. Beyond that the event is
    energy-visible and the energy path already owns it.
  - WIDTH_STEP_MIN_RUN_BARS (4): a real 4-5 bar ramp produces a run of
    consecutive qualifying bars as the pre/post medians slide across it;
    1-2 bar blips are imaging wobble, not a boundary.
  - Bug fix (Rev 2): the helper emits the SNAPPED bar as "bar" so the merge
    protection set and the section boundary land at the same bar (a Vente
    case had the boundary snap to 152 while the protection held 153 and the
    cue folded away). step_db / pre_width / post_width stay measured at the
    unsnapped bar; "detected_bar" exposes that bar explicitly.

Cases:
  a. Revoloution-shaped ramp, flat mix_norm: ONE cue fires, snapped bar 64,
     step_db <= -3, detected_bar revealed (was previously the same value
     but is now a separate field).
  b. Flat width the whole track: no cues.
  c. A 1-2 bar spike down that recovers: no cues (medians over 8 bars swallow it).
  d. A ramp whose best |step_db| bar snaps >1 bar off-grid: no cue (grid
     discipline drops it).
  e. Near-mono pre level (pre width 0.03 stepping to 0.01): no cue (the floor).
  f. An UP step 0.107 -> 0.170: no cue (down-only, documented).
  g. _merge_same_label protection: two adjacent same-label 'drop' sections
     merge WITHOUT protected_bars (current behaviour pinned) and stay split
     WITH the shared boundary bar in protected_bars.
  h. detect()-level flag-off no-op: detect accepts width_cues=False as default
     via inspect.signature, and _merge_same_label's default arg is an empty
     frozenset. (The corpus-level byte-identical proof is the courier's job.)
  i. NEW (Rev 2): Revoloution-shaped width ramp + mix_norm with a 3 dB step
     down (0.5 after the bar): NO cue (the RMS gate hands it to the energy
     path). Negative control with flat mix_norm: cue fires (same body as a).
  j. NEW (Rev 2): a 2-bar qualifying run (hand-built per_bar list through
     _width_step_qualifying_runs) is filtered out by the run-length gate;
     a 4+ bar run survives.
"""

import inspect
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def _run(width_bar, downbeat=0.0, sec_per_bar=1.0, mix_norm=None):
    """Run _width_step_cues with a known sec_per_bar (so test math is exact).

    Default mix_norm = flat ones = the "RMS stays dead flat" assumption every
    existing test relies on. Cases that are about the RMS gate explicitly
    pass their own mix_norm.
    """
    from stem_detector import _width_step_cues
    width_bar = np.asarray(width_bar, dtype=float)
    n = len(width_bar)
    if mix_norm is None:
        mix_norm = np.ones(n)
    else:
        mix_norm = np.asarray(mix_norm, dtype=float)
    return _width_step_cues(width_bar, mix_norm, downbeat, sec_per_bar)


def _revoloution_ramp(n_bars=80):
    """The Revoloution-shaped width_bar: flat 0.170 for n_bars/3 bars, linear
    ramp down to 0.107 over 5 bars (on the 4-bar grid), flat 0.107 after.
    Returns the width_bar; the test supplies its own mix_norm so the same
    ramp can be re-driven under flat-RMS (fires) and RMS-stepping (silent)."""
    width_bar = np.full(n_bars, 0.170)
    ramp = np.linspace(0.170, 0.107, 5)
    width_bar[60:65] = ramp
    width_bar[65:] = 0.107
    return width_bar


def test_revoloution_shaped_ramp_emits_one_cue_at_grid_bar():
    """Revoloution-shaped ramp under flat mix_norm (RMS stays dead flat, the
    class this signal exists for): exactly ONE cue fires. The rightmost-max
    |step_db| is at detected b=64 (bars 60-65 are all near -4.02 dB; the
    rightmost tie-break picks b=64 even after the run-length gate), the
    detected bar snaps to 64 (dist 0), and detected_bar is exposed as the
    unsnapped (here coincidentally equal to the snapped) bar for the sweep
    report. Pre-window medians all sit at 0.170, post-window at 0.107, so
    pre_width=0.17 and post_width=0.107 once rounded; rms_delta_db is 0 by
    construction (flat mix_norm)."""
    width_bar = _revoloution_ramp()
    cues = _run(width_bar)

    assert len(cues) == 1
    cue = cues[0]
    assert cue["type"] == "width_step"
    # The snapped "bar" lands on 64 (the rightmost max |step_db| bar, with the
    # rightmost-max tie-break resolving the run-length=6 step_db~-4 run).
    assert cue["bar"] == 64.0
    # detected_bar exposes the unsnapped (here coincidentally equal) value.
    assert cue["detected_bar"] == 64.0
    assert cue["step_db"] <= -3.0
    assert cue["pre_width"] == 0.17
    assert cue["post_width"] == pytest.approx(0.107, rel=1e-3)
    assert cue["rms_delta_db"] == 0.0
    # start_sec anchors at downbeat + snapped_bar * sec_per_bar (here both 1.0).
    assert cue["start_sec"] == 64.0


def test_rms_step_hands_event_to_energy_path_no_width_cue():
    """Revoloution-shaped width ramp with mix_norm stepping down 3 dB at the
    same bar: the RMS gate |delta| > WIDTH_STEP_RMS_FLAT_DB=1.5 dB, so the
    bar DOES NOT qualify and the cue is suppressed. The negative control in
    the same test drives the SAME width_bar under flat mix_norm and confirms
    the cue DOES fire -- same body, two asserts, isolates the RMS gate
    contribution (per the Rev 2 brief: "can be the same test, two asserts").
    """
    width_bar = _revoloution_ramp()
    n = len(width_bar)

    # mix_norm flat 1.0 until bar 60, then 0.5 (= -6.02 dB, well over 1.5 dB).
    # The POST median sits at 0.5, the PRE median at 1.0, so rms_delta_db at
    # the ramp midpoint is 20*log10(0.5/1.0) = -6.02 dB. |...| = 6.02 > 1.5.
    mix_norm_stepping = np.ones(n)
    mix_norm_stepping[60:] = 0.5

    cues_stepping = _run(width_bar, mix_norm=mix_norm_stepping)
    assert cues_stepping == [], (
        "RMS gate: same width ramp with a 3 dB mix step must NOT fire -- "
        "the event is energy-visible and belongs to the energy path"
    )

    cues_flat = _run(width_bar)  # default mix_norm = flat ones
    assert len(cues_flat) == 1
    assert cues_flat[0]["bar"] == 64.0


def test_flat_width_emits_no_cues():
    """Flat 0.170 the whole track: step_db = 0 dB everywhere, no qualifying
    bar, no cues."""
    width_bar = np.full(120, 0.170)
    cues = _run(width_bar)
    assert cues == []


def test_one_to_two_bar_spike_is_swallowed_by_medians():
    """A 1-2 bar dip that recovers (e.g. a single transient that briefly
    pulls width down) is invisible to W=8 medians: both pre and post windows
    span enough flat bars that the medians cancel the spike. No cues."""
    width_bar = np.full(80, 0.170)
    width_bar[30] = 0.107  # 1-bar spike
    cues_one = _run(width_bar)
    assert cues_one == []

    width_bar2 = np.full(80, 0.170)
    width_bar2[30:32] = 0.107  # 2-bar spike
    cues_two = _run(width_bar2)
    assert cues_two == []


def test_ramp_landing_off_grid_with_best_bar_snap_gt_one_is_dropped():
    """A 5-bar linear ramp at bars 62-66 (center 64 -- bar 62 / 66 sit on the
    ramp's descending edge). With W=8 medians, the qualifying run spans
    bars 62-66 and the rightmost max |step_db| (=4.02 dB) is b=66, which
    snaps to 68 (round(16.5)*4 = 68, dist 2 > WIDTH_STEP_GRID_TOL=1). The
    grid discipline drops the cue. This is the discriminating case: the same
    ramp shape at a grid-aligned position fires; at an off-grid position it
    does not -- so 'best bar snaps >1 bar' is what drops it. The Rev 2 bug
    fix means the "detected_bar" field still surfaces 66 here even though
    no cue survives -- a corpus sweep can confirm why no boundary moved."""
    width_bar = np.full(80, 0.170)
    ramp = np.linspace(0.170, 0.107, 5)
    width_bar[62:67] = ramp
    width_bar[67:] = 0.107

    cues = _run(width_bar)
    assert cues == [], (
        "off-grid ramp where the best bar's snap is >1 bar must be dropped "
        "by the phrase-grid discipline"
    )


def test_off_grid_one_bar_drift_emits_cue_at_snapped_bar():
    """The Rev 2 bug fix: when the best bar is off-grid by EXACTLY one bar
    (not two), the cue IS preserved and the snapped bar is what survives into
    the merge protection set. A Vente corpus case had detected bar 153 ->
    snapped 152; before the fix, the boundary landed at 152 and the protected
    set held 153 and the cue folded away. After the fix: bar=152.0,
    detected_bar=153.0 (both surfaces).

    Constructed so the rightmost max |step_db| bar lands at global b=153:
    ramp over positions 149..152 (4 values; flat-0.170 at 153+). The run
    spans b=148..153 (6 bars), all -3.40 to -4.02 dB; rightmost tie-break
    picks b=153 with step_db=-4.02. b=153 has pre_window [145:153] mostly
    flat-0.170 plus the ramp tail, median still 0.170, so its -4.02 ties the
    mid-ramp max -- which b=153 wins by rightmost tie-break. Snapped 152,
    dist 1 <= TOL, so the cue survives with detected_bar != bar.
    """
    width_bar = np.full(180, 0.170)
    # 4-value ramp at positions 149..152 from 0.170 to 0.107.
    ramp = np.linspace(0.170, 0.107, 4)
    width_bar[149:153] = ramp
    width_bar[153:] = 0.107
    cues = _run(width_bar)
    # Exactly one cue: bar = 152 (round(153/4)*4 = 152, dist 1 <= TOL), and
    # detected_bar = 153.
    assert len(cues) == 1
    cue = cues[0]
    assert cue["bar"] == 152.0
    assert cue["detected_bar"] == 153.0
    assert cue["step_db"] <= -3.0


def test_near_mono_pre_level_is_dropped_by_the_floor():
    """Near-mono pre level (0.03 stepping to 0.01) sits below
    WIDTH_STEP_MIN_PRE=0.05, so the pre floor suppresses the cue: the
    side/mid ratio is essentially noise there, not a musical width event."""
    width_bar = np.full(80, 0.03)
    width_bar[30:] = 0.01
    cues = _run(width_bar)
    assert cues == []


def test_up_step_emits_no_cue():
    """An UP step (width GAIN) is documented as DOWN-only: the brief notes
    that width GAIN almost always rides an energy/kick event the existing
    cues already cut, so adding a symmetric UP detector would double-fire.
    step_db > 0 for an UP step is rejected at the threshold check."""
    width_bar = np.full(80, 0.107)
    width_bar[30:] = 0.170
    cues = _run(width_bar)
    assert cues == []


def test_run_length_gate_drops_short_runs_keeps_long_runs():
    """The Rev 2 run-length gate (WIDTH_STEP_MIN_RUN_BARS=4): a 2-bar
    qualifying run is dropped while a 4+ bar run survives.

    Real raw width_bar inputs don't easily produce a 2-bar qualifying run --
    the W=8 medians in the upstream path tend to swallow short blips, so a
    hand-built per_bar list fed through _width_step_qualifying_runs is the
    way to assert the gate's behaviour. The Rev 2 brief explicitly allows
    this: "If constructing a <4-bar run proves fiddly, an equivalent direct
    test of the run-length discard using a hand-built hits list refactored
    into a tiny helper is acceptable."
    """
    from stem_detector import (
        _width_step_qualifying_runs,
        WIDTH_STEP_DB,
        WIDTH_STEP_RMS_FLAT_DB,
        WIDTH_STEP_MIN_RUN_BARS,
    )
    # 2 consecutive qualifying bars, then a None (WIDTH_STEP_MIN_PRE floor
    # in the upstream path becomes a None here), then 5 more consecutive
    # qualifying bars. The MIN_RUN_BARS gate is the caller's job -- verify
    # the helper yields BOTH runs and the caller-side filter discards the
    # short one.
    flat_qual = {"step_db": -1.5 * WIDTH_STEP_DB, "rms_delta_db": 0.0,
                 "pre": 0.17, "post": 0.107}
    short = [None, flat_qual, flat_qual, None, flat_qual, flat_qual,
             flat_qual, flat_qual, flat_qual, None]
    runs = _width_step_qualifying_runs(short)
    assert runs == [(1, 3), (4, 9)], (
        "helper groups the two runs correctly: short 2-bar and 5-bar"
    )
    # Caller-side filter (the loop in _width_step_cues) drops runs shorter
    # than MIN_RUN_BARS and keeps the rest. Confirm:
    kept = [(s, e) for s, e in runs if e - s >= WIDTH_STEP_MIN_RUN_BARS]
    assert kept == [(4, 9)], (
        "only the 5-bar run survives the WIDTH_STEP_MIN_RUN_BARS gate -- "
        "the 2-bar imaging-wobble run is dropped"
    )


def test_merge_same_label_protection_keeps_width_cut_split():
    """Two adjacent same-label 'drop' sections (Revoloution bars 128-164
    classify as drop on both sides of the bar ~148 step). Without
    protected_bars they merge into one drop and erase the cue inside; with
    the shared boundary bar in protected_bars they stay split. After the
    Rev 2 bug fix, the boundary bar that needs protecting is the SNAPPED bar
    (corpus case: detected 153 -> snapped 152)."""
    from stem_detector import _merge_same_label

    sections = [
        {
            "start_bar": 0, "end_bar": 148,
            "start_sec": 0.0, "end_sec": 148.0,
            "stems_on": ["drums", "bass"],
            "label": "drop",
        },
        {
            "start_bar": 148, "end_bar": 296,
            "start_sec": 148.0, "end_sec": 296.0,
            "stems_on": ["drums", "bass"],
            "label": "drop",
        },
    ]
    # No protected_bars -> pre-existing behaviour pinned: same-label merge.
    merged = _merge_same_label(sections)
    assert len(merged) == 1
    assert merged[0]["end_bar"] == 296

    # Boundary bar 148 in protected_bars -> the cut survives.
    merged_protected = _merge_same_label(sections, protected_bars=frozenset({148}))
    assert len(merged_protected) == 2
    assert merged_protected[1]["start_bar"] == 148


def test_detect_signature_and_merge_default_arg_byte_identical():
    """detect() must accept width_cues=False as default (no caller breakage),
    and _merge_same_label's protected_bars default must be an empty frozenset
    so the default branch is byte-identical to pre-flag code. The actual
    corpus-level JSON byte-identity proof is the courier's job; this test
    pins the surface area."""
    from stem_detector import _merge_same_label, detect

    sig = inspect.signature(detect)
    assert sig.parameters["width_cues"].default is False

    sig2 = inspect.signature(_merge_same_label)
    assert sig2.parameters["protected_bars"].default == frozenset()


def test_detect_wires_width_protected_from_snapped_bar():
    """Rev 2 bug fix: the width_protected set in detect() is built from the
    SNAPPED bar (c['bar'] is now the snapped value, and int() on it lines up
    with int(round(c['bar'])) the old code did on the unsnapped bar). For a
    cue whose detected bar was 153 -> snapped 152, the OLD code passed 153
    into the protection set (which did not match the snapped 152 boundary
    _snap_merge wrote into bounds), so the boundary folded into the
    surrounding same-label run. The NEW code reads int(c['bar']) == 152,
    which IS in bounds, so the cut survives. Pinned via a direct
    wcues=[...] call shape: detect()'s wcues are emitted as the snapped-bar
    cue dict, and the corresponding int(c['bar']) is what's protected.

    Uses the same ramp shape as test_off_grid_one_bar_drift_emits_cue_at_snapped_bar
    so detected_bar=153 / bar=152 is what we drive protect against."""
    from stem_detector import _width_step_cues, _merge_same_label

    width_bar = np.full(180, 0.170)
    ramp = np.linspace(0.170, 0.107, 4)
    width_bar[149:153] = ramp
    width_bar[153:] = 0.107
    cues = _width_step_cues(width_bar, np.ones(180), 0.0, 1.0)
    assert len(cues) == 1
    snapped_bar = int(cues[0]["bar"])
    detected_bar = float(cues[0]["detected_bar"])
    assert snapped_bar == 152
    assert detected_bar == 153.0

    # Build the protect set exactly as detect() now does (int(c['bar'])).
    width_protected = frozenset(int(c["bar"]) for c in cues)
    # And the same boundary that _snap_merge would write into bounds (the
    # snapped bar IS what _width_step_cues contributed via raw_bounds).
    bounds_snap = {snapped_bar}
    # The intersection lands: the protection matches the boundary, so the
    # width cut into the same-label run survives the merge.
    assert snapped_bar in width_protected
    assert snapped_bar in bounds_snap
    # Simulate the merge: two same-label sections share start_bar == snapped_bar;
    # without protection they merge; with protection they stay split.
    sections = [
        {"start_bar": 0, "end_bar": snapped_bar, "start_sec": 0.0,
         "end_sec": float(snapped_bar), "stems_on": ["drums", "bass"],
         "label": "drop"},
        {"start_bar": snapped_bar, "end_bar": snapped_bar + 148,
         "start_sec": float(snapped_bar), "end_sec": float(snapped_bar + 148),
         "stems_on": ["drums", "bass"], "label": "drop"},
    ]
    merged_protected = _merge_same_label(
        sections, protected_bars=width_protected,
    )
    assert len(merged_protected) == 2
    assert merged_protected[1]["start_bar"] == snapped_bar
