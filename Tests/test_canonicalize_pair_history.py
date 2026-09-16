"""Tests for burn list C7 Step 0: Source/canonicalize_pair_history.py.

Real corpus regression: `pair_history.jsonl` has 34 raw records that reduce
to 25 unique (project, pair_index) observations (matching the figure Codex's
plan review independently derived), of which 4 are genuine conflicts (Black
Book x Defected V2 pairs 3, 4, 5, 7) - each verified by hand against the real
file during the C6/C7/C8 plan review rounds, reproduced here as a pinned
regression so a future edit to the corpus or the canonicalisation logic
can't silently lose this coverage.
"""
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

from canonicalize_pair_history import (  # noqa: E402
    CanonicalPair, canonicalize, derive_delta_beats, load_records,
    load_resolutions, shadow_swap_preference,
)

REAL_PAIR_HISTORY = ROOT / "Documentation" / "Mix Patterns Library" / "pair_history.jsonl"


def _record(project="P", pair_index=1, claude=100.0, sam=100.0,
           verdict="correct", source="test", **extra):
    return {
        "project": project, "pair_index": pair_index,
        "claude_bass_swap_beat": claude, "sam_bass_swap_beat": sam,
        "verdict": verdict, "source": source, **extra,
    }


def test_derive_delta_never_trusts_the_raw_field():
    """The whole point of deriving rather than reading bass_swap_delta_beats
    directly: a record can carry a STALE or absent delta field while its own
    beat fields tell the truth. derive_delta_beats ignores the field
    entirely - confirmed by never even including it in the fixture."""
    r = _record(claude=2400.0, sam=2304.0)
    assert derive_delta_beats(r) == -96.0


def test_agreeing_duplicates_dedupe_to_one_canonical_row():
    records = [
        _record(project="P", pair_index=1, claude=100.0, sam=100.0, verdict="correct"),
        _record(project="P", pair_index=1, claude=100.0, sam=100.0, verdict="correct",
                source="auto_diff"),
    ]
    result = canonicalize(records)
    assert len(result.canonical) == 1
    assert len(result.conflicts) == 0
    assert result.canonical[0].delta_beats == 0.0
    assert result.canonical[0].n_records == 2


def test_agreeing_within_tolerance_still_dedupes():
    """A sub-bar disagreement (real corpus records carry both int and float
    beat values for the same transition) must not be flagged as a conflict -
    the 4-beat tolerance is exactly one bar, this project's own snap
    granularity."""
    records = [
        _record(project="P", pair_index=1, claude=100.0, sam=96.0),   # delta -4
        _record(project="P", pair_index=1, claude=100.0, sam=97.0),   # delta -3
    ]
    result = canonicalize(records, tolerance_beats=4.0)
    assert len(result.canonical) == 1
    assert len(result.conflicts) == 0


def test_delta_conflict_beyond_tolerance_fails_closed():
    records = [
        _record(project="P", pair_index=1, claude=2400.0, sam=2304.0),  # -96
        _record(project="P", pair_index=1, claude=2368.0, sam=2304.0),  # -64
    ]
    result = canonicalize(records)
    assert len(result.canonical) == 0
    assert len(result.conflicts) == 1
    assert result.conflicts[0].reason == "delta"


def test_verdict_conflict_with_identical_zero_delta_fails_closed():
    """The Black Book pair 5 shape exactly: both records agree the swap
    beat didn't move (delta 0 both sides), but disagree on verdict - a
    delta-only check would see agreement and miss this entirely."""
    records = [
        _record(project="P", pair_index=1, claude=100.0, sam=100.0, verdict="corrected"),
        _record(project="P", pair_index=1, claude=100.0, sam=100.0, verdict="correct"),
    ]
    result = canonicalize(records)
    assert len(result.canonical) == 0
    assert len(result.conflicts) == 1
    assert result.conflicts[0].reason == "verdict"


def test_verdict_and_delta_both_conflicting_is_reported_as_both():
    records = [
        _record(project="P", pair_index=1, claude=1856.0, sam=1792.0, verdict="corrected"),
        _record(project="P", pair_index=1, claude=1792.0, sam=1792.0, verdict="correct"),
    ]
    result = canonicalize(records)
    assert result.conflicts[0].reason == "verdict+delta"


def test_resolution_record_overrides_a_conflict():
    records = [
        _record(project="P", pair_index=1, claude=2400.0, sam=2304.0, verdict="corrected"),
        _record(project="P", pair_index=1, claude=2368.0, sam=2304.0, verdict="corrected"),
    ]
    resolutions = {("P", 1): {
        "resolved_verdict": "corrected", "resolved_delta_beats": -96.0,
        "resolved_by": "Sam", "date": "2026-09-15", "reason": "test resolution",
    }}
    result = canonicalize(records, resolutions)
    assert len(result.conflicts) == 0
    assert len(result.canonical) == 1
    assert result.canonical[0].source == "resolution"
    assert result.canonical[0].delta_beats == -96.0


def test_malformed_line_is_skipped_and_reported_not_silently_included(tmp_path):
    p = tmp_path / "ph.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": 1, "claude_bass_swap_beat": 100.0, '
        '"sam_bass_swap_beat": 100.0, "verdict": "correct"}\n'
        "{not valid json\n"
        '{"project": "P", "pair_index": 2, "verdict": "correct"}\n',  # missing beat fields
        encoding="utf-8",
    )
    records, malformed = load_records(p)
    assert len(records) == 1
    assert len(malformed) == 2


def test_absent_resolutions_file_is_legal_not_an_error(tmp_path):
    assert load_resolutions(tmp_path / "does_not_exist.jsonl") == {}
    assert load_resolutions(None) == {}


def test_resolution_record_missing_a_required_field_raises(tmp_path):
    p = tmp_path / "resolutions.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": 1, "resolved_verdict": "correct"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required field"):
        load_resolutions(p)


def test_duplicate_resolution_for_the_same_pair_is_rejected_not_last_wins(tmp_path):
    """Real bug found in review: two complete but CONTRADICTORY resolutions
    for the same (project, pair_index) used to silently resolve to whichever
    one came last in the file - the exact failure mode a human-authored,
    auditable resolution exists to prevent."""
    p = tmp_path / "resolutions.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": 1, "resolved_verdict": "corrected", '
        '"resolved_delta_beats": -96.0, "resolved_by": "Sam", "date": "2026-09-15", '
        '"reason": "first"}\n'
        '{"project": "P", "pair_index": 1, "resolved_verdict": "corrected", '
        '"resolved_delta_beats": -64.0, "resolved_by": "Sam", "date": "2026-09-15", '
        '"reason": "second, contradicts the first"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate resolution"):
        load_resolutions(p)


def test_resolution_with_non_finite_delta_is_rejected(tmp_path):
    p = tmp_path / "resolutions.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": 1, "resolved_verdict": "corrected", '
        '"resolved_delta_beats": NaN, "resolved_by": "Sam", "date": "2026-09-15", '
        '"reason": "test"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="finite"):
        load_resolutions(p)


def test_resolution_with_blank_audit_field_is_rejected(tmp_path):
    p = tmp_path / "resolutions.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": 1, "resolved_verdict": "corrected", '
        '"resolved_delta_beats": -64.0, "resolved_by": "  ", "date": "2026-09-15", '
        '"reason": "test"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-empty"):
        load_resolutions(p)


def test_non_finite_beat_field_is_reported_as_malformed_not_derived_into_nan(tmp_path):
    """A NaN/inf beat value must never reach derive_delta_beats() - nan != nan
    in Python, so a conflict check comparing it against a tolerance would
    silently misbehave rather than raising or flagging it."""
    p = tmp_path / "ph.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": 1, "claude_bass_swap_beat": NaN, '
        '"sam_bass_swap_beat": 100.0, "verdict": "correct"}\n',
        encoding="utf-8",
    )
    records, malformed = load_records(p)
    assert records == []
    assert len(malformed) == 1
    assert "non-finite" in malformed[0]["error"]


def test_canonicalize_rejects_a_non_finite_tolerance():
    with pytest.raises(ValueError, match="finite"):
        canonicalize([], tolerance_beats=float("nan"))
    with pytest.raises(ValueError, match="non-negative"):
        canonicalize([], tolerance_beats=-1.0)


def test_boolean_beat_value_is_rejected_not_silently_treated_as_a_number(tmp_path):
    """Real bug found in round-2 review: bool is a Python subclass of int
    (isinstance(True, int) is True), so a naive isinstance(value, (int,
    float)) check silently accepted a JSON `true`/`false` as a valid beat
    position - directly confirmed accepted before this fix."""
    p = tmp_path / "ph.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": 1, "claude_bass_swap_beat": true, '
        '"sam_bass_swap_beat": 100.0, "verdict": "correct"}\n',
        encoding="utf-8",
    )
    records, malformed = load_records(p)
    assert records == []
    assert len(malformed) == 1


def test_boolean_resolution_delta_is_rejected(tmp_path):
    p = tmp_path / "resolutions.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": 1, "resolved_verdict": "corrected", '
        '"resolved_delta_beats": true, "resolved_by": "Sam", "date": "2026-09-15", '
        '"reason": "test"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="finite"):
        load_resolutions(p)


def test_non_string_audit_fields_are_rejected_not_str_coerced(tmp_path):
    """Real bug found in round-2 review: the old str(record[f]).strip()
    pattern silently accepted resolved_verdict=true, resolved_by=7,
    date=[], reason={} as "non-empty" once coerced to text - all four
    directly confirmed accepted before this fix. Each case below must be
    individually rejected, not just the first one found."""
    base = {"project": "P", "pair_index": 1, "resolved_verdict": "corrected",
            "resolved_delta_beats": -64.0, "resolved_by": "Sam",
            "date": "2026-09-15", "reason": "test"}
    for field, bad_value in (
        ("resolved_verdict", True),
        ("resolved_by", 7),
        ("date", []),
        ("reason", {}),
    ):
        record = dict(base, **{field: bad_value})
        p = tmp_path / f"resolutions_{field}.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="non-empty string"):
            load_resolutions(p)


def test_boolean_pair_index_in_pair_history_is_rejected_as_malformed(tmp_path):
    """Same bool-subclass-of-int gap as the resolution-file check, found
    still open in load_records() by independent review (MiniMax, round 3):
    int(True) is 1, so a bool pair_index would have silently merged into
    whatever real pair 1 record exists via _key()'s int() coercion, rather
    than being rejected."""
    p = tmp_path / "ph.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": true, '
        '"claude_bass_swap_beat": 100.0, "sam_bass_swap_beat": 100.0, '
        '"verdict": "correct"}\n', encoding="utf-8",
    )
    records, malformed = load_records(p)
    assert records == []
    assert len(malformed) == 1
    assert "pair_index" in malformed[0]["error"]


def test_boolean_pair_index_is_rejected(tmp_path):
    p = tmp_path / "resolutions.jsonl"
    p.write_text(
        '{"project": "P", "pair_index": true, "resolved_verdict": "corrected", '
        '"resolved_delta_beats": -64.0, "resolved_by": "Sam", "date": "2026-09-15", '
        '"reason": "test"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pair_index"):
        load_resolutions(p)


def test_within_tolerance_duplicates_pick_a_deterministic_representative():
    """The canonical row for an agreeing-within-tolerance group must not
    depend on which duplicate happened to be listed first in the file -
    confirmed by checking BOTH orderings of the same two records produce
    the IDENTICAL canonical row."""
    a = _record(project="P", pair_index=1, claude=100.0, sam=98.0, verdict="correct",
               bpm_out=128.0)
    b = _record(project="P", pair_index=1, claude=100.0, sam=97.0, verdict="correct",
               bpm_out=129.0)

    result_ab = canonicalize([a, b])
    result_ba = canonicalize([b, a])

    assert result_ab.canonical[0].bpm_out == result_ba.canonical[0].bpm_out
    assert result_ab.canonical[0].delta_beats == result_ba.canonical[0].delta_beats


# --------------------------------------------------------------------------- #
# Real corpus regression                                                      #
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not REAL_PAIR_HISTORY.exists(),
                    reason="real pair_history.jsonl unavailable")
def test_real_corpus_reduces_to_35_unique_observations_with_4_conflicts():
    """Pinned against the actual project data. If this ever changes, it
    means the real pair_history.jsonl corpus itself changed (new sessions
    append to it) - re-verify by hand before updating this pin, the same
    discipline test_alignment_baseline.py uses.

    2026-09-15 (C6/C7/C8 plan rounds): 34 records, 25 unique, 4 conflicting
    (Black Book x Defected V2 pairs 3/4/5/7), all derived by hand.
    2026-09-15 later: the 15.09.26 August Releases Mix added 11 records, one
    per transition, no duplicates, so 45 records and 35 unique; the 4
    conflicts are unchanged. Its T4 has NO swap in Sam's version (he
    crossfaded instead - `swap_removed` in its corrections), so
    `sam_bass_swap_beat` is null and load_records reports it as malformed:
    the canonicaliser has no observation class for a removed swap yet. That
    record is real, not broken - see burn list D12/C7."""
    records, malformed = load_records(REAL_PAIR_HISTORY)
    assert [(m["record"]["project"], m["record"]["pair_index"], m["error"])
            for m in malformed] == [
        ("15.09.26 August Releases Mix", 4, "missing field(s): ['sam_bass_swap_beat']"),
    ]
    assert len(records) == 44

    result = canonicalize(records)
    assert len(result.canonical) + len(result.conflicts) == 35

    conflict_keys = {(c.project, c.pair_index) for c in result.conflicts}
    assert conflict_keys == {
        ("Black Book x Defected V2", 3),
        ("Black Book x Defected V2", 4),
        ("Black Book x Defected V2", 5),
        ("Black Book x Defected V2", 7),
    }


# --------------------------------------------------------------------------- #
# shadow_swap_preference (burn list C7 Step 1, report-only)                   #
# --------------------------------------------------------------------------- #

def _canonical(project="A", pair_index=1, delta=10.0, bpm=128.0,
              out_structure=("intro_1", "drop_1"), in_structure=("drop_1",),
              verdict="corrected"):
    return CanonicalPair(
        project=project, pair_index=pair_index, delta_beats=delta,
        verdict=verdict, bpm_out=bpm, bpm_in=bpm,
        out_structure=tuple(out_structure), in_structure=tuple(in_structure),
        source="corpus", n_records=1,
    )


def test_shadow_preference_is_none_with_no_canonical_pairs():
    assert shadow_swap_preference(128.0, ["drop_1"], ["drop_1"], []) is None


def test_shadow_preference_weights_by_similarity_hand_verified():
    # A: exact BPM + exact structure match -> similarity 1.0
    a = _canonical(project="ProjA", delta=10.0, bpm=128.0)
    # B: BPM off by exactly BPM_MATCH_TOLERANCE (2.0) -> bpm_score 0.0,
    # same structure -> struct_score 1.0 -> similarity 0.3*0 + 0.7*1 = 0.7
    b = _canonical(project="ProjB", delta=20.0, bpm=130.0)
    result = shadow_swap_preference(
        128.0, ["intro_1", "drop_1"], ["drop_1"], [a, b])
    assert result is not None
    # weighted avg: (1.0*10 + 0.7*20) / (1.0 + 0.7) = 24/1.7
    assert result["suggested_delta_beats"] == pytest.approx(14.1, abs=0.05)
    assert result["confidence"] == pytest.approx(1.0, abs=1e-6)
    assert [b["project"] for b in result["based_on"]] == ["ProjA", "ProjB"]


def test_shadow_preference_excludes_the_named_project():
    a = _canonical(project="ProjA", delta=10.0, bpm=128.0)
    b = _canonical(project="ProjB", delta=20.0, bpm=130.0)
    result = shadow_swap_preference(
        128.0, ["intro_1", "drop_1"], ["drop_1"], [a, b],
        exclude_project="ProjA")
    assert result is not None
    assert [pr["project"] for pr in result["based_on"]] == ["ProjB"]
    assert result["suggested_delta_beats"] == 20.0


def test_shadow_preference_excludes_itself_out_of_existence_returns_none():
    a = _canonical(project="OnlyProj", delta=10.0, bpm=128.0)
    result = shadow_swap_preference(
        128.0, ["intro_1", "drop_1"], ["drop_1"], [a],
        exclude_project="OnlyProj")
    assert result is None


def test_shadow_preference_below_similarity_threshold_returns_none():
    # A real (if modest) similarity score, but below an explicit stricter
    # min_similarity - no fabricated low-confidence guess past the caller's
    # own bar. (BPM 80 off -> score 0; structure -> similarity 0.56, hand-
    # verified: out_dist 4 + in_dist 2 = 6, struct_score 1-6/30=0.8,
    # total=0.7*0.8=0.56.)
    a = _canonical(project="ProjA", delta=10.0, bpm=90.0,
                   out_structure=("break_1",), in_structure=("break_2",))
    result = shadow_swap_preference(
        170.0, ["intro_1", "drop_1", "drop_2"], ["drop_1", "outro_1"], [a],
        min_similarity=0.6)
    assert result is None


def test_shadow_preference_caps_at_max_results():
    pairs = [_canonical(project=f"Proj{i}", delta=float(i), bpm=128.0)
             for i in range(5)]
    result = shadow_swap_preference(
        128.0, ["intro_1", "drop_1"], ["drop_1"], pairs, max_results=2)
    assert len(result["based_on"]) == 2
