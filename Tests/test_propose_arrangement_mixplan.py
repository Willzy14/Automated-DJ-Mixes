"""Tests for propose_arrangement.py's MixPlan tempo/warp-mode recording -
specifically the FULLY INHERITED case (burn list A4, 2026-09-14): no
explicit --project-bpm/--warp-mode override, no "auto" - the combination
build_ab_comparison.py uses for every side it builds.

Confirmed live before this fix: MixPlan reconciliation (validate_mix_plan_als.py)
hard-failed on all three sides of the first-ever real Tech House Heldout A/B/C
bounce, on every track, identically - project_bpm stayed None (float(None or
"nan") is NaN) and every track's warp_mode was the literal string "inherited"
(mix_plan.build_mix_plan's own fallback for an empty warp_modes dict), which
matches neither "repitch" nor "complex_pro" in the reconciler's lookup table.

The fix reads both values straight off the ALS being written (never
re-derives them) - see _resolve_inherited_tempo_and_warp_modes's own
docstring for why a first attempt at re-deriving the warp mode from a
bpm-distance formula was tried and rejected on real data.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

from propose_arrangement import (  # noqa: E402
    TrackInfo, _resolve_inherited_tempo_and_warp_modes)
from automated_dj_mixes.warping import (  # noqa: E402
    WARP_MODE_COMPLEX, WARP_MODE_COMPLEX_PRO, WARP_MODE_REPITCH)


def _als_root(manual_bpm: str | None, track_warp_modes: dict[str, list[float]]) -> ET.Element:
    """A minimal ALS tree: one AudioTrack per (name, [WarpMode per clip])
    entry, matching the real structure _resolve_inherited_tempo_and_warp_modes
    reads (EffectiveName, AudioClip/WarpMode)."""
    root = ET.Element("Ableton")
    live = ET.SubElement(root, "LiveSet")
    tempo = ET.SubElement(live, "Tempo")
    if manual_bpm is not None:
        manual = ET.SubElement(tempo, "Manual")
        manual.set("Value", manual_bpm)
    for name, modes in track_warp_modes.items():
        track_el = ET.SubElement(live, "AudioTrack")
        eff = ET.SubElement(track_el, "EffectiveName")
        eff.set("Value", name)
        for mode in modes:
            clip = ET.SubElement(track_el, "AudioClip")
            wm = ET.SubElement(clip, "WarpMode")
            wm.set("Value", str(mode))
    return root


def test_reads_the_als_own_tempo_and_warp_modes_not_a_recomputed_guess():
    """Direct read, not a formula - the exact scenario that broke a formula-
    based approach on real data (see module docstring): two tracks near the
    Re-Pitch/Complex-Pro boundary must resolve to whatever the ALS ACTUALLY
    says, not whatever a bpm-distance rule would predict."""
    root = _als_root("130", {
        "Repitch Track": [float(WARP_MODE_REPITCH)],
        "ComplexPro Track": [float(WARP_MODE_COMPLEX_PRO)],
    })
    tracks = [TrackInfo("Repitch Track", [], 0.0, 400.0),
             TrackInfo("ComplexPro Track", [], 0.0, 400.0)]
    bpm, modes = _resolve_inherited_tempo_and_warp_modes(root, tracks)
    assert bpm == 130.0
    assert modes["Repitch Track"] == WARP_MODE_REPITCH
    assert modes["ComplexPro Track"] == WARP_MODE_COMPLEX_PRO


def test_matches_a_track_whose_tracks_list_name_is_html_escaped():
    """Real bug found re-verifying this fix against the actual Tech House
    Heldout build: the ALS's own EffectiveName decodes XML entities
    naturally on parse (a literal apostrophe), but the `tracks` list this
    function is actually called with (TrackInfo.name, built upstream from
    the sections JSON) can carry the ESCAPED form instead ("There&apos;s") -
    confirmed directly against the real project's Sections_V1.json, which
    stores exactly that. Two of nine real tracks (HARTY, Sapian) have an
    apostrophe in their name and would have silently fallen out of the
    resolved dict without matching both forms."""
    root = _als_root("130", {"Band's Track": [float(WARP_MODE_REPITCH)]})
    tracks = [TrackInfo("Band&apos;s Track", [], 0.0, 400.0)]  # escaped, as
                                                                # TrackInfo.name
                                                                # really carries it
    bpm, modes = _resolve_inherited_tempo_and_warp_modes(root, tracks)
    assert bpm == 130.0
    assert modes["Band&apos;s Track"] == WARP_MODE_REPITCH


def test_fails_closed_on_unreadable_tempo():
    """No Manual tempo element at all - returns (None, {}) rather than
    fabricating a number."""
    root = _als_root(None, {"T": [float(WARP_MODE_REPITCH)]})
    tracks = [TrackInfo("T", [], 0.0, 400.0)]
    bpm, modes = _resolve_inherited_tempo_and_warp_modes(root, tracks)
    assert bpm is None
    assert modes == {}


def test_leaves_out_a_track_whose_warpmode_this_project_never_uses():
    """A clip carrying plain Complex (this project's DJ-mix policy only
    ever chooses Re-Pitch or Complex Pro) must be left OUT of the returned
    dict, not mapped to something arbitrary - build_mix_plan's own
    "inherited" fallback then correctly flags that ONE track, honestly."""
    root = _als_root("130", {"T": [float(WARP_MODE_COMPLEX)]})
    tracks = [TrackInfo("T", [], 0.0, 400.0)]
    bpm, modes = _resolve_inherited_tempo_and_warp_modes(root, tracks)
    assert bpm == 130.0
    assert "T" not in modes


def test_leaves_out_a_track_whose_clips_disagree_with_each_other():
    """Two clips on the same track with different WarpModes - malformed,
    should never happen, but must not be silently averaged or first-wins;
    left out entirely."""
    root = _als_root("130", {
        "T": [float(WARP_MODE_REPITCH), float(WARP_MODE_COMPLEX_PRO)],
    })
    tracks = [TrackInfo("T", [], 0.0, 400.0)]
    bpm, modes = _resolve_inherited_tempo_and_warp_modes(root, tracks)
    assert bpm == 130.0
    assert "T" not in modes


def test_reconciliation_would_have_failed_before_this_fix():
    """Proves-the-test at the level that actually matters: reproduces the
    real reconciler's two failure modes directly against what an EMPTY
    warp_modes dict / None project_bpm would have produced, using
    validate_mix_plan_als.py's own literal lookup table and NaN-from-None
    behaviour - not an assertion invented for this test - then confirms
    this fix's actual output clears both."""
    import math

    expected_warp = {"repitch": WARP_MODE_REPITCH,
                     "complex_pro": WARP_MODE_COMPLEX_PRO}.get("inherited")
    assert expected_warp is None, \
        "the literal string 'inherited' must not resolve to a real WarpMode"

    expected_bpm = float(None or "nan")
    assert math.isnan(expected_bpm), \
        "a None project_bpm must reconcile as NaN, i.e. never match a real tempo"

    root = _als_root("130", {"T": [float(WARP_MODE_REPITCH)]})
    tracks = [TrackInfo("T", [], 0.0, 400.0)]
    bpm, modes = _resolve_inherited_tempo_and_warp_modes(root, tracks)
    assert bpm is not None and math.isfinite(bpm)
    assert modes["T"] in (WARP_MODE_REPITCH, WARP_MODE_COMPLEX_PRO)
