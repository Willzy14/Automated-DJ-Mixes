"""Tests for burn list C6's `bass_out_payoff` rank tier in
Source/align_engine.py's `_search_anchors`.

Real-world motivation (Tech House Heldout T2, Freejak -> HARTY): the
production pick was bar 164 (a `kick_dropout` landmark 16 bars after the
outgoing's own bass actually ends), NOT bar 148 (Freejak's own
`bass_out_bar`, the exact point this file's own top-of-module arrangement
model calls the natural swap point when bass doesn't run to the end). Both
are admissible, already-registered cues within the SAME (single) admissible
incoming anchor - the old rank tuple's `weighted_score` term (which sums
every coincidental cue-pair across the WHOLE overlap window, not just the
swap point) let bar 164 win by a ONE-POINT cue-coincidence edge, because the
tuple had no term at all for "is this the track's own natural bass-out."

The fixture below is a minimal, COMMITTED reproduction of that real shape
(not a dependency on the gitignored `Test Project/` corpus) - proved
independently against the pre-fix code (git-stashed `align_engine.py`,
re-run against this exact fixture): it picks bar 164 before the fix, bar 148
after. See `Documentation/BURN_LIST.md` item C6 for the full real-data
investigation (the `CUE_CONFIG.emit_bass_out` A/B that ruled out the
existing weight-boost mechanism first, and why a discrete tier is the
structurally correct fix rather than any cue-weight value).
"""
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as AE  # noqa: E402


def _track(name, n_bars, sections, *, bass_out=None, bass_out_is_end=False,
          landmarks=None):
    return AE.Track(
        name=name, bpm=129.2, spb=4 * 60.0 / 129.2, downbeat=0.0,
        n_bars=n_bars, sections=sections, bass_in_bar=0.0,
        bass_out_bar=bass_out, bass_out_is_end=bass_out_is_end,
        last_min_bars=32, musical_landmarks=landmarks or [],
    )


def _t2_shaped_outgoing(**overrides):
    """Freejak's real shape, reduced to the minimum that reproduces the
    flip: a drop ending (and bass ending) at bar 148, a kick-dropout
    landmark at bar 164 sixteen bars later."""
    defaults = dict(
        n_bars=165,
        sections=[
            {"name": "drop_1", "label": "drop", "start_bar": 116.0, "end_bar": 148.0},
            {"name": "outro_1", "label": "outro", "start_bar": 148.0, "end_bar": 165.0},
        ],
        bass_out=148.0, bass_out_is_end=False,
        landmarks=[{"landmark_id": "kd1", "type": "kick_dropout",
                    "start_bar": 164.0, "end_bar": 165.0}],
    )
    defaults.update(overrides)
    return _track("Outgoing", defaults.pop("n_bars"), defaults.pop("sections"),
                  **defaults)


def _t2_shaped_incoming():
    """HARTY's real shape, reduced: a single drop anchor at bar 16 (so
    `_incoming_drop_anchors` = [16], matching the real pair exactly - only
    ONE admissible incoming anchor, so this fixture also independently
    re-proves C6(b) would have been a no-op for this shape, same as the
    real pair)."""
    return _track("Incoming", 200, [
        {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 16.0},
        {"name": "drop_1", "label": "drop", "start_bar": 16.0, "end_bar": 200.0},
    ])


def test_bass_out_payoff_flips_the_real_t2_shape_to_the_natural_breakpoint():
    """The core fix: bar 148 (bass_out) now wins over bar 164 (a landmark
    with a marginally higher weighted_score/paired-count) - proved
    independently to flip FROM bar 164 against the reverted pre-fix code
    (see module docstring)."""
    al = AE.align_pair(_t2_shaped_outgoing(), _t2_shaped_incoming(),
                        AE._DEFAULT_POLICY)
    assert al.handoff_bar_out == 148.0
    assert al.overlap_bars == 33.0


def test_bass_out_is_end_excludes_the_payoff():
    """A bass-out that runs to the file's end must NOT get the payoff - that
    bar is already track_end, which can never be a valid swap (progress
    always hits 1.0); promoting it would create noise, not a usable anchor
    (matches the existing rationale already documented for
    CUE_CONFIG.emit_bass_out). With bass_out_is_end=True on the same
    shape, bar 164 must win exactly as it did before this fix."""
    outgoing = _t2_shaped_outgoing(bass_out=148.0, bass_out_is_end=True)
    al = AE.align_pair(outgoing, _t2_shaped_incoming(), AE._DEFAULT_POLICY)
    assert al.handoff_bar_out == 164.0


def test_null_bass_out_bar_does_not_crash_or_win():
    """bass_out_bar is nullable (round-3 finding 1) - a track with no
    measured bass-out must not crash the payoff check, and must not somehow
    win it either."""
    outgoing = _t2_shaped_outgoing(bass_out=None)
    al = AE.align_pair(outgoing, _t2_shaped_incoming(), AE._DEFAULT_POLICY)
    assert al.handoff_bar_out == 164.0


def test_payoff_only_ranks_an_already_admissible_cue_no_new_candidates():
    """No candidate generation: bass_out_bar pointed at a bar that is NOT
    any real outgoing cue (bar 148 and bar 164 stay exactly as in the T2
    fixture, both still real cues - only bass_out moves to 155, a bar with
    no section boundary or landmark at all) must never fire the payoff for
    either candidate, so the result is UNCHANGED from the pre-fix baseline:
    bar 164 still wins on the original weighted_score/paired-count edge."""
    outgoing = _t2_shaped_outgoing(bass_out=155.0)
    al = AE.align_pair(outgoing, _t2_shaped_incoming(), AE._DEFAULT_POLICY)
    assert al.handoff_bar_out == 164.0


def test_drop_payoff_still_dominates_bass_out_payoff():
    """drop_payoff stays the FIRST tier (round-2 finding 2's rank-order
    requirement): a candidate with drop_payoff=1 must still beat one with
    bass_out_payoff=1 but drop_payoff=0 - a REAL two-candidate competition
    via rank_all=True pooling (Codex code-review MINOR 1: the previous
    version of this test only inspected one candidate's tuple slots, which
    cannot actually prove one tier dominates another).

    Two incoming anchors (8 and 16, with first_drop_bar=12 sitting strictly
    between them so anchor 8 alone gets drop_payoff=1). Two outgoing cues:
    bar 36 = the outgoing's own bass_out_bar (reachable only from anchor 16
    - bar 36 sits outside anchor 8's own admissible overlap/progress range,
    confirmed directly before writing this fixture), bar 44 = a plain
    section boundary with no bass_out coincidence, reachable only from
    anchor 8. Pooling both anchors must pick anchor 8's bar-44 candidate
    (drop_payoff=1) over anchor 16's bar-36 candidate (bass_out_payoff=1,
    drop_payoff=0), proving the tier ordering for real rather than by
    inspecting one candidate's own tuple."""
    from dataclasses import replace

    outgoing = _track("Outgoing", 64, [
        {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 36.0},
        {"name": "break_1", "label": "break", "start_bar": 36.0, "end_bar": 44.0},
        {"name": "outro_1", "label": "outro", "start_bar": 44.0, "end_bar": 64.0},
    ], bass_out=36.0, bass_out_is_end=False)
    incoming = _track("Incoming", 300, [
        {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 8.0},
        {"name": "break_1", "label": "break", "start_bar": 8.0, "end_bar": 16.0},
        {"name": "drop_1", "label": "drop", "start_bar": 16.0, "end_bar": 300.0},
    ])
    policy = replace(AE._DEFAULT_POLICY, prefer_intro_swap_with_drop_payoff=True)

    outgoing_cues = AE._mix_cues(outgoing)
    incoming_cues = AE._mix_cues(incoming)
    window_start = outgoing.n_bars - outgoing.last_min_bars - AE.HANDOFF_WINDOW_BARS

    # Sanity: confirm each anchor's OWN best pick before pooling, so a
    # future change to the fixture's numbers that breaks the intended
    # separation fails loudly here rather than masking a real regression
    # below.
    anchor_8 = AE._search_anchors([8], outgoing, incoming, outgoing_cues,
                                   incoming_cues, window_start, 12.0, policy,
                                   rank_all=False)
    anchor_16 = AE._search_anchors([16], outgoing, incoming, outgoing_cues,
                                    incoming_cues, window_start, 12.0, policy,
                                    rank_all=False)
    assert anchor_8[3] == 44.0 and anchor_8[0][0] == 1 and anchor_8[0][1] == 0
    assert anchor_16[3] == 36.0 and anchor_16[0][0] == 0 and anchor_16[0][1] == 1

    pooled = AE._search_anchors(
        [8, 16], outgoing, incoming, outgoing_cues, incoming_cues,
        window_start, 12.0, policy, rank_all=True,
    )
    assert pooled[3] == 44.0
    assert pooled[0][0] == 1
