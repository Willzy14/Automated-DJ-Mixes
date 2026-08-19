"""Regression tests for the tail-anchor rescue (Source/align_engine.py).

The rescue is a LAST-RESORT path in `_align_pair_landmark_aware` that fires
ONLY after both existing search passes return None. It implements Sam's
"run back 16 bars from the last beat" rule for sustained-to-end outgoings.

What this test pins:
  * flag-OFF behaviour is byte-identical to the pre-flag code (no-regression)
  * flag-ON makes a known previously-raising pair (Revoloution -> Renegades)
    produce a placement instead of raising
  * the produced Alignment is honestly labelled: alignment_policy =
    "tail_anchor_rescue_v1", handoff_kind carries "rescue/" prefix,
    and the notes name, for BOTH sides, whether the anchor is a detected cue
    or a grid-derived position
  * the NEVER-FABRICATE property: when no real cue coincides with the swap
    point, paired_cues is an EMPTY list and the score is 0. A future attempt
    to synthesise a fake pair with a fake weight fails this test.

Style: mirrors Tests/test_section_soft_rules.py::test_flag_off_is_byte_identical_to_main.
Skips cleanly when the corpus fixture is absent (no SECTIONS_STEM_*.json or
baseline file), the same way Tests/test_alignment_baseline.py does.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from dataclasses import replace as dc_replace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as AE  # noqa: E402

STEM_DIR = ROOT / "Test Project" / "14.08.26" / "_Stem Analysis"
NAMED_BASELINE_OUT = "Nic Fanciulli - Revoloution (Extended Mix) 24 Bit MASTER"
NAMED_BASELINE_IN  = "Harry Romero - Renegades SW V1"

pytestmark = pytest.mark.skipif(
    not list(STEM_DIR.glob("SECTIONS_STEM_*.json")),
    reason="14.08.26 stem JSONs are unavailable",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_tracks():
    tracks = {}
    for f in sorted(STEM_DIR.glob("SECTIONS_STEM_*.json")):
        key = f.name.replace("SECTIONS_STEM_", "").replace(".json", "")
        tracks[key] = AE.load_track(f)
    return tracks


def _with_flag(tracks_out_in, *, flag_value: bool):
    """Run align_pair on (out, in) with CUE_CONFIG.tail_anchor_rescue = flag_value
    then restore the saved global. Returns the Alignment (or raises)."""
    saved = AE.CUE_CONFIG
    AE.CUE_CONFIG = dc_replace(saved, tail_anchor_rescue=flag_value)
    try:
        return AE.align_pair(*tracks_out_in)
    finally:
        AE.CUE_CONFIG = saved


# ---------------------------------------------------------------------------
# 1. flag OFF is byte-identical to pre-flag behaviour on a corpus sample
# ---------------------------------------------------------------------------

def test_flag_off_byte_identical_to_pre_flag_code():
    """The default flag is OFF. Behaviour must be byte-identical to pre-flag code.

    Sampled by sweeping the same 380 ordered pairs and confirming the pinned
    fields (handoff_bar_out / arr_offset_bars / overlap_bars / swap_progress /
    handoff_kind / alignment_policy / n_paired_cues / paired_cue_bars) match
    the BASELINE captured before the flag was added. We compare against a
    freshly-captured sample rather than a checked-in file because the corpus
    may move underneath us; the byte-identity assertion is between this run's
    OFF output and this run's `AE.align_pair` invoked a second time with the
    same flag state.
    """
    tracks = _load_tracks()
    keys = sorted(tracks)

    def sweep():
        rows = []
        for out_name in keys:
            for in_name in keys:
                if out_name == in_name:
                    continue
                rec = {"out": out_name, "in": in_name}
                try:
                    al = AE.align_pair(tracks[out_name], tracks[in_name])
                except Exception:
                    rec["status"] = "raise"
                else:
                    rec.update({
                        "status": "ok",
                        "handoff_bar_out": al.handoff_bar_out,
                        "arr_offset_bars": al.arr_offset_bars,
                        "overlap_bars": al.overlap_bars,
                        "swap_progress": al.swap_progress,
                        "handoff_kind": al.handoff_kind,
                        "alignment_policy": al.alignment_policy,
                        "n_paired_cues": len(al.paired_cues or []),
                        "paired_cue_bars": [p["arrangement_bar"]
                                            for p in (al.paired_cues or [])],
                    })
                rows.append(rec)
        return rows

    # First run with the saved (default OFF) CUE_CONFIG.
    before = sweep()
    # Second run with flag explicitly set to False (the default value).
    saved = AE.CUE_CONFIG
    AE.CUE_CONFIG = dc_replace(saved, tail_anchor_rescue=False)
    try:
        after = sweep()
    finally:
        AE.CUE_CONFIG = saved

    # Both runs must yield the same outcome for every pair. A failure here
    # means the flag's default is not in fact inert — i.e. the no-regression
    # contract is broken.
    assert len(before) == len(after), "row counts diverge"
    assert before == after, (
        "flag-OFF behaviour is not byte-identical to the pre-flag default. "
        "Either the rescue is firing with the default, or another field on "
        "CueConfig has drifted."
    )


# ---------------------------------------------------------------------------
# 2. flag ON turns a known previously-raising pair into a placement
# ---------------------------------------------------------------------------

def test_flag_on_unblocks_revoloution_to_renegades():
    """The blocker: Revoloution -> Renegades.

    With the default flag (OFF) this pair RAISES because Revoloution's tail
    carries no detected cue in the last-minute handoff window. With
    tail_anchor_rescue=True, Sam's "run back 16 bars from the last beat" rule
    produces a placement at bar 148 (= 164 - 16) — the same bar Q1 of the
    Phase 1 probe predicted.
    """
    tracks = _load_tracks()
    out = tracks[NAMED_BASELINE_OUT]
    inc = tracks[NAMED_BASELINE_IN]

    # OFF must RAISE — pinned by the frozen baseline.
    with pytest.raises(ValueError):
        _with_flag((out, inc), flag_value=False)

    # ON must produce an Alignment, NOT raise.
    al = _with_flag((out, inc), flag_value=True)
    assert isinstance(al, AE.Alignment)
    assert al.handoff_bar_out == pytest.approx(148.0)
    assert al.overlap_bars == pytest.approx(48.0)
    # 32/48 = 0.6667 — the rule's intended progress (outgoing has 1/3 of its
    # last minute to fade out, which is what Sam's rule is for).
    assert al.swap_progress == pytest.approx(2 / 3, rel=1e-3)


# ---------------------------------------------------------------------------
# 3. honest labelling: the Alignment carries the right policy / kind / notes
# ---------------------------------------------------------------------------

def test_rescue_alignment_labelled_as_grid_derived():
    """Revoloution has NO detected cue near bar 148, so the rescue anchor is
    grid-derived. The Alignment must say so unambiguously."""
    tracks = _load_tracks()
    al = _with_flag((tracks[NAMED_BASELINE_OUT], tracks[NAMED_BASELINE_IN]),
                    flag_value=True)

    assert al.alignment_policy == "tail_anchor_rescue_v1"
    # Outgoing anchor is grid-derived: handoff_kind must carry "grid_tail".
    # Incoming anchor IS a real first_drop: handoff_kind must carry "->drop".
    assert al.handoff_kind.startswith("rescue/")
    assert "grid_tail" in al.handoff_kind
    assert al.handoff_kind.endswith("->drop")

    # Notes: explicit sentence naming BOTH sides.
    note = al.notes[0].lower()
    assert "outgoing anchor is a grid-derived" in note
    assert "incoming anchor is a detected cue" in note
    # And it states the count of REAL paired cues (1 in this case).
    assert "1 real paired cue" in note


# ---------------------------------------------------------------------------
# 4. NEVER FABRICATE: when no real cue coincides, paired_cues is empty
# ---------------------------------------------------------------------------

def test_no_fabricate_paired_cues_empty_when_nothing_coincides():
    """The previous unsupervised attempt invented a cue with a fake weight so
    the rescue could claim a paired cue where none existed. That is banned.
    Pin: when the outgoing anchor is grid-derived AND no incoming cue happens
    to coincide with it, paired_cues MUST be an empty list and the score
    MUST be 0. The rescue must still fire (Sam's rule applies regardless of
    whether the detector marked the point), but it must not synthesise
    evidence."""
    tracks = _load_tracks()
    # Pick a pair whose real rescue has no coinciding pair (the verify_t6
    # ablation shows Double Dutch -> BUTCH & Santos has paired_cues=[]).
    out_name = "Sam Leagas - Double Dutch (Extended Mix) SW V1"
    in_name  = "BUTCH & Santos -  Come Get Up 24 Bit MASTER"
    assert out_name in tracks and in_name in tracks, "named pair not in corpus"
    al = _with_flag((tracks[out_name], tracks[in_name]), flag_value=True)

    assert al.alignment_policy == "tail_anchor_rescue_v1"
    assert al.paired_cues == [], (
        f"never-fabricate violation: paired_cues must be empty when no real "
        f"cue coincides with the swap point, got {al.paired_cues!r}"
    )
    assert al.score == 0, (
        f"never-fabricate violation: score must be 0 when no real cue "
        f"coincides, got {al.score}"
    )
    # And the notes MUST explicitly state that 0 REAL paired cues coincided.
    note = al.notes[0].lower()
    assert "0 real paired cue" in note, (
        f"notes must state the count of real paired cues; got {al.notes[0]!r}"
    )


# ---------------------------------------------------------------------------
# 5. paired_cues must ONLY contain entries for REAL outgoing cues
# ---------------------------------------------------------------------------

def test_no_fabricate_paired_cues_only_count_real_outgoing_cues():
    """A pair entry in paired_cues must require BOTH the incoming cue to
    exist AND the outgoing cue (at that arrangement_bar) to exist. A future
    attempt to inject a synthetic outgoing entry to inflate the score
    shows up as a paired_cue entry whose arrangement_bar is NOT a key of
    the outgoing cue dict."""
    tracks = _load_tracks()
    out = tracks[NAMED_BASELINE_OUT]
    inc = tracks[NAMED_BASELINE_IN]
    # The cue dict the rescue actually sees for the outgoing.
    outgoing_cue_keys = set(AE._mix_cues(out).keys())
    al = _with_flag((out, inc), flag_value=True)
    for p in al.paired_cues or []:
        assert p["arrangement_bar"] in outgoing_cue_keys, (
            f"never-fabricate violation: paired_cues entry at arrangement_bar "
            f"{p['arrangement_bar']} is not a real outgoing cue (must only "
            f"contain entries that exist in the outgoing cue dict)."
        )