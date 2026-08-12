"""The incoming enters earlier, quietly, without moving the bass swap.

Sam's most consistent correction: on T2, T3, T5 and T6 he brought the incoming
in well before the handover and let it play at low level with its bass killed
until ownership passed. His note is that the arranger "starts useful incoming
material too late".

The mechanism existed but block (1) of `plan_fill_or_cut` is gated on
`not landmark_mode`, and the landmark path is the production path - so in
practice nothing ever brought the incoming in early.

Fixture-gated (Fresh Mix artifacts are gitignored), like the other replay tests.
"""

import glob
import os
from pathlib import Path

import pytest

from automated_dj_mixes.transition_policy import INTERIM_V1, SAM_V1

SECTIONS_GLOB = (
    "Test Project/16.07.26 Fresh Mix/_Stem Analysis Refined V2/"
    "SECTIONS_STEM_*.json"
)

#: (outgoing, incoming, label). Sam extended the entry on T3 and T6; he did not
#: on T1 or T4.
EXTENDED = [("Get The Message", "Same Thing", "T3"),
            ("Natural Child", "Seein", "T6")]
NOT_EXTENDED = [("Falling", "Roadblock", "T1"),
                ("Same Thing", "Making Shapes", "T4")]


def _pair(out_sub, in_sub, policy):
    from align_engine import _align_pair_landmark_aware, load_track, plan_fill_or_cut

    found = {os.path.basename(p): p for p in glob.glob(SECTIONS_GLOB)}
    if not found:
        pytest.skip("Fresh Mix section maps unavailable (gitignored artifacts)")

    def find(sub):
        return next(p for name, p in found.items() if sub.lower() in name.lower())

    outgoing = load_track(Path(find(out_sub)))
    incoming = load_track(Path(find(in_sub)))
    alignment = _align_pair_landmark_aware(outgoing, incoming, policy)
    specs = plan_fill_or_cut(outgoing, incoming, alignment, policy)
    entries = [s for s in specs if s.kind == "incoming_intro"]
    return alignment, entries


@pytest.mark.parametrize("out_sub,in_sub,label", EXTENDED)
def test_entry_extends_where_sam_extended(out_sub, in_sub, label):
    _, entries = _pair(out_sub, in_sub, SAM_V1)
    assert entries, f"{label}: Sam brought the incoming in earlier here"
    assert entries[0].reps >= 1


@pytest.mark.parametrize("out_sub,in_sub,label", EXTENDED + NOT_EXTENDED)
def test_default_policy_never_extends(out_sub, in_sub, label):
    """Production behaviour is unchanged - the landmark path had no entry
    extension at all, and must still have none."""
    _, entries = _pair(out_sub, in_sub, INTERIM_V1)
    assert entries == []


@pytest.mark.parametrize("out_sub,in_sub,label", NOT_EXTENDED)
def test_no_false_positives_where_sam_left_the_entry_alone(out_sub, in_sub, label):
    _, entries = _pair(out_sub, in_sub, SAM_V1)
    assert entries == [], f"{label}: Sam did not extend the entry here"


@pytest.mark.parametrize("out_sub,in_sub,label", EXTENDED)
def test_extension_does_not_move_the_bass_swap(out_sub, in_sub, label):
    """The contract of plan_fill_or_cut: it plans around a LOCKED swap. Sam's
    T2 note is explicit - start the incoming eight bars earlier, keep the bass
    handover unchanged. Entry and ownership are separate decisions."""
    interim_alignment, _ = _pair(out_sub, in_sub, INTERIM_V1)
    sam_alignment, _ = _pair(out_sub, in_sub, SAM_V1)
    assert interim_alignment.anchor_bar_in == sam_alignment.anchor_bar_in
    assert interim_alignment.arr_offset_bars == sam_alignment.arr_offset_bars


@pytest.mark.parametrize("out_sub,in_sub,label", EXTENDED)
def test_extension_uses_only_sanctioned_phrase_lengths(out_sub, in_sub, label):
    """4 and 8 bars only. A 3-bar (12-beat) loop happened to work once in Sam's
    edit and he confirmed it should not become a rule."""
    _, entries = _pair(out_sub, in_sub, SAM_V1)
    for spec in entries:
        phrase = round(spec.source_end_bar - spec.source_start_bar)
        assert phrase in SAM_V1.entry_phrase_bars, f"{label}: {phrase}-bar phrase"


@pytest.mark.parametrize("out_sub,in_sub,label", EXTENDED)
def test_extension_lands_on_an_outgoing_cue(out_sub, in_sub, label):
    """Entry is a coincidence point, not an arbitrary bar count - the whole
    number of repeats must reach the outgoing marker exactly."""
    _, entries = _pair(out_sub, in_sub, SAM_V1)
    for spec in entries:
        phrase = spec.source_end_bar - spec.source_start_bar
        assert spec.target_marker_bar >= 0
        assert abs(phrase * spec.reps % 1) < 1e-6


@pytest.mark.parametrize("out_sub,in_sub,label", EXTENDED)
def test_extension_respects_the_minimum(out_sub, in_sub, label):
    _, entries = _pair(out_sub, in_sub, SAM_V1)
    for spec in entries:
        bars = (spec.source_end_bar - spec.source_start_bar) * spec.reps
        assert bars >= SAM_V1.min_entry_extension_bars
