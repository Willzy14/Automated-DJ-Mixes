"""Tests for Source/validate_als.py OPTIONAL expectation layers (5-7).

Codex BLOCKER 6 hardening: a corrupt or incomplete ALS can still ship because
the corruption gate never checks track count vs job, device presence, or
transition-envelope presence. validate_als.py's layers 5-7 close that gap,
each gated by an OPTIONAL kwarg (None = layer skipped = existing callers
untouched).

Style mirrors Tests/test_arrangement_safety.py: synthetic gzipped XML
fixtures built with plain strings + gzip, pytest, tmp_path. Run from the
repo root with `PYTHONPATH=Source python -m pytest Tests -q`.
"""

import gzip
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def _make_als(path, tracks):
    """Write a gzipped minimal Ableton XML to `path` with one MainTrack
    followed by the AudioTracks described in `tracks`.

    `tracks` is a list of dicts:
        {"name": str,
         "clip": (time, end) | None,
         "devices": bool,
         "envelopes": [(pointee_id, [(t, v), ...]), ...],
         "envelope_sentinel": bool (default True; emits the
                                    Time="-63072000" FloatEvent before the
                                    real events when True)}

    Per-track AutomationTarget Ids are deterministic so tests can reference
    them: StereoGain's Gain AutomationTarget Id = 1000 + i*10,
    ChannelEq's LowShelfGain AutomationTarget Id = 1000 + i*10 + 1.

    Clip Times must be ascending across tracks in file order to pass the
    unchanged layer-4 (track ordering) gate.
    """
    track_xml_parts = []
    for i, t in enumerate(tracks):
        name = t["name"]
        clip = t.get("clip")
        devices = t.get("devices", False)
        envelopes = t.get("envelopes", [])
        emit_sentinel = t.get("envelope_sentinel", True)

        # Clip block (optional)
        clip_xml = ""
        if clip is not None:
            clip_time, clip_end = clip
            clip_xml = (
                f'<AudioClip Id="{900 + i}" Time="{clip_time}">'
                f'<Name Value="clip" />'
                f'<CurrentStart Value="{clip_time}" />'
                f'<CurrentEnd Value="{clip_end}" />'
                f'<LoopStart Value="0.0" />'
                f'<LoopEnd Value="{clip_end - clip_time}" />'
                f'</AudioClip>'
            )

        # Devices block (optional)
        gain_id = 1000 + i * 10
        eq_id = 1000 + i * 10 + 1
        devices_xml = ""
        if devices:
            devices_xml = (
                f'<DeviceChain><Devices>'
                f'<StereoGain Id="{2000 + i}"><Gain>'
                f'<AutomationTarget Id="{gain_id}" />'
                f'</Gain></StereoGain>'
                f'<ChannelEq Id="{2001 + i}"><LowShelfGain>'
                f'<AutomationTarget Id="{eq_id}" />'
                f'</LowShelfGain></ChannelEq>'
                f'</Devices></DeviceChain>'
            )

        # Envelopes
        envelope_parts = []
        for j, (pointee, pts) in enumerate(envelopes):
            events_xml_parts = []
            if emit_sentinel:
                events_xml_parts.append(
                    '<FloatEvent Id="9000" Time="-63072000" Value="1.0" />'
                )
            for k, (tt, vv) in enumerate(pts):
                events_xml_parts.append(
                    f'<FloatEvent Id="{9100 + k}" Time="{tt}" Value="{vv}" />'
                )
            envelope_parts.append(
                f'<AutomationEnvelope Id="{3000 + j}">'
                f'<EnvelopeTarget><PointeeId Value="{pointee}" /></EnvelopeTarget>'
                f'<Automation><Events>{"".join(events_xml_parts)}</Events></Automation>'
                f'</AutomationEnvelope>'
            )
        envelopes_xml = (
            '<AutomationEnvelopes><Envelopes>'
            + "".join(envelope_parts)
            + '</Envelopes></AutomationEnvelopes>'
        )

        track_xml = (
            f'<AudioTrack Id="{i}">'
            f'<Name><EffectiveName Value="{name}" /></Name>'
            f'<DeviceChain>'
            f'<MainSequencer><Sample><ArrangerAutomation><Events>'
            f'{clip_xml}'
            f'</Events></ArrangerAutomation></Sample></MainSequencer>'
            f'{devices_xml}'
            f'</DeviceChain>'
            f'{envelopes_xml}'
            f'</AudioTrack>'
        )
        track_xml_parts.append(track_xml)

    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Ableton MajorVersion="5" MinorVersion="0">\n'
        '<MainTrack>\n'
        '<Tempo>\n'
        '<Manual Value="120.0" />\n'
        '</Tempo>\n'
        '</MainTrack>\n'
        + "".join(track_xml_parts)
        + '\n</Ableton>\n'
    )

    # Sanity: emitted XML must round-trip through ET.fromstring before we
    # gzip it (one bad fixture has burned enough hours already).
    ET.fromstring(xml)

    with gzip.open(path, "wb") as f:
        f.write(xml.encode("utf-8"))


# ---------------------------------------------------------------------------
# Layer 5: expected track count
# ---------------------------------------------------------------------------


def test_no_expectations_backwards_compatible(tmp_path):
    """Without the new kwargs, validate_als MUST behave exactly as before.
    Two active tracks with no devices and no envelopes pass layers 1-4
    cleanly. Layers 5-7 are absent, so no errors."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 200.0), "devices": False, "envelopes": []},
        {"name": "B", "clip": (100.0, 400.0), "devices": False, "envelopes": []},
    ])
    assert validate_als.validate_als(p) == []


def test_expected_track_count_pass(tmp_path):
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 200.0), "devices": False, "envelopes": []},
        {"name": "B", "clip": (100.0, 400.0), "devices": False, "envelopes": []},
    ])
    assert validate_als.validate_als(p, expected_track_count=2) == []


def test_expected_track_count_catches_dropped_track(tmp_path):
    """The third clipless track must be ignored (layer 5 counts ONLY
    clip-bearing AudioTracks). The mismatch (2 vs 3) is the failure."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "Inactive", "clip": None, "devices": False, "envelopes": []},
        {"name": "A", "clip": (0.0, 200.0), "devices": False, "envelopes": []},
        {"name": "B", "clip": (100.0, 400.0), "devices": False, "envelopes": []},
    ])
    errs = validate_als.validate_als(p, expected_track_count=3)
    assert len(errs) == 1
    assert "Track count" in errs[0]
    assert "expects 3" in errs[0]


# ---------------------------------------------------------------------------
# Layer 6: mixer device presence
# ---------------------------------------------------------------------------


def test_device_check_pass(tmp_path):
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 200.0), "devices": True, "envelopes": []},
        {"name": "B", "clip": (100.0, 400.0), "devices": True, "envelopes": []},
    ])
    assert validate_als.validate_als(
        p, expected_track_devices=validate_als.MIXER_AUTOMATION_DEVICES
    ) == []


def test_device_check_catches_stripped_device(tmp_path):
    """An active track with devices=False fails layer 6 for BOTH
    (StereoGain, Gain) and (ChannelEq, LowShelfGain)."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "NoDevices", "clip": (0.0, 200.0), "devices": False,
         "envelopes": []},
        {"name": "A", "clip": (100.0, 400.0), "devices": True, "envelopes": []},
    ])
    errs = validate_als.validate_als(
        p, expected_track_devices=validate_als.MIXER_AUTOMATION_DEVICES
    )
    assert any("StereoGain" in e and "NoDevices" in e for e in errs)
    assert any("ChannelEq" in e and "NoDevices" in e for e in errs)


def test_device_check_ignores_inactive_tracks(tmp_path):
    """Layer 6 walks only ACTIVE tracks - a clipless track without
    devices must NOT trigger an error."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "Inactive", "clip": None, "devices": False, "envelopes": []},
        {"name": "Active", "clip": (0.0, 200.0), "devices": True, "envelopes": []},
    ])
    assert validate_als.validate_als(
        p, expected_track_devices=validate_als.MIXER_AUTOMATION_DEVICES
    ) == []


# ---------------------------------------------------------------------------
# Layer 7: transition envelope presence
# ---------------------------------------------------------------------------


def test_transition_envelope_pass(tmp_path):
    """Track A envelopes on BOTH its gain and eq ids, spanning 500..700;
    track B envelopes on BOTH its gain and eq ids, spanning 500..700.
    swap_beats=604.0 falls inside both ranges -> covered -> no errors."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 800.0), "devices": True,
         "envelopes": [
             (1000, [(500.0, 1.0), (700.0, 0.0)]),     # gain
             (1001, [(500.0, 0.5), (700.0, 0.5)]),     # eq
         ]},
        {"name": "B", "clip": (500.0, 1400.0), "devices": True,
         "envelopes": [
             (1010, [(500.0, 0.0), (700.0, 1.0)]),     # gain
             (1011, [(500.0, 0.5), (700.0, 0.5)]),     # eq
         ]},
    ])
    errs = validate_als.validate_als(
        p,
        expected_track_devices=__import__("validate_als").MIXER_AUTOMATION_DEVICES,
        expected_transitions=[{
            "out_track": "A",
            "in_track": "B",
            "swap_beats": 604.0,
        }],
    )
    assert errs == [], f"expected no errors, got: {errs}"


def test_transition_envelope_catches_missing_envelope(tmp_path):
    """Track B has NO envelopes; swap 604 is on the swap -> layer 7 fails
    for the in_track side. Track A has no transition automation either
    since it would be the out_track of this single transition."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 800.0), "devices": True,
         "envelopes": [
             (1000, [(500.0, 1.0), (700.0, 0.0)]),
             (1001, [(500.0, 0.5), (700.0, 0.5)]),
         ]},
        {"name": "B", "clip": (500.0, 1400.0), "devices": True,
         "envelopes": []},
    ])
    errs = validate_als.validate_als(
        p,
        expected_track_devices=__import__("validate_als").MIXER_AUTOMATION_DEVICES,
        expected_transitions=[{
            "out_track": "A",
            "in_track": "B",
            "swap_beats": 604.0,
        }],
    )
    assert len(errs) == 1
    assert "covers the swap" in errs[0]
    assert "B" in errs[0]


def test_transition_envelope_catches_non_covering_span(tmp_path):
    """Track B's envelope events span 900..1200 while swap=604 falls
    outside that window -> one error for B."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 800.0), "devices": True,
         "envelopes": [
             (1000, [(500.0, 1.0), (700.0, 0.0)]),
             (1001, [(500.0, 0.5), (700.0, 0.5)]),
         ]},
        {"name": "B", "clip": (500.0, 1400.0), "devices": True,
         "envelopes": [
             (1010, [(900.0, 0.0), (1200.0, 1.0)]),
             (1011, [(900.0, 0.5), (1200.0, 0.5)]),
         ]},
    ])
    errs = validate_als.validate_als(
        p,
        expected_track_devices=__import__("validate_als").MIXER_AUTOMATION_DEVICES,
        expected_transitions=[{
            "out_track": "A",
            "in_track": "B",
            "swap_beats": 604.0,
        }],
    )
    assert len(errs) == 1
    assert "covers the swap" in errs[0]
    assert "B" in errs[0]


def test_transition_envelope_ignores_non_mixer_pointee(tmp_path):
    """Track B's ONLY envelope points at PointeeId 99999 (not a mixer
    target), even though it covers swap=604 -> one error for B."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 800.0), "devices": True,
         "envelopes": [
             (1000, [(500.0, 1.0), (700.0, 0.0)]),
             (1001, [(500.0, 0.5), (700.0, 0.5)]),
         ]},
        {"name": "B", "clip": (500.0, 1400.0), "devices": True,
         "envelopes": [
             (99999, [(500.0, 0.0), (700.0, 1.0)]),
         ]},
    ])
    errs = validate_als.validate_als(
        p,
        expected_track_devices=__import__("validate_als").MIXER_AUTOMATION_DEVICES,
        expected_transitions=[{
            "out_track": "A",
            "in_track": "B",
            "swap_beats": 604.0,
        }],
    )
    assert len(errs) == 1
    assert "covers the swap" in errs[0]
    assert "B" in errs[0]


def test_transition_envelope_skips_null_swap(tmp_path):
    """A transition with swap_beats=None has no automation contract and
    must be SKIPPED entirely (no envelopes needed)."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 800.0), "devices": True, "envelopes": []},
        {"name": "B", "clip": (500.0, 1400.0), "devices": True, "envelopes": []},
    ])
    errs = validate_als.validate_als(
        p,
        expected_track_devices=__import__("validate_als").MIXER_AUTOMATION_DEVICES,
        expected_transitions=[{
            "out_track": "A",
            "in_track": "B",
            "swap_beats": None,
        }],
    )
    assert errs == []


def test_transition_envelope_unmatched_track_name(tmp_path):
    """A transition that names a track absent from the ALS must surface
    a 'not found unambiguously' error for that side."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 800.0), "devices": True, "envelopes": []},
        {"name": "B", "clip": (500.0, 1400.0), "devices": True, "envelopes": []},
    ])
    errs = validate_als.validate_als(
        p,
        expected_track_devices=__import__("validate_als").MIXER_AUTOMATION_DEVICES,
        expected_transitions=[{
            "out_track": "ZZZ not here",
            "in_track": "B",
            "swap_beats": 604.0,
        }],
    )
    assert any("not found" in e for e in errs)


def test_transition_envelope_xml_escaped_names(tmp_path):
    """Track name `Fish & Chips` in the ALS XML is emitted as the
    XML-escaped `Fish &amp; Chips`. Report track names match the
    arrangement-report convention (XML-escaped `&amp;`). Both sides
    normalize via html.unescape -> exact match -> no errors."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 800.0), "devices": True,
         "envelopes": [
             (1000, [(500.0, 1.0), (700.0, 0.0)]),
             (1001, [(500.0, 0.5), (700.0, 0.5)]),
         ]},
        {"name": "Fish &amp; Chips", "clip": (500.0, 1400.0), "devices": True,
         "envelopes": [
             (1010, [(500.0, 0.0), (700.0, 1.0)]),
             (1011, [(500.0, 0.5), (700.0, 0.5)]),
         ]},
    ])
    errs = validate_als.validate_als(
        p,
        expected_track_devices=__import__("validate_als").MIXER_AUTOMATION_DEVICES,
        expected_transitions=[{
            "out_track": "A",
            "in_track": "Fish &amp; Chips",  # as it appears in a real report
            "swap_beats": 604.0,
        }],
    )
    assert errs == [], f"expected no errors, got: {errs}"


# ---------------------------------------------------------------------------
# Sentinel handling
# ---------------------------------------------------------------------------


def test_sentinel_events_excluded(tmp_path):
    """Ableton's Time="-63072000" sentinel FloatEvent is a before-all-time
    default that real .als files carry on every envelope. Layer 7 must
    ignore it (use only real events where Time >= 0) when computing the
    span that must cover swap.

    Case 1: 1 sentinel + 1 real event at 604 -> span [604, 604] -> covers
            swap 604 -> no errors.
    Case 2: ONLY the sentinel event -> empty real-events list -> NOT
            covered -> one error.
    """
    import validate_als
    p = tmp_path / "fixture.als"

    # Track 0: A - sentinel + real event covering swap
    # Track 1: B - ONLY sentinel (no real events at all)
    p.write_bytes(b"")  # ensure path exists for the next call
    _make_als(p, [
        {"name": "A", "clip": (0.0, 800.0), "devices": True,
         "envelopes": [
             (1000, [(604.0, 1.0)]),
             (1001, [(604.0, 0.5)]),
         ]},
        {"name": "B", "clip": (500.0, 1400.0), "devices": True,
         "envelope_sentinel": True,
         "envelopes": [
             # No pts -> just the sentinel alone
             (1010, []),
             (1011, []),
         ]},
    ])
    errs = validate_als.validate_als(
        p,
        expected_track_devices=__import__("validate_als").MIXER_AUTOMATION_DEVICES,
        expected_transitions=[{
            "out_track": "A",
            "in_track": "B",
            "swap_beats": 604.0,
        }],
    )
    # A is covered (single-point span covers 604); B is NOT covered
    # because its only FloatEvent is the sentinel.
    assert len(errs) == 1
    assert "covers the swap" in errs[0]
    assert "B" in errs[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_flags(tmp_path, monkeypatch):
    """main() must accept the new flags; wrong --expected-tracks -> 2,
    correct -> 0. Pattern from test_arrangement_safety.py."""
    import validate_als
    p = tmp_path / "fixture.als"
    _make_als(p, [
        {"name": "A", "clip": (0.0, 200.0), "devices": False, "envelopes": []},
        {"name": "B", "clip": (100.0, 400.0), "devices": False, "envelopes": []},
    ])

    # Wrong expected track count -> non-zero exit
    monkeypatch.setattr(sys, "argv", [
        "validate_als.py",
        str(p),
        "--expected-tracks", "3",
    ])
    assert validate_als.main() == 2

    # Correct expected track count -> zero exit
    monkeypatch.setattr(sys, "argv", [
        "validate_als.py",
        str(p),
        "--expected-tracks", "2",
    ])
    assert validate_als.main() == 0
