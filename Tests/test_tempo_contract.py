"""The tempo arc, frozen into the immutable contract.

Absolute (beat, bpm) points are the executable authority. Their brittleness is
the FEATURE: they are only meaningful against the geometry they were computed
from, so any later change invalidates the contract instead of letting a curve
quietly describe a mix that no longer exists.

The refusals below all guard the same failure - a curve that moves tempo while
one track is playing alone, which is exactly what a DJ would hear.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "Source"))

from test_mix_plan import _arrangement, _hash  # reuse the existing fixtures


def _tempo(points, held=(120.0, 121.0), native=(120.0, 121.0), exposures=(0.0,)):
    from automated_dj_mixes.mix_plan import TempoContract
    return TempoContract(
        strategy="tempo_arc_v1",
        points=tuple(points),
        held_bpms=tuple(held),
        native_bpms=tuple(native),
        solver="tempo_curve.solve_track_tempos/1",
        smoothing=2.0,
        max_step_bpm=1.5,
        max_span_stretch_pct=2.0,
        max_ramp_bpm_per_bar=0.1,
        ramp_exposures=tuple(exposures),
    )


def _build_with(tempo, arrangement=None):
    from automated_dj_mixes.mix_plan import build_one_transition_mix_plan
    from automated_dj_mixes.warp_contract import WarpGridSummary
    return build_one_transition_mix_plan(
        arrangement or _arrangement(),
        source_hashes={"out": _hash("a"), "in": _hash("b")},
        section_map_hashes={"out": _hash("c"), "in": _hash("d")},
        warp_grid_contracts={"out": WarpGridSummary(401, _hash("1"), 120.0),
                             "in": WarpGridSummary(405, _hash("2"), 121.0)},
        input_hashes={"sections_json": _hash("e"), "input_als": _hash("f")},
        policy_versions={"overlap": "safety_v1"},
        tool_versions={"planner": "mix_plan_v1"},
        tempo=tempo,
    )


def _window(arrangement):
    o = arrangement.overlaps[0]
    return o.overlap_start, o.overlap_end


def test_plan_without_a_tempo_contract_still_builds():
    """Backwards compatibility: existing fixed-tempo plans are untouched."""
    plan = _build_with(None)
    assert plan.tempo is None
    assert plan.schema_version == "1.4"


def test_valid_curve_is_frozen_into_the_plan():
    arrangement = _arrangement()
    start, end = _window(arrangement)
    plan = _build_with(_tempo([(0.0, 120.0), (start, 120.0), (end, 121.0)]),
                       arrangement)
    assert plan.tempo is not None
    assert plan.tempo.strategy == "tempo_arc_v1"
    assert plan.tempo.points[-1] == (end, 121.0)


def test_hash_covers_the_tempo_curve():
    """If the hash ignored the curve, the contract would not constrain it."""
    arrangement = _arrangement()
    start, end = _window(arrangement)
    a = _build_with(_tempo([(0.0, 120.0), (start, 120.0), (end, 121.0)]), arrangement)
    b = _build_with(_tempo([(0.0, 120.0), (start, 120.0), (end, 122.0)]),
                    _arrangement())
    assert a.plan_hash != b.plan_hash


def test_curve_for_the_wrong_number_of_tracks_is_refused():
    with pytest.raises(ValueError, match="covers 3 tracks"):
        _build_with(_tempo([(0.0, 120.0)], held=(120.0, 121.0, 122.0),
                           native=(120.0, 121.0, 122.0)))


def test_non_monotonic_points_are_refused():
    arrangement = _arrangement()
    start, end = _window(arrangement)
    with pytest.raises(ValueError, match="not monotonic"):
        _build_with(_tempo([(0.0, 120.0), (end, 121.0), (start, 120.0)]),
                    arrangement)


def test_a_point_outside_every_transition_is_refused():
    """This is the real guard: a tempo move landing where only ONE track plays."""
    arrangement = _arrangement()
    start, end = _window(arrangement)
    with pytest.raises(ValueError, match="outside every transition window"):
        _build_with(_tempo([(0.0, 120.0), (start, 120.0), (end + 500.0, 121.0)]),
                    arrangement)


def test_implausible_tempo_is_refused():
    arrangement = _arrangement()
    start, end = _window(arrangement)
    with pytest.raises(ValueError, match="implausible tempo point"):
        _build_with(_tempo([(0.0, 120.0), (start, 120.0), (end, 900.0)]),
                    arrangement)


def test_exposure_count_must_match_transitions():
    arrangement = _arrangement()
    start, end = _window(arrangement)
    with pytest.raises(ValueError, match="one exposure per transition"):
        _build_with(_tempo([(0.0, 120.0), (start, 120.0), (end, 121.0)],
                           exposures=(0.0, 0.0)), arrangement)
