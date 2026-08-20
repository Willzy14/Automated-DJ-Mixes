"""Cue candidate records + MIK/amplitude/visual-hint candidate synthesis.

The Rekordbox interval-based detector was removed 2026-08-18. CueCandidate
remains the canonical cue record the rest of the pipeline consumes (the
MIK-synthesis and visual-hint paths produce it), but the per-interval RB
producer (find_cue_candidates + first_drop_candidate) is gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# ANALYSIS_MODEL_VERSION kept here verbatim for any consumer that imports it
# (the MIK/visual-hint candidates still record it for cache-key / report purposes).
ANALYSIS_MODEL_VERSION = "cue-candidates-v1"


@dataclass
class CueCandidate:
    """One detected cue point. Confidence + explainability built-in."""
    beat: float                  # warp-beat coordinate (= source beat)
    sec: float                   # source-seconds (from TrackFeatures.beats)
    cue_type: str                # bass_entry | break_start | break_end | chop_point | outro_start
    confidence: float            # 0-1 (after region penalty applied)
    sources: list[str]           # ["mik_cue", "librosa_bass_rise+42%", "visual_hint"]
    reasons: list[str]           # human-readable strings
    interval_index: int
    region: str                  # pre_first_rb_chorus | active | post_last_rb_chorus
    penalty: float = 0.0
    analysis_model_version: str = ANALYSIS_MODEL_VERSION


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

def candidates_for(
    candidates: Iterable[CueCandidate],
    cue_type: str,
    min_confidence: float = 0.5,
) -> list[CueCandidate]:
    """Return all candidates of a type, ranked by confidence (descending)."""
    return sorted(
        [c for c in candidates if c.cue_type == cue_type and c.confidence >= min_confidence],
        key=lambda c: -c.confidence,
    )


def _is_visual_hint(cand: CueCandidate) -> bool:
    """Visual hints get priority over algorithmic picks (Sam's rule, 2026-05)."""
    return any("visual_hint" in s for s in cand.sources)


def first_credible(
    candidates: Iterable[CueCandidate],
    cue_type: str,
    min_confidence: float = 0.5,
) -> CueCandidate | None:
    """Top candidate of a type. Visual hints win over anything else; otherwise
    highest confidence. Returns None if nothing credible.
    """
    ranked = candidates_for(candidates, cue_type, min_confidence)
    if not ranked:
        return None
    hinted = [c for c in ranked if _is_visual_hint(c)]
    if hinted:
        return hinted[0]
    return ranked[0]


# ---------------------------------------------------------------------------
# MIK-only synthesis (the standard non-hint cue source)
# ---------------------------------------------------------------------------

# Base confidence for a MIK cue position alone (no energy validation).
MIK_ONLY_BASE_CONFIDENCE = 0.65

# Bonus when MIK energy ACTUALLY changes around the cue (Sam's energy rule:
# a real cue point must show different energy before vs after). Lifts the
# candidate above the RB-derived equivalents when corroborated.
MIK_ENERGY_VALIDATED_BONUS = 0.20

# Minimum MIK energy delta to count as "validated" (1-10 scale).
#   - bass_entry: after > before by this much (energy rises into the drop)
#   - outro_start: before > after by this much (energy drops into the outro)
MIK_ENERGY_DELTA_MIN = 2

# Cues this many beats from the track start are treated as "intro" and skipped
# when picking the bass_entry (drop) cue.
MIK_INTRO_SKIP_BEATS = 16 * 4

# Cues this many beats from the track end are treated as "outro tail" and
# skipped when picking the outro_start cue.
MIK_OUTRO_TAIL_BEATS = 8 * 4

# Seconds of audio before/after a cue used as the energy comparison window.
# 30s typically spans into the next MIK energy segment so the before/after
# readings differ. Shorter windows often land inside the same segment.
MIK_ENERGY_WINDOW_SEC = 30.0

# Chop_point search rule for MIK-only tracks: the chop should be where the
# outgoing audio stops being useful as a groove (end of the last segment
# with energy >= this threshold). Anything quieter is decay tail.
MIK_CHOP_ENERGY_FLOOR = 4

# If no MIK energy segments are usable, fall back to this many beats past
# the outro_start as the chop point (= "give the outro one phrase to play
# before we chop into the loop").
MIK_CHOP_FALLBACK_BEATS_PAST_OUTRO = 16 * 4


def _mik_energy_at(mik_energy_segments, time_sec: float) -> int | None:
    """Return MIK energy level (1-10) at a given time, or None if no segment."""
    if not mik_energy_segments:
        return None
    for s in mik_energy_segments:
        if s.start_sec <= time_sec < s.end_sec:
            return int(s.energy)
    return None


def _mik_energy_around(
    mik_energy_segments, cue_time_sec: float, window_sec: float = MIK_ENERGY_WINDOW_SEC,
) -> tuple[int | None, int | None]:
    """Energy reading window_sec before and after the cue.

    Returns (energy_before, energy_after). Either can be None if no segment
    covers that timestamp.
    """
    before = _mik_energy_at(mik_energy_segments, cue_time_sec - window_sec)
    after = _mik_energy_at(mik_energy_segments, cue_time_sec + window_sec)
    return before, after


def mik_to_candidates(
    cue_times_sec: list[float],
    first_downbeat_sec: float,
    bpm: float,
    total_beats: float,
    mik_energy_segments: list | None = None,
) -> list[CueCandidate]:
    """Synthesise CueCandidates from MIK auto-cue times.

    The standard non-hint cue source. Picks two anchors:
      - bass_entry  = FIRST MIK cue past the intro skip (the first drop -
                      what a DJ cares about)
      - outro_start = LAST MIK cue before the outro tail (the final
                      chorus -> outro transition)

    Energy validation is applied as a CONFIDENCE BOOST, not for cue
    selection. Sam's rule (2026-05): "checking the energy in front and
    behind the key point - that's essentially what we're looking for: an
    energy change." We honour that by reporting whether the picked cue
    DOES show an energy delta - if yes, confidence rises and the source
    label shows it; if no, the cue is still used but confidence is lower.

    Why position trumps magnitude: a later cue with a bigger energy rise
    is usually the climax/second drop. Using it as bass_entry would mean
    the listener hears 2/3 of the incoming track before the swap, which
    is wrong. The first drop is where the transition needs to land even
    if MIK measures a softer energy change there.
    """
    if not cue_times_sec or bpm <= 0:
        return []

    sec_per_beat = 60.0 / bpm
    cue_beats: list[tuple[float, float]] = []  # (beat, sec)
    for t in sorted(cue_times_sec):
        beat = (t - first_downbeat_sec) / sec_per_beat
        if 0 <= beat <= total_beats:
            cue_beats.append((beat, t))

    if not cue_beats:
        return []

    candidates: list[CueCandidate] = []

    # --- BASS_ENTRY: FIRST cue past intro skip (the first drop) -----------
    drop_pool = [(b, s) for b, s in cue_beats if b >= MIK_INTRO_SKIP_BEATS]
    if not drop_pool:
        drop_pool = cue_beats[:1]
    best_drop = drop_pool[0]

    # Energy validation as a confidence signal (not a selection criterion).
    best_drop_delta = None
    if mik_energy_segments:
        e_before, e_after = _mik_energy_around(mik_energy_segments, best_drop[1])
        if e_before is not None and e_after is not None:
            delta = e_after - e_before
            if delta >= MIK_ENERGY_DELTA_MIN:
                best_drop_delta = ("rise", delta, e_before, e_after)
            else:
                best_drop_delta = ("flat", delta, e_before, e_after)

    sources = [f"mik_cue@{best_drop[1]:.1f}s"]
    reasons = [
        f"Mixed In Key auto-cue at {best_drop[1]:.1f}s "
        f"({best_drop[0]:.0f} beats from first downbeat) - "
        f"first cue past the {MIK_INTRO_SKIP_BEATS}-beat intro region"
    ]
    confidence = MIK_ONLY_BASE_CONFIDENCE
    if best_drop_delta is None:
        reasons.append("No MIK energy data around this cue - confidence unboosted")
    elif best_drop_delta[0] == "rise":
        _, delta, e_before, e_after = best_drop_delta
        sources.append(f"mik_energy_rise+{delta}")
        reasons.append(
            f"MIK energy rises E{e_before} -> E{e_after} (D+{delta}) - "
            f"confirms a real drop"
        )
        confidence += MIK_ENERGY_VALIDATED_BONUS
    else:
        _, delta, e_before, e_after = best_drop_delta
        sources.append(f"mik_energy_flat({delta:+d})")
        reasons.append(
            f"MIK energy stays flat (E{e_before} -> E{e_after}, D{delta:+d}) - "
            f"either MIK's first cue is a soft drop or the segments are coarse"
        )

    candidates.append(CueCandidate(
        beat=best_drop[0],
        sec=best_drop[1],
        cue_type="bass_entry",
        confidence=min(1.0, confidence),
        sources=sources,
        reasons=reasons,
        interval_index=-1,
        region="active",
        penalty=0.0,
    ))

    # --- OUTRO_START: LAST cue before outro tail (the final outro) ---------
    outro_cutoff = max(0.0, total_beats - MIK_OUTRO_TAIL_BEATS)
    outro_pool = [
        (b, s) for b, s in cue_beats
        if b <= outro_cutoff and b > best_drop[0] + MIK_INTRO_SKIP_BEATS
    ]
    best_outro = outro_pool[-1] if outro_pool else None

    best_outro_delta = None
    if best_outro is not None and mik_energy_segments:
        e_before, e_after = _mik_energy_around(mik_energy_segments, best_outro[1])
        if e_before is not None and e_after is not None:
            delta = e_before - e_after  # positive = energy DROPPED
            if delta >= MIK_ENERGY_DELTA_MIN:
                best_outro_delta = ("drop", delta, e_before, e_after)
            else:
                best_outro_delta = ("flat", delta, e_before, e_after)

    if best_outro is not None:
        sources = [f"mik_cue@{best_outro[1]:.1f}s"]
        reasons = [
            f"Mixed In Key auto-cue at {best_outro[1]:.1f}s "
            f"({best_outro[0]:.0f} beats) - last cue before the "
            f"{MIK_OUTRO_TAIL_BEATS}-beat outro tail"
        ]
        confidence = MIK_ONLY_BASE_CONFIDENCE
        if best_outro_delta is None:
            reasons.append("No MIK energy data around this cue - confidence unboosted")
        elif best_outro_delta[0] == "drop":
            _, delta, e_before, e_after = best_outro_delta
            sources.append(f"mik_energy_drop-{delta}")
            reasons.append(
                f"MIK energy drops E{e_before} -> E{e_after} (D-{delta}) - "
                f"confirms entry into the outro"
            )
            confidence += MIK_ENERGY_VALIDATED_BONUS
        else:
            _, delta, e_before, e_after = best_outro_delta
            sources.append(f"mik_energy_flat({delta:+d})")
            reasons.append(
                f"MIK energy stays flat around this cue (E{e_before} -> E{e_after}, "
                f"D{delta:+d}) - outro may be a soft fade rather than a hard drop"
            )
        candidates.append(CueCandidate(
            beat=best_outro[0],
            sec=best_outro[1],
            cue_type="outro_start",
            confidence=min(1.0, confidence),
            sources=sources,
            reasons=reasons,
            interval_index=-1,
            region="post_last_rb_chorus",
            penalty=0.0,
        ))

        # --- CHOP_POINT: a later beat than outro_start ----------------------
        # outro_start marks where the outro BEGINS. We want the clip to play
        # through the outro until the groove stops being useful, then chop.
        # Source order (best -> fallback):
        #   1. End of last MIK segment with energy >= MIK_CHOP_ENERGY_FLOOR
        #   2. outro_start + MIK_CHOP_FALLBACK_BEATS_PAST_OUTRO (capped)
        chop_sec = None
        chop_source_label = None
        if mik_energy_segments:
            for s in reversed(mik_energy_segments):
                if int(s.energy) >= MIK_CHOP_ENERGY_FLOOR and s.start_sec > best_outro[1]:
                    chop_sec = s.end_sec
                    chop_source_label = f"end_of_last_E>={MIK_CHOP_ENERGY_FLOOR}_segment"
                    break

        if chop_sec is None:
            fallback_beat = best_outro[0] + MIK_CHOP_FALLBACK_BEATS_PAST_OUTRO
            chop_beat = min(fallback_beat, total_beats)
            chop_sec = first_downbeat_sec + chop_beat * sec_per_beat
            chop_source_label = f"outro_start+{MIK_CHOP_FALLBACK_BEATS_PAST_OUTRO}beats"
        else:
            chop_beat = (chop_sec - first_downbeat_sec) / sec_per_beat
            chop_beat = min(chop_beat, total_beats)

        if chop_beat > best_outro[0]:
            candidates.append(CueCandidate(
                beat=chop_beat,
                sec=chop_sec,
                cue_type="chop_point",
                confidence=MIK_ONLY_BASE_CONFIDENCE,
                sources=[f"mik_synth:{chop_source_label}"],
                reasons=[
                    f"Chop placed at {chop_sec:.1f}s ({chop_beat:.0f} beats) - "
                    f"{chop_source_label.replace('_', ' ')}. "
                    f"Lets the outro play before the loop takes over."
                ],
                interval_index=-1,
                region="post_last_rb_chorus",
                penalty=0.0,
            ))

    return candidates


# ---------------------------------------------------------------------------
# Visual hints (Sam's-eyes / Claude's-eyes broad-strokes anchors)
# ---------------------------------------------------------------------------

# Visual hints come from a HUMAN (or AI) looking at the waveform image and
# writing down where the structural moments are. They sit at the TOP of the
# confidence hierarchy - above MIK auto-cues, above librosa/amplitude analysis
# - because a real eye on the picture beats any algorithm at the broad-strokes
# question of "where's the first drop?".
#
# Schema (see Hints/track_hints.json):
#   "<track filename>": {
#     "first_drop_sec":  float,   # bass_entry candidate
#     "first_break_sec": float,   # break_start candidate
#     "outro_start_sec": float,   # outro_start candidate
#     "notes":           string   # optional, free text
#   }
HINT_CONFIDENCE = 0.95

HINT_TO_CUE_TYPE = {
    "first_drop_sec":     "bass_entry",
    "first_break_sec":    "break_start",
    "outro_start_sec":    "outro_start",
    "last_bass_drop_sec": "last_bass_drop",  # outgoing-role anchor for bass_swap:
                                              # aligns to incoming.first_drop_sec.
}


def hint_to_candidates(
    track_hint: dict,
    bpm: float,
    first_downbeat_sec: float,
    mik_cues_sec: list[float] | None = None,
) -> list[CueCandidate]:
    """Convert a human visual hint dict into CueCandidates.

    Each hint timestamp snaps to the nearest MIK cue (within 4s) or to the
    nearest whole beat - the broad-strokes time becomes a precise beat.
    """
    from automated_dj_mixes.amplitude_analysis import snap_to_mik_or_beat

    if not track_hint or bpm <= 0:
        return []

    sec_per_beat = 60.0 / bpm
    notes = track_hint.get("notes", "")
    out: list[CueCandidate] = []
    for hint_key, cue_type in HINT_TO_CUE_TYPE.items():
        raw_sec = track_hint.get(hint_key)
        if raw_sec is None:
            continue
        snapped_sec, snap_src = snap_to_mik_or_beat(
            float(raw_sec), bpm, first_downbeat_sec, mik_cues_sec,
        )
        beat = (snapped_sec - first_downbeat_sec) / sec_per_beat
        sources = [f"visual_hint@{raw_sec:.1f}s"]
        reasons = [
            f"Visual hint for {cue_type}: human reviewer marked "
            f"{raw_sec:.1f}s as the {cue_type.replace('_', ' ')} moment."
        ]
        if snap_src == "mik_cue":
            sources.append(f"snap_to_mik@{snapped_sec:.1f}s")
            reasons.append(f"Snapped to MIK cue at {snapped_sec:.1f}s (within 4s).")
        else:
            sources.append(f"snap_to_beat@{snapped_sec:.1f}s")
            reasons.append(f"Snapped to nearest whole beat ({snapped_sec:.1f}s) - no MIK cue within 4s.")
        if notes:
            reasons.append(f"Reviewer notes: {notes}")
        out.append(CueCandidate(
            beat=beat,
            sec=snapped_sec,
            cue_type=cue_type,
            confidence=HINT_CONFIDENCE,
            sources=sources,
            reasons=reasons,
            interval_index=-1,
            region="active",
            penalty=0.0,
        ))
    return out


def load_hints_file(hints_path) -> dict:
    """Read the per-mix hints JSON file. Returns {} if missing or invalid."""
    import json
    from pathlib import Path
    p = Path(hints_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Amplitude-envelope synthesis (the "look at the picture" signal)
# ---------------------------------------------------------------------------

def amplitude_to_candidates(
    audio_path,
    bpm: float,
    first_downbeat_sec: float,
    duration_sec: float,
    mik_cues_sec: list[float] | None = None,
) -> list[CueCandidate]:
    """Run amplitude-envelope analysis and emit CueCandidates.

    Sam's method: read the broad-stroke shape of the waveform - where it's
    loud and where it's quiet - and use that to find structural anchors.
    The amplitude envelope shows things MIK cues sometimes miss (e.g.
    VLAD's first drop at 16s, which has no MIK cue).

    Emits up to three candidates (each snapped to nearest MIK cue or
    whole beat):
      - bass_entry: largest amplitude rise in first 90s
      - break_start: first big amplitude drop after the first drop
      - outro_start: last big amplitude drop in the final 90s

    Confidence: AMP_BASE_CONFIDENCE (0.70), bumped to 0.85 if the
    amplitude change snapped onto a MIK cue (cross-source corroboration).
    """
    from automated_dj_mixes.amplitude_analysis import (
        compute_envelope,
        find_first_drop,
        find_first_break,
        find_outro_start,
        snap_to_mik_or_beat,
        AMP_BASE_CONFIDENCE,
        AMP_MIK_CORROBORATED_BONUS,
    )

    if bpm <= 0:
        return []

    try:
        times, env = compute_envelope(audio_path)
    except Exception:
        return []
    if len(env) == 0:
        return []

    sec_per_beat = 60.0 / bpm
    cands: list[CueCandidate] = []

    def emit(cue_type: str, raw_sec: float, delta: float, level_after: float,
             reason_prefix: str) -> None:
        snapped_sec, snap_src = snap_to_mik_or_beat(
            raw_sec, bpm, first_downbeat_sec, mik_cues_sec,
        )
        beat = (snapped_sec - first_downbeat_sec) / sec_per_beat
        confidence = AMP_BASE_CONFIDENCE
        sources = [f"amplitude_envelope_D{delta:+.2f}"]
        reasons = [
            f"{reason_prefix} at {raw_sec:.1f}s (D={delta:+.2f}, "
            f"level={level_after:.2f})."
        ]
        if snap_src == "mik_cue":
            confidence += AMP_MIK_CORROBORATED_BONUS
            sources.append(f"snap_to_mik@{snapped_sec:.1f}s")
            reasons.append(
                f"Snapped to MIK cue at {snapped_sec:.1f}s (within "
                f"4s - cross-source corroboration)."
            )
        else:
            sources.append(f"snap_to_beat@{snapped_sec:.1f}s")
            reasons.append(
                f"No MIK cue within 4s - snapped to the nearest whole "
                f"beat ({snapped_sec:.1f}s)."
            )
        cands.append(CueCandidate(
            beat=beat,
            sec=snapped_sec,
            cue_type=cue_type,
            confidence=min(1.0, confidence),
            sources=sources,
            reasons=reasons,
            interval_index=-1,
            region="active",
            penalty=0.0,
        ))

    drop_result = find_first_drop(env, times)
    if drop_result:
        drop_sec, drop_delta, drop_level = drop_result
        emit("bass_entry", drop_sec, drop_delta, drop_level,
             "Amplitude rises sharply (visual first drop)")

        break_result = find_first_break(env, times, drop_sec)
        if break_result:
            break_sec, break_delta, break_level = break_result
            emit("break_start", break_sec, -break_delta, break_level,
                 "Amplitude falls after the first drop (visual first break)")

    outro_result = find_outro_start(env, times, duration_sec)
    if outro_result:
        outro_sec, outro_delta, outro_level = outro_result
        emit("outro_start", outro_sec, -outro_delta, outro_level,
             "Last significant amplitude drop in the final 90s (outro begins)")

    return cands
