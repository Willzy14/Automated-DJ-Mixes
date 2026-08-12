"""Bass ownership may pass inside an incoming intro, not only at a drop.

Grounded in the measured Sam-Tweaks correction. The section maps in
`Test Project/16.07.26 Fresh Mix/_Stem Analysis Refined V2/` say:

  Making Shapes  intro_1 = source beats 0-128  (32 bars), Sam swapped at 64  -> bar 16
  Natural Child  intro_1 = source beats 0-64   (16 bars), Sam swapped at 32  -> bar 8

Both are the intro midpoint and both sit on the phrase grid; neither is a
section boundary. The corrected clips carried the names intro_2/intro_1, but
clip names are stale after a manual split - the source clock is the truth
(Sam Tweaks lesson 7). A drop-only anchor list cannot express either swap.
"""

from types import SimpleNamespace

import pytest

from align_engine import (
    _incoming_drop_anchors,
    _incoming_intro_phrase_anchors,
    _incoming_swap_anchors,
)
from automated_dj_mixes.transition_policy import INTERIM_V1, SAM_V1


def _track(*sections):
    return SimpleNamespace(sections=[
        {"label": label, "start_bar": start, "end_bar": end}
        for label, start, end in sections
    ])


#: Making Shapes: 32-bar intro then a drop. Sam's swap was bar 16.
MAKING_SHAPES = _track(("intro", 0.0, 32.0), ("drop", 32.0, 96.0),
                       ("break", 96.0, 112.0), ("outro", 168.0, 194.0))

#: Natural Child: 16-bar intro then a drop. Sam's swap was bar 8.
NATURAL_CHILD = _track(("intro", 0.0, 16.0), ("drop", 16.0, 56.0),
                       ("break", 56.0, 64.0), ("outro", 176.0, 195.0))


def test_default_policy_offers_drops_only():
    """Production behaviour must be exactly what it was."""
    assert _incoming_swap_anchors(MAKING_SHAPES, INTERIM_V1) == \
        _incoming_drop_anchors(MAKING_SHAPES)
    assert 16 not in _incoming_swap_anchors(MAKING_SHAPES, INTERIM_V1)


def test_sam_v1_makes_the_measured_t4_swap_proposable():
    anchors = _incoming_swap_anchors(MAKING_SHAPES, SAM_V1)
    assert 16 in anchors, "bar 16 of a 32-bar intro is Sam's measured T4 swap"
    assert 32 in anchors, "the drop must still be offered"


def test_sam_v1_makes_the_measured_t5_swap_proposable():
    anchors = _incoming_swap_anchors(NATURAL_CHILD, SAM_V1)
    assert 8 in anchors, "bar 8 of a 16-bar intro is Sam's measured T5 swap"
    assert 16 in anchors


def test_intro_start_and_end_are_not_anchors():
    """Bar 0 swaps before the incoming establishes itself; the intro end IS the
    following drop, which the drop anchors already provide."""
    phrase = _incoming_intro_phrase_anchors(MAKING_SHAPES)
    assert 0 not in phrase
    assert 32 not in phrase


def test_phrase_anchors_stay_on_the_grid():
    for bar in _incoming_intro_phrase_anchors(MAKING_SHAPES):
        assert bar % 8 == 0, f"bar {bar} is off the 8-bar phrase grid"


def test_short_intro_yields_no_interior_phrase_point():
    """An 8-bar intro has no interior 8-bar phrase boundary, so nothing is
    invented - the drop remains the only ownership point."""
    short = _track(("intro", 0.0, 8.0), ("drop", 8.0, 64.0))
    assert _incoming_intro_phrase_anchors(short) == []
    assert _incoming_swap_anchors(short, SAM_V1) == _incoming_drop_anchors(short)


def test_anchors_are_capped_to_the_mixable_head():
    """A long intro must not offer ownership points deep into the track."""
    long_intro = _track(("intro", 0.0, 200.0), ("drop", 200.0, 260.0))
    assert max(_incoming_intro_phrase_anchors(long_intro)) <= 64.0


def test_policy_defaults_to_production_when_omitted():
    assert _incoming_swap_anchors(MAKING_SHAPES) == \
        _incoming_swap_anchors(MAKING_SHAPES, INTERIM_V1)


@pytest.mark.parametrize("track,expected", [(MAKING_SHAPES, 16), (NATURAL_CHILD, 8)])
def test_sam_v1_is_a_strict_superset_of_the_default(track, expected):
    """sam_v1 may only ADD candidates - never remove one the default offered,
    which would silently change transitions the default already got right."""
    default = set(_incoming_swap_anchors(track, INTERIM_V1))
    extended = set(_incoming_swap_anchors(track, SAM_V1))
    assert default <= extended
    assert expected in extended - default
