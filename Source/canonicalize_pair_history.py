"""Canonicalise `pair_history.jsonl` (burn list C7, Step 0).

`pair_history.jsonl` holds real Sam corrections across multiple projects, but
it is not 34 clean, independent observations: the same (project, pair_index)
transition is often logged twice under different sources (an older, richer
`v21_v22_initial` capture and a coarser automated `auto_diff` re-diff), and
these duplicates can genuinely CONFLICT - not just on formatting, on the
actual correction. Nothing downstream should trust this file directly; this
module is the one place that reads it and decides what's safe to score from.

The core lesson (Codex review, 2026-09-15, burn list C6/C7/C8 plan rounds
2-4): comparing the raw `bass_swap_delta_beats` field is not enough. That
field is frequently absent, and can be `None` on BOTH sides of a real
conflict at once - two nulls read as "agreement" by a naive comparison,
which is exactly backwards. Real example, Black Book x Defected V2 pair 4:
one record has claude=2400/sam=2304 (derived delta -96), the other has
claude=2368/sam=2304 (derived delta -64) - genuinely different corrections,
same "corrected" verdict, `bass_swap_delta_beats` absent on both. So this
module ALWAYS derives the delta itself from the two beat fields and never
trusts a pre-existing delta field on its own.

A second real class (round-4 finding 1): some conflicts are about VERDICT
semantics, not position. Black Book pair 5 has IDENTICAL zero derived delta
in both duplicate records but disagrees `corrected` (a two_stage_bass
automation change - the swap position didn't move, but Sam still changed
something real) vs `correct` (the coarser `auto_diff` source can't see a
correction that doesn't move the swap beat at all). Pair 7 disagrees
`correct_with_arrangement` vs `correct` the same way, over an arrangement
extension. A delta-only conflict check would miss both.

FAIL CLOSED: a conflicting (project, pair_index) group is EXCLUDED from the
canonical dataset entirely - never auto-resolved by "keep the latest" or
"keep the most complete," there is no reliable signal for either (the real
Black Book duplicates share the same 2026-05-21 timestamp, so recency can't
even tiebreak) - until a human-authored resolution record exists for it in
the sidecar resolutions file.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

DELTA_TOLERANCE_BEATS = 4.0  # 1 bar - align_engine.SNAP_BARS' own beat
                             # equivalent, not a new arbitrary number.

RESOLUTION_REQUIRED_FIELDS = (
    "resolved_verdict", "resolved_delta_beats", "resolved_by", "date", "reason",
)

# Same value as propose_arrangement.BPM_MATCH_TOLERANCE. Duplicated rather
# than imported: propose_arrangement.py imports FROM this module (burn list
# C7 Step 1), so importing back would cycle. Keep both in sync by hand if
# either changes.
BPM_MATCH_TOLERANCE = 2.0


@dataclass(frozen=True)
class CanonicalPair:
    """One trustworthy (project, pair_index) observation, ready for scoring."""
    project: str
    pair_index: int
    delta_beats: float
    verdict: str
    bpm_out: float | None
    bpm_in: float | None
    out_structure: tuple[str, ...]
    in_structure: tuple[str, ...]
    source: str                    # "corpus" (agreed dedup) or "resolution"
    n_records: int                 # how many raw records this row represents


@dataclass(frozen=True)
class Conflict:
    """A (project, pair_index) group whose raw records disagree and has no
    resolution record yet - excluded from the canonical dataset."""
    project: str
    pair_index: int
    reason: str                    # "verdict" | "delta" | "verdict+delta"
    records: tuple[dict, ...]      # the raw, disagreeing records


@dataclass(frozen=True)
class CanonicalizationResult:
    canonical: tuple[CanonicalPair, ...]
    conflicts: tuple[Conflict, ...]
    malformed: tuple[dict, ...] = field(default_factory=tuple)


def _key(record: dict) -> tuple[str, int]:
    return (record["project"], int(record["pair_index"]))


def _is_finite_number(value: object) -> bool:
    """int/float only - `bool` is a Python subclass of `int`
    (`isinstance(True, int)` is True), so a naive `isinstance(value, (int,
    float))` silently accepts a JSON `true`/`false` as a valid beat/delta
    (found in review, 2026-09-15: directly confirmed accepted before this
    fix). Excluded explicitly, not left to an isinstance ordering trick."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _is_real_string(value: object) -> bool:
    """A real, non-empty (once stripped) `str` - not `str(value)` on
    whatever JSON type happened to be there. The old `str(record[f]).strip()`
    pattern silently accepted a JSON `true`, a bare number, an empty list,
    or an empty dict as "non-empty" once coerced to text (found in review,
    2026-09-15, all four directly confirmed accepted before this fix)."""
    return isinstance(value, str) and bool(value.strip())


def derive_delta_beats(record: dict) -> float:
    """The one true delta: sam - claude, from the beat fields directly.

    Never reads `bass_swap_delta_beats` - see module docstring for why that
    field cannot be trusted, even when present on both sides of a conflict.
    """
    return float(record["sam_bass_swap_beat"]) - float(record["claude_bass_swap_beat"])


def load_records(path: Path) -> tuple[list[dict], list[dict]]:
    """Parse `pair_history.jsonl`. Returns (records, malformed) - a line
    missing `project`/`pair_index`/either beat field is malformed, reported
    separately, never silently dropped or silently included."""
    records: list[dict] = []
    malformed: list[dict] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            malformed.append({"line": lineno, "error": f"invalid JSON: {exc}"})
            continue
        required = ("project", "pair_index", "claude_bass_swap_beat",
                    "sam_bass_swap_beat", "verdict")
        missing = [f for f in required if record.get(f) is None]
        if missing:
            malformed.append({"line": lineno, "error": f"missing field(s): {missing}",
                              "record": record})
            continue
        # Same bool-subclass-of-int gap the resolution-file pair_index check
        # already closed, found still open HERE by independent review
        # (MiniMax, 2026-09-15): `int(True)` is 1, so a bool pair_index
        # would silently merge into whatever real pair 1 record exists via
        # `_key()`'s `int(record["pair_index"])` coercion, rather than being
        # rejected as malformed.
        pair_index = record["pair_index"]
        if not isinstance(pair_index, int) or isinstance(pair_index, bool):
            malformed.append({"line": lineno,
                              "error": "pair_index must be an integer (not a bool)",
                              "record": record})
            continue
        # A syntactically-present but non-finite beat value (nan/inf, or a
        # non-numeric string that slipped past a hand edit) must not reach
        # derive_delta_beats() silently - it would produce a nan/inf delta
        # that no conflict check catches (nan != nan is True in Python, and
        # a spread computed against it is nan too, comparing False against
        # any tolerance) and could poison a canonical row (found in review,
        # 2026-09-15).
        bad_beats = [
            f for f in ("claude_bass_swap_beat", "sam_bass_swap_beat")
            if not _is_finite_number(record[f])
        ]
        if bad_beats:
            malformed.append({"line": lineno,
                              "error": f"non-finite beat field(s): {bad_beats}",
                              "record": record})
            continue
        if not _is_real_string(record.get("project")):
            malformed.append({"line": lineno, "error": "empty or non-string project",
                              "record": record})
            continue
        records.append(record)
    return records, malformed


def load_resolutions(path: Path | None) -> dict[tuple[str, int], dict]:
    """Sidecar resolution file, same JSONL convention as pair_history itself.
    Absent file = no resolutions yet (legal, not an error - most projects
    will have zero conflicts needing one).

    Fails closed on anything ambiguous, same discipline as `canonicalize`
    itself - a resolution file is trusted to override a real conflict, so it
    gets LESS tolerance for malformed input than the corpus it resolves, not
    more:
      - a duplicate (project, pair_index) key is REJECTED outright, even if
        the two records happen to agree - two "complete" resolutions for the
        same pair is itself a process failure worth surfacing, and silently
        keeping "whichever line came last" (found in review, 2026-09-15: two
        contradictory resolutions for one pair silently produced whichever
        delta happened to be on the last line) defeats the entire point of a
        human-authored, auditable resolution.
      - `resolved_delta_beats` must be a finite number.
      - every audit field must be a non-empty string once stripped.
    """
    if path is None or not path.exists():
        return {}
    out: dict[tuple[str, int], dict] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        record = json.loads(raw)
        all_required = RESOLUTION_REQUIRED_FIELDS + ("project", "pair_index")
        missing = [f for f in all_required if record.get(f) is None]
        if missing:
            raise ValueError(
                f"{path}:{lineno}: resolution record missing required "
                f"field(s) {sorted(set(missing))} - every resolution must be complete, "
                f"a partial one is worse than none (it would silently fail "
                f"to resolve the conflict it claims to)"
            )
        delta = record["resolved_delta_beats"]
        if not _is_finite_number(delta):
            raise ValueError(
                f"{path}:{lineno}: resolved_delta_beats must be a finite "
                f"number (not a bool), got {delta!r}"
            )
        for f in ("resolved_verdict", "resolved_by", "date", "reason",
                  "project"):
            if not _is_real_string(record[f]):
                raise ValueError(
                    f"{path}:{lineno}: {f!r} must be a non-empty string, "
                    f"got {record[f]!r}"
                )
        pair_index = record["pair_index"]
        if not isinstance(pair_index, int) or isinstance(pair_index, bool):
            raise ValueError(
                f"{path}:{lineno}: pair_index must be an integer (not a "
                f"bool), got {pair_index!r}"
            )
        key = (record["project"], pair_index)
        if key in out:
            raise ValueError(
                f"{path}:{lineno}: duplicate resolution for {key} - a "
                f"second, later record for the same pair would silently "
                f"win over the first regardless of whether they agree; fix "
                f"the file so each (project, pair_index) has exactly one "
                f"resolution record"
            )
        out[key] = record
    return out


def _deterministic_representative(group: list[dict]) -> dict:
    """Pick ONE record from an agreeing (or metadata-only) group in a way
    that never depends on JSONL line order (found in review, 2026-09-15:
    the old `group[0]` made the canonical bpm/structure metadata for a
    within-tolerance-but-not-identical group depend on which duplicate
    happened to be logged first). Sorts by the record's own canonical JSON
    form - stable, reproducible, and needs no new "average" concept for
    fields (structure lists, bpm) that don't have an obvious average."""
    return min(group, key=lambda r: json.dumps(r, sort_keys=True, default=str))


def canonicalize(records: list[dict],
                 resolutions: dict[tuple[str, int], dict] | None = None,
                 tolerance_beats: float = DELTA_TOLERANCE_BEATS,
                 ) -> CanonicalizationResult:
    """Group by (project, pair_index), detect conflicts on the DERIVED delta
    and the verdict, fail closed on anything unresolved."""
    if not math.isfinite(tolerance_beats) or tolerance_beats < 0:
        raise ValueError(
            f"tolerance_beats must be finite and non-negative, got "
            f"{tolerance_beats!r}"
        )
    resolutions = resolutions or {}
    groups: dict[tuple[str, int], list[dict]] = {}
    for record in records:
        groups.setdefault(_key(record), []).append(record)

    canonical: list[CanonicalPair] = []
    conflicts: list[Conflict] = []

    for key, group in groups.items():
        project, pair_index = key
        deltas = [derive_delta_beats(r) for r in group]
        verdicts = {r["verdict"] for r in group}
        delta_spread = max(deltas) - min(deltas)
        verdict_conflict = len(verdicts) > 1
        delta_conflict = delta_spread > tolerance_beats

        if not verdict_conflict and not delta_conflict:
            rep = _deterministic_representative(group)
            canonical.append(CanonicalPair(
                project=project, pair_index=pair_index,
                delta_beats=derive_delta_beats(rep), verdict=rep["verdict"],
                bpm_out=rep.get("bpm_out"), bpm_in=rep.get("bpm_in"),
                out_structure=tuple(rep.get("out_structure") or ()),
                in_structure=tuple(rep.get("in_structure") or ()),
                source="corpus", n_records=len(group),
            ))
            continue

        resolution = resolutions.get(key)
        if resolution is not None:
            rep = _deterministic_representative(group)
            canonical.append(CanonicalPair(
                project=project, pair_index=pair_index,
                delta_beats=float(resolution["resolved_delta_beats"]),
                verdict=resolution["resolved_verdict"],
                bpm_out=rep.get("bpm_out"), bpm_in=rep.get("bpm_in"),
                out_structure=tuple(rep.get("out_structure") or ()),
                in_structure=tuple(rep.get("in_structure") or ()),
                source="resolution", n_records=len(group),
            ))
            continue

        reason = ("verdict+delta" if verdict_conflict and delta_conflict
                  else "verdict" if verdict_conflict else "delta")
        conflicts.append(Conflict(
            project=project, pair_index=pair_index, reason=reason,
            records=tuple(group),
        ))

    return CanonicalizationResult(
        canonical=tuple(canonical), conflicts=tuple(conflicts),
    )


def _structure_signature(sections: list[str]) -> tuple[int, int, int, int]:
    """Compact fingerprint of a section structure: (drops, breaks, fills, intros).

    Deliberately identical to propose_arrangement._structure_signature -
    duplicated, not imported, for the same reason BPM_MATCH_TOLERANCE is
    duplicated above (this module cannot import from propose_arrangement.py
    without cycling). Keep both in sync by hand if either changes."""
    drops = sum(1 for s in sections if s.lower().startswith("drop"))
    breaks = sum(1 for s in sections if s.lower().startswith(("break", "braak")))
    fills = sum(
        1 for s in sections
        if s.lower().startswith(("fill", "beat_dropout"))
    )
    intros = sum(1 for s in sections if s.lower().startswith("intro"))
    return (drops, breaks, fills, intros)


def shadow_swap_preference(
    bpm: float,
    out_structure: list[str],
    in_structure: list[str],
    canonical_pairs: list[CanonicalPair],
    *,
    exclude_project: str | None = None,
    max_results: int = 3,
    min_similarity: float = 0.1,
) -> dict | None:
    """Burn list C7 Step 1 - REPORT ONLY. Never called by anything that
    chooses a swap point, an overlap, or any other real arrangement
    decision - see propose_arrangement.py's own call site, which only ever
    assigns the result to a report field.

    A similarity-weighted average of the DELTA (sam - claude, in beats) the
    canonical corpus's real corrections applied to transitions shaped like
    this one. This is a hypothesis to be MEASURED, not a trusted signal -
    `evaluate_shadow_swap_preference.py` runs this function in a
    leave-one-project-out loop against the real corpus and reports how often
    it would actually have been right, honestly, including against a
    trivial "predict zero" baseline. Promotion past shadow mode (an actual
    nudge to a real decision) needs that evaluation to show real skill
    first, per the C6/C7/C8 plan's own Codex-reviewed sequencing - this
    function existing is not itself that promotion.

    Same similarity weights as propose_arrangement.find_similar_pairs (BPM
    0.3, section-shape 0.7) - kept identical so "similar" doesn't quietly
    mean something different between the report's existing `similar_pairs`
    field and this one.

    `exclude_project`: pairs belonging to this project are never scored -
    the leave-one-project-out contract (a project's own transitions are
    correlated, not independent samples, so a project must never be allowed
    to predict itself; Codex review, swap-first-redesign plan).
    """
    out_sig = _structure_signature(out_structure)
    in_sig = _structure_signature(in_structure)

    scored: list[tuple[float, CanonicalPair]] = []
    for pair in canonical_pairs:
        if exclude_project is not None and pair.project == exclude_project:
            continue
        if pair.bpm_out is None:
            bpm_score = 0.0
        else:
            bpm_diff = abs(bpm - pair.bpm_out)
            bpm_score = (0.0 if bpm_diff > BPM_MATCH_TOLERANCE
                         else 1.0 - (bpm_diff / BPM_MATCH_TOLERANCE))
        pair_out_sig = _structure_signature(pair.out_structure)
        pair_in_sig = _structure_signature(pair.in_structure)
        out_dist = sum(abs(a - b) for a, b in zip(out_sig, pair_out_sig))
        in_dist = sum(abs(a - b) for a, b in zip(in_sig, pair_in_sig))
        struct_score = max(0.0, 1.0 - (out_dist + in_dist) / 30.0)
        total = 0.3 * bpm_score + 0.7 * struct_score
        if total > min_similarity:
            scored.append((total, pair))

    if not scored:
        return None

    scored.sort(key=lambda item: -item[0])
    top = scored[:max_results]
    weight_sum = sum(sim for sim, _ in top)
    if weight_sum <= 0:
        return None

    suggested_delta = sum(sim * p.delta_beats for sim, p in top) / weight_sum
    return {
        "suggested_delta_beats": round(suggested_delta, 1),
        "confidence": round(top[0][0], 3),  # best single match's similarity
        "based_on": [
            {"project": p.project, "pair_index": p.pair_index,
             "delta_beats": p.delta_beats, "verdict": p.verdict,
             "similarity": round(sim, 3)}
            for sim, p in top
        ],
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pair_history", type=Path,
                    help="Path to pair_history.jsonl")
    ap.add_argument("--resolutions", type=Path, default=None,
                    help="Path to a resolutions sidecar JSONL (default: "
                         "<pair_history dir>/pair_history_resolutions.jsonl "
                         "if it exists)")
    ap.add_argument("--out", type=Path, default=None,
                    help="Write the canonical dataset as JSON to this path")
    args = ap.parse_args()

    resolutions_path = args.resolutions
    if resolutions_path is None:
        candidate = args.pair_history.with_name("pair_history_resolutions.jsonl")
        resolutions_path = candidate if candidate.exists() else None

    records, malformed = load_records(args.pair_history)
    resolutions = load_resolutions(resolutions_path)
    result = canonicalize(records, resolutions)

    print(f"{len(records)} raw records, {len(malformed)} malformed "
          f"(skipped, not silently included)")
    for m in malformed:
        print(f"  MALFORMED line {m['line']}: {m['error']}")
    print(f"{len(result.canonical)} canonical (project, pair_index) rows "
          f"({sum(1 for c in result.canonical if c.source == 'resolution')} "
          f"via resolution record)")
    print(f"{len(result.conflicts)} unresolved conflict(s) - EXCLUDED, "
          f"need a human-authored resolution record:")
    for c in result.conflicts:
        print(f"  {c.project} pair {c.pair_index} ({c.reason}):")
        for r in c.records:
            delta = derive_delta_beats(r)
            print(f"    source={r.get('source')!r:20} verdict={r['verdict']!r:12} "
                  f"derived_delta={delta:+.0f}b claude={r['claude_bass_swap_beat']} "
                  f"sam={r['sam_bass_swap_beat']}")

    if args.out:
        payload = {
            "canonical": [vars(c) for c in result.canonical],
            "conflicts": [
                {"project": c.project, "pair_index": c.pair_index,
                 "reason": c.reason, "records": list(c.records)}
                for c in result.conflicts
            ],
            "malformed": malformed,
        }
        # allow_nan=False: a non-finite value reaching this point would be a
        # real bug in the validation above, not something to silently emit
        # as non-standard JSON (`NaN`) for a downstream reader to choke on.
        args.out.write_text(json.dumps(payload, indent=1, allow_nan=False),
                            encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
