"""Replay swap selection against the correction mix's own section maps.

This is the fitting data, not held-out data - passing here is a floor, not
evidence. Its job is to stop a fitted rule that REGRESSES a transition from
reaching Sam's listening test, which is what happened to
`prefer_intro_swap_with_drop_payoff` on 2026-08-12.

Skips when the Fresh Mix analysis artifacts are absent (they are gitignored),
in the same spirit as the existing golden-fixture skips.
"""

import glob
import os
from dataclasses import replace
from pathlib import Path

import pytest

from automated_dj_mixes.transition_policy import INTERIM_V1, SAM_V1

SECTIONS_GLOB = (
    "Test Project/16.07.26 Fresh Mix/_Stem Analysis Refined V2/"
    "SECTIONS_STEM_*.json"
)

#: (outgoing, incoming, label, the bar Sam actually swapped on)
CASES = [
    ("Same Thing", "Making Shapes", "T4", 16),
    ("Making Shapes", "Natural Child", "T5", 8),
    ("Get The Message", "Same Thing", "T3", 24),
]


def _sections():
    found = {os.path.basename(p): p for p in glob.glob(SECTIONS_GLOB)}
    if not found:
        pytest.skip("Fresh Mix section maps unavailable (gitignored artifacts)")
    return found


def _swap_bar(out_sub, in_sub, policy):
    from align_engine import _align_pair_landmark_aware, load_track

    found = _sections()

    def find(sub):
        return next(p for name, p in found.items() if sub.lower() in name.lower())

    outgoing = load_track(Path(find(out_sub)))
    incoming = load_track(Path(find(in_sub)))
    try:
        return _align_pair_landmark_aware(outgoing, incoming, policy).anchor_bar_in
    except ValueError:
        return None


@pytest.mark.parametrize("out_sub,in_sub,label,_sam", CASES)
def test_sam_v1_does_not_regress_what_the_default_already_matched(
    out_sub, in_sub, label, _sam
):
    """T3's default selection already equals Sam's choice (bar 24). sam_v1 must
    not move a transition the default got right."""
    if label != "T3":
        pytest.skip("only T3 is a default-already-correct case")
    assert _swap_bar(out_sub, in_sub, INTERIM_V1) == _swap_bar(out_sub, in_sub, SAM_V1)


def test_rejected_preference_is_off_in_the_shipped_policy():
    assert SAM_V1.prefer_intro_swap_with_drop_payoff is False


def test_rescue_ordering_contains_the_experimental_preference():
    """The drop pass runs first and, when it succeeds, the rescue pass never
    executes - so even enabling the rejected preference cannot re-decide a
    transition the default already handles.

    This is the containment that makes intro anchors safe to ship. Pooling the
    anchors instead moved T3 off Sam's bar 24; ordering them does not.
    """
    out_sub, in_sub = "Get The Message", "Same Thing"
    sam_choice = 24
    experimental = replace(SAM_V1, prefer_intro_swap_with_drop_payoff=True)
    assert _swap_bar(out_sub, in_sub, INTERIM_V1) == sam_choice
    assert _swap_bar(out_sub, in_sub, experimental) == sam_choice


def test_intro_anchors_alone_change_nothing_on_the_correction_mix():
    """Offering intro anchors without a selection rule is inert here - the
    reason the shipped sam_v1 is safe, and the reason it is not yet useful."""
    for out_sub, in_sub, _label, _sam in CASES:
        assert _swap_bar(out_sub, in_sub, INTERIM_V1) == \
            _swap_bar(out_sub, in_sub, SAM_V1)
