"""Tests for suspect_passages_report.py (burn list D6).

Report-only: must never abort the build even on malformed/missing data,
and a transition with nothing to flag is OMITTED entirely so the list
stays short and worth reading.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from suspect_passages_report import (  # noqa: E402
    build_report_lines,
    generate_suspect_passages_report,
)


def _report(transitions: list[dict], tracks: list[dict] | None = None) -> dict:
    return {"tracks": tracks or [], "transitions": transitions}


def test_only_the_flagged_transition_appears_clean_ones_omitted():
    report = _report([
        {"out_track": "A", "in_track": "B", "vocal_clash_ranges": [
            {"clash_range": (10.0, 20.0), "outgoing_range": (5.0, 25.0),
             "incoming_range": (10.0, 30.0)},
        ], "density_status": "", "density_score": None},
        {"out_track": "B", "in_track": "C", "vocal_clash_ranges": [],
         "density_status": "", "density_score": None},
        {"out_track": "C", "in_track": "D", "vocal_clash_ranges": [],
         "density_status": "", "density_score": None},
    ], tracks=[{"name": "A", "bpm": 128.0}, {"name": "B", "bpm": 128.0}])

    lines = build_report_lines(report)
    text = "\n".join(lines)

    assert "A -> B:" in text
    assert "B -> C:" not in text
    assert "C -> D:" not in text


def test_no_suspect_passages_states_that_plainly_not_empty():
    report = _report([
        {"out_track": "A", "in_track": "B", "vocal_clash_ranges": [],
         "density_status": "", "density_score": None},
    ])

    lines = build_report_lines(report)

    assert lines == ["No suspect passages flagged this build."]


def test_completely_empty_report_does_not_crash():
    lines = build_report_lines({})
    assert lines == ["No suspect passages flagged this build."]


def test_malformed_transition_entries_do_not_crash_the_report():
    """Garbage/missing fields on a transition must produce a sensible
    result, not an exception - the whole point of report-only is that a
    computation problem here can never take down the pipeline."""
    report = _report([
        {},  # completely empty transition dict
        {"out_track": "A", "in_track": "B"},  # missing vocal_clash_ranges/density entirely
        {"out_track": "C", "in_track": "D", "vocal_clash_ranges": None},
    ])

    lines = build_report_lines(report)  # must not raise

    assert lines == ["No suspect passages flagged this build."]


def test_density_spread_summary_appears_when_measured_values_exist():
    report = _report([
        {"out_track": "A", "in_track": "B", "vocal_clash_ranges": [],
         "density_status": "measured", "density_score": 1.0},
        {"out_track": "B", "in_track": "C", "vocal_clash_ranges": [],
         "density_status": "measured", "density_score": 3.0},
        {"out_track": "C", "in_track": "D", "vocal_clash_ranges": [],
         "density_status": "unmeasured: no candidate", "density_score": None},
    ])

    lines = build_report_lines(report)
    text = "\n".join(lines)

    assert "Entry-extension density (2 measured)" in text
    assert "min +1.0dB" in text
    assert "max +3.0dB" in text


def test_vocal_clash_converts_to_mmss_when_track_bpm_is_known():
    report = _report([
        {"out_track": "A", "in_track": "B", "vocal_clash_ranges": [
            {"clash_range": (240.0, 248.0), "outgoing_range": (232.0, 248.0),
             "incoming_range": (240.0, 256.0)},
        ], "density_status": "", "density_score": None},
    ], tracks=[{"name": "A", "bpm": 128.0}])

    lines = build_report_lines(report)
    text = "\n".join(lines)

    # 232 beats @ 128bpm, 4/4: 232 / (128*4/60) = 27.1875s = 0:27.2
    assert "vocal clash:" in text
    assert "A vocal @" in text
    assert "B vocal @" in text


def test_vocal_clash_falls_back_to_beats_when_no_bpm_on_record():
    report = _report([
        {"out_track": "A", "in_track": "B", "vocal_clash_ranges": [
            {"clash_range": (240.0, 248.0), "outgoing_range": (232.0, 248.0),
             "incoming_range": (240.0, 256.0)},
        ], "density_status": "", "density_score": None},
    ], tracks=[])  # no BPM anywhere

    lines = build_report_lines(report)  # must not raise
    text = "\n".join(lines)

    assert "no BPM on record" in text


def test_null_tracks_and_transitions_do_not_crash_the_report():
    """.get(key, []) only substitutes the default when the key is ABSENT -
    a key present with value null (legal JSON) still returns None and
    would crash the loop. Regression test for the MiniMax code review
    finding, 2026-09-15."""
    lines = build_report_lines({"tracks": None, "transitions": None})
    assert lines == ["No suspect passages flagged this build."]


def test_generate_suspect_passages_report_includes_header_and_body():
    report = _report([
        {"out_track": "A", "in_track": "B", "vocal_clash_ranges": [],
         "density_status": "", "density_score": None},
    ])

    full = generate_suspect_passages_report(report)

    assert full.startswith("# Suspect Passages")
    assert "Report-only" in full
    assert "No suspect passages flagged this build." in full
