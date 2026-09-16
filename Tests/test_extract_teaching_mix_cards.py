"""Tests for extract_teaching_mix_cards.py - the Teaching Mixes case-study
extractor (Sam, 2026-09-16: "this is not for the bots... this is for you as
an AI looking for several different answers"). Report-only, human/Claude-
read library - never wired into propose_arrangement.py's decisions.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

from extract_teaching_mix_cards import (  # noqa: E402
    AutomationPoint, TrackInfo, TrackDirectAutomation, ZoneAutomation, MixExtraction,
    _value_to_db, _summarize_curve, find_transitions, build_card, _track_zone,
    _track_direct_automation,
)

TEACHING_MIXES = ROOT / "Teaching Mixes"


def test_value_to_db_matches_the_known_ableton_curve():
    # Inverse of automated_dj_mixes.als_generator._db_to_ableton_volume
    # (value = 10**(db/20)) - hand-verified against real values seen in
    # the actual files (0.0003162277571 is the recurring "fader off" value).
    assert _value_to_db(1.0) == pytest.approx(0.0, abs=1e-6)
    assert _value_to_db(0.5) == pytest.approx(-6.0206, abs=1e-3)
    assert _value_to_db(0.0003162277571) == pytest.approx(-70.0, abs=0.01)
    assert _value_to_db(0.0) == -70.0  # floored, never -inf


def test_summarize_curve_catches_a_dip_that_returns_to_the_start_value():
    # The real bug found against actual data: start==end (both 64) masked a
    # genuine dip to 32 in the middle. Must be reported, not called "flat".
    pts = [AutomationPoint(284.0 * 4, 64.0), AutomationPoint(288.0 * 4, 32.0),
           AutomationPoint(319.9 * 4, 64.0)]
    out = _summarize_curve(pts, lambda v: round(v, 1), "/64", snap_threshold=10.0)
    assert "dips to 32.0" in out
    assert "flat" not in out


def test_summarize_curve_reports_a_spike_above_both_ends():
    pts = [AutomationPoint(0, 0.0), AutomationPoint(4, 10.0), AutomationPoint(8, 0.0)]
    out = _summarize_curve(pts, lambda v: round(v, 1), "u", snap_threshold=100.0,
                           excursion_threshold=2.0)
    assert "spikes to 10.0" in out


def test_summarize_curve_detects_a_hard_snap_within_the_window():
    pts = [AutomationPoint(0, 1.0), AutomationPoint(2, 0.1)]  # 0.5 bar gap, big jump
    out = _summarize_curve(pts, lambda v: round(v, 2), "x", snap_threshold=0.5,
                           snap_window_bars=1.0)
    assert "SNAP" in out


def test_summarize_curve_empty_is_reported_honestly():
    assert _summarize_curve([], lambda v: v, "", snap_threshold=1.0) == "no automation in this window"


def test_summarize_curve_never_claims_precision_it_does_not_have():
    # A single point: no shape claim beyond "starts X" - never invents a
    # ramp or hold from one sample.
    pts = [AutomationPoint(40.0, 0.7)]
    out = _summarize_curve(pts, lambda v: round(v, 2), "x", snap_threshold=0.1)
    assert out == "starts 0.7x @ bar 10.0"


def _track(order, name, zone, arr_start, arr_end):
    return TrackInfo(order=order, name=name, zone=zone, clips=[],
                     arr_start=arr_start, arr_end=arr_end)


def test_find_transitions_only_pairs_genuinely_overlapping_adjacent_tracks():
    tracks = [
        _track(1, "A", "Zone A", 0.0, 100.0),
        _track(2, "B", "Zone B", 80.0, 200.0),   # overlaps A: real transition
        _track(3, "C", "Zone A", 250.0, 350.0),  # no overlap with B: not a transition
    ]
    mix = MixExtraction(als_path=Path("x.als"), ableton_version="test",
                        tracks=tracks, zones={}, locators=[])
    pairs = find_transitions(mix)
    assert len(pairs) == 1
    assert pairs[0][0].name == "A" and pairs[0][1].name == "B"


def test_find_transitions_drops_a_whole_mix_reference_track():
    # The real pattern found in discovery: one track's clip spans the whole
    # mix (bar 0-2000) while every real track spans ~50-100 bars. Must not
    # be treated as 17 fake "transitions" into/out of it.
    tracks = [_track(i, f"T{i}", "Zone A", i * 40.0, i * 40.0 + 80.0) for i in range(1, 6)]
    tracks.append(_track(99, "Reference Import", None, 0.0, 2000.0))
    mix = MixExtraction(als_path=Path("x.als"), ableton_version="test",
                        tracks=tracks, zones={}, locators=[])
    pairs = find_transitions(mix)
    assert all(a.name != "Reference Import" and b.name != "Reference Import"
              for a, b in pairs)


def test_build_card_flags_same_zone_transitions_as_not_a_crossfade():
    a = _track(1, "A", "Zone A", 0.0, 100.0)
    b = _track(2, "B", "Zone A", 80.0, 120.0)
    mix = MixExtraction(als_path=Path("x.als"), ableton_version="test",
                        tracks=[a, b], zones={}, locators=[])
    card = build_card(mix, a, b)
    assert "Same zone on both sides" in card


def test_build_card_reports_unresolved_zone_honestly_not_silently():
    a = _track(1, "A", None, 0.0, 100.0)
    b = _track(2, "B", "Zone B", 80.0, 120.0)
    za = ZoneAutomation(zone_name="Zone B")
    mix = MixExtraction(als_path=Path("x.als"), ableton_version="test",
                        tracks=[a, b], zones={"Zone B": za}, locators=[])
    card = build_card(mix, a, b)
    assert "not resolved" in card
    assert "no zone assigned" in card


def test_build_card_never_claims_same_zone_when_both_are_unresolved():
    # Independent review found this exactly, 2026-09-16: `out_t.zone ==
    # in_t.zone` is True when BOTH are None, firing a fabricated "same
    # zone... direct edit/layer" claim on 188 of 269 real cards (70%) -
    # every transition where NEITHER side's zone was resolved. Confirmed
    # directly against the real corpus before this fix.
    a = _track(1, "A", None, 0.0, 100.0)
    b = _track(2, "B", None, 80.0, 120.0)
    mix = MixExtraction(als_path=Path("x.als"), ableton_version="test",
                        tracks=[a, b], zones={}, locators=[])
    card = build_card(mix, a, b)
    assert "Same zone on both sides" not in card


def test_track_zone_resolves_by_send_position_not_the_raw_id_attribute():
    # Independent review found this exactly, 2026-09-16, confirmed against
    # the real Defected CD2/CD3 files: TrackSendHolder's own `Id="N"`
    # attribute is an internal object id, NOT a stable 0-based zone slot -
    # tracks 1-2 in those real files carry Ids "2,3,4" for the same three
    # sends every later track numbers "0,1,2". Using the raw Id as a
    # zone-name lookup key silently mis-resolved (or lost) the first two
    # transitions of both rich files. Position (order of appearance) is
    # the only reliable signal.
    body = (
        '<TrackSendHolder Id="2"><Manual Value="1" /></TrackSendHolder>'
        '<TrackSendHolder Id="3"><Manual Value="0.0003162277571" /></TrackSendHolder>'
        '<TrackSendHolder Id="4"><Manual Value="0.0003162277571" /></TrackSendHolder>'
    )
    zone_names = {"0": "A-Zone", "1": "B-Zone", "2": "C-Reverb"}
    assert _track_zone(body, zone_names) == "A-Zone"  # 1st send (position 0), not zone_names["2"]


def _arranger_block(param_tag, points):
    events = "".join(f'<FloatEvent Time="{t}" Value="{v}" />' for t, v in points)
    return (f"<{param_tag}><ArrangerAutomation><Events>{events}</Events>"
            f"</ArrangerAutomation></{param_tag}>")


def test_track_direct_automation_classifies_volume_gainlo_and_cutoff():
    # Real shapes confirmed against Gbox Side 1 and Tapesh Mix, round 2
    # discovery (2026-09-16): the OLDER per-clip ArrangerAutomation
    # mechanism, keyed by the parameter's own XML tag name (no
    # AutomationTarget/PointeeId indirection like the zone-bus mechanism).
    body = (
        _arranger_block("Volume", [(0, 1.0), (4, 0.5)])
        + _arranger_block("GainLo", [(0, 0.5), (4, 0.13)])
        + _arranger_block("Cutoff", [(0, 20), (4, 68.8)])
        + _arranger_block("DryWet", [(0, 0.3), (4, 0.7)])  # not a mix move - ignored
    )
    ta = _track_direct_automation(body)
    assert [p.value for p in ta.volume_points] == [1.0, 0.5]
    assert [p.value for p in ta.bass_gain_points] == [0.5, 0.13]
    assert [p.value for p in ta.cutoff_points] == [20, 68.8]


def test_track_direct_automation_ignores_single_point_blocks():
    # A block with exactly one FloatEvent is a static "set once" value (an
    # On/Off toggle, or a parameter that was simply left at its default) -
    # not real automation. Matches _zone_automation's own discipline.
    body = _arranger_block("Volume", [(0, 1.0)])
    ta = _track_direct_automation(body)
    assert ta.volume_points == []


@pytest.mark.skipif(not TEACHING_MIXES.exists(), reason="Teaching Mixes/ unavailable")
def test_real_file_with_rich_automation_resolves_zone_routing_and_macro_label():
    """Pinned against a real file, proved by hand during discovery
    (2026-09-16): tracks alternate onto two zone returns via sends, each
    zone carries real Volume automation and a MacroControls.0 automation
    from a live-played MIDI-mapped knob."""
    from extract_teaching_mix_cards import extract
    path = TEACHING_MIXES / "Defected In The House - Ibiza 2026 (CD 2 Of 2) [Defected].als"
    mix = extract(path)
    assert mix.ableton_version == "Ableton Live 12.3.2"
    real_tracks = [t for t in mix.tracks if t.zone and t.zone.startswith(("A-Zone", "B-Zone"))]
    assert len(real_tracks) >= 15  # 17 real songs, one stray reference track excluded
    zone_a = mix.zones.get("A-Zone 62 DJ EQ")
    assert zone_a is not None
    assert len(zone_a.volume_points) > 0
    assert len(zone_a.filter_points) > 0
    assert zone_a.filter_label == "MacroControls.0"
    assert zone_a.filter_confirmed is False  # macro number confirmed, underlying target is not


@pytest.mark.skipif(not TEACHING_MIXES.exists(), reason="Teaching Mixes/ unavailable")
def test_real_file_with_no_zone_automation_still_honest_about_the_zone_side():
    """Pinned against a real file (Gbox Side 1) confirmed to carry zero
    ZONE-bus `<AutomationEnvelope>` entries anywhere - this file genuinely
    has no zone-bus routing setup, so the ZONE-side fields must stay
    explicitly empty/unresolved for it, never a fabricated value.

    Renamed and narrowed after round 2 (Claude subagent review, 2026-09-16):
    the original test claimed this file had NO automation at all "mixed
    live, nothing written" - false. It has 56 real curves via the older
    per-CLIP `<ArrangerAutomation>` mechanism, now extracted separately
    (see test_real_file_with_direct_track_automation below). This test's
    job narrows to: the ZONE-bus reading specifically stays honestly empty
    for a file that has no zone-bus setup - it must not fabricate zone data
    just because SOME other automation exists on the file."""
    import gzip
    from extract_teaching_mix_cards import extract
    path = TEACHING_MIXES / "Gbox Side 1 CB Final SW V1.als"

    with gzip.open(path, "rb") as f:
        raw = f.read().decode("utf-8", errors="replace")
    assert raw.count("<AutomationEnvelope ") == 0  # the file itself, not just this parser's read of it

    mix = extract(path)
    assert mix.ableton_version == "Ableton Live 9.7"
    for za in mix.zones.values():
        assert za.volume_points == []
        assert za.filter_points == []
    transitions = find_transitions(mix)
    assert len(transitions) > 0
    for out_t, in_t in transitions:
        card = build_card(mix, out_t, in_t)
        assert "zone (" not in card  # no zone-attributed automation line for this file


@pytest.mark.skipif(not TEACHING_MIXES.exists(), reason="Teaching Mixes/ unavailable")
def test_real_file_with_direct_track_automation_extracted_correctly():
    """Round 2 finding (Claude subagent review, 2026-09-16): Gbox Side 1 -
    previously reported as having "zero automation, mixed live" - actually
    carries 56 real curves via the older per-clip ArrangerAutomation
    mechanism, directly on individual song tracks' own Volume/GainLo/Cutoff
    parameters. Pinned here against the real file so this extraction path
    cannot silently regress back to reporting nothing."""
    from extract_teaching_mix_cards import extract
    path = TEACHING_MIXES / "Gbox Side 1 CB Final SW V1.als"
    mix = extract(path)

    tracks_with_direct_data = [
        t for t in mix.tracks if t.direct_automation is not None and (
            t.direct_automation.volume_points or t.direct_automation.bass_gain_points
            or t.direct_automation.cutoff_points)
    ]
    assert len(tracks_with_direct_data) > 0

    transitions = find_transitions(mix)
    cards_with_direct_automation = [
        build_card(mix, a, b) for a, b in transitions
        if "direct automation" in build_card(mix, a, b)
    ]
    assert len(cards_with_direct_automation) > 0


@pytest.mark.skipif(not TEACHING_MIXES.exists(), reason="Teaching Mixes/ unavailable")
def test_real_corpus_coverage_is_pinned_against_regression():
    """MiniMax review, 2026-09-16: the INDEX's coverage claims were manual
    observations with no automated check behind them - a regression in
    either extraction mechanism's regex would silently shift these numbers
    with nothing to catch it. Pinned here the same way
    test_alignment_baseline.py and test_canonicalize_pair_history's
    real-corpus test pin their own numbers: re-verify by hand before
    updating this pin if the real Teaching Mixes/ corpus itself changes.

    Extended, round 2 (2026-09-16): after adding direct-track-automation
    extraction (the ArrangerAutomation mechanism the round-2 review found
    this module was missing), 19 of 20 files now have SOME real extracted
    automation (5 via the zone bus, 14 via direct track devices, zero
    overlap between the two sets) - only ONE file (Gbox Side 3) has
    neither, because its real ArrangerAutomation curves are all on
    parameters this module doesn't classify as a mix move (DryWet/Send/
    Tempo, not Volume/GainLo/Cutoff)."""
    from extract_teaching_mix_cards import extract, find_transitions

    files = sorted(TEACHING_MIXES.glob("*.als"))
    assert len(files) == 20

    zone_rich = set()
    direct_rich = set()
    total_transitions = 0
    for f in files:
        mix = extract(f)
        total_transitions += len(find_transitions(mix))
        if any(za.volume_points or za.filter_points for za in mix.zones.values()):
            zone_rich.add(f.name)
        if any(t.direct_automation and (t.direct_automation.volume_points
                                       or t.direct_automation.bass_gain_points
                                       or t.direct_automation.cutoff_points)
              for t in mix.tracks):
            direct_rich.add(f.name)

    assert len(zone_rich) == 5
    assert len(direct_rich) == 14
    assert zone_rich.isdisjoint(direct_rich)
    assert len(zone_rich | direct_rich) == 19
    assert total_transitions == 269
