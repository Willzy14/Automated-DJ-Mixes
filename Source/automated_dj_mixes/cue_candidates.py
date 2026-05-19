"""Cue candidate detection — the interpretation layer.

Reads factual Interval records (from phrase_viz) and produces RANKED cue
candidates with confidence + sources + human-readable reasons.

Five cue types:
  bass_entry    — bass turns on (intro → drop transition)
  break_start   — bass turns off (drop → break)
  break_end     — bass returns after a break
  chop_point    — end-of-percussion (where outgoing tail begins; chop here)
  outro_start   — last drop ends, outro begins

Each candidate carries:
  - beat / sec for placement
  - confidence (0-1) based on signal agreement
  - sources list (signals that triggered it)
  - reasons list (human-readable explanation)
  - region (pre_first_rb_chorus | active | post_last_rb_chorus) — pre-chorus
    candidates get a small penalty but are NEVER hidden

Signal hierarchy (Sam's call, 2026-05): MIK 11 auto-cues are the most
trusted source. When a candidate aligns with a MIK cue (cue falls inside
the interval), the candidate gets the largest single confidence boost.

ANALYSIS_MODEL_VERSION = "cue-candidates-v1"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from automated_dj_mixes.features import ANALYSIS_MODEL_VERSION, TrackFeatures
from automated_dj_mixes.phrase_viz import Interval

# Tunable thresholds (percentile-based + delta)
BASS_ENTRY_LOW_BAND = "low"
BASS_ENTRY_HIGH_BAND = "high"
BASS_DELTA_RISE = 0.30      # fraction-of-track-max rise to confirm bass entry
BASS_DELTA_DROP = 0.30      # fraction-of-track-max drop to confirm break

# Region penalty for pre-first-chorus candidates (Codex: small, never hide)
PRE_CHORUS_PENALTY = 0.15
POST_CHORUS_PENALTY = 0.15

# MIK signal boost — biggest single source of confidence because MIK 11's
# auto-cue model has been refined over many years on dance music
MIK_CONFIDENCE_BOOST = 0.25
MIK_BOOST_TYPES = {"bass_entry", "break_start", "break_end", "outro_start"}


@dataclass
class CueCandidate:
    """One detected cue point. Confidence + explainability built-in."""
    beat: float                  # warp-beat coordinate (= source beat)
    sec: float                   # source-seconds (from TrackFeatures.beats)
    cue_type: str                # bass_entry | break_start | break_end | chop_point | outro_start
    confidence: float            # 0-1 (after region penalty applied)
    sources: list[str]           # ["rb_chorus_start", "librosa_bass_rise+42%", "wf_height_rise"]
    reasons: list[str]           # human-readable strings
    interval_index: int
    region: str                  # pre_first_rb_chorus | active | post_last_rb_chorus
    penalty: float = 0.0
    analysis_model_version: str = ANALYSIS_MODEL_VERSION


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

def _region_for(idx: int, first_drop_idx: int | None, last_drop_idx: int | None) -> tuple[str, float]:
    """Determine region label + penalty for an interval index."""
    if first_drop_idx is None:
        return "active", 0.0
    if idx < first_drop_idx:
        return "pre_first_rb_chorus", PRE_CHORUS_PENALTY
    if last_drop_idx is not None and idx > last_drop_idx:
        return "post_last_rb_chorus", POST_CHORUS_PENALTY
    return "active", 0.0


def _bass_changed(prev_bass: float, cur_bass: float, direction: str) -> tuple[bool, float]:
    """Did the bass cross a meaningful threshold? Returns (yes_no, delta)."""
    delta = cur_bass - prev_bass
    if direction == "rise":
        return (delta >= BASS_DELTA_RISE), delta
    if direction == "drop":
        return (-delta >= BASS_DELTA_DROP), delta
    return False, delta


def _interval_first_beat(interval: Interval, track_features: TrackFeatures) -> tuple[float, float]:
    """Return (warp_beat, sec) of the first beat in this interval."""
    pssi_idx = interval.pssi_start_beat - 1
    if 0 <= pssi_idx < len(track_features.beats):
        b = track_features.beats[pssi_idx]
        return float(b.beat_index), b.sec
    # Fall back to source_start_beats
    return interval.source_start_beats, 0.0


def _interval_time_range(interval: Interval, track_features: TrackFeatures) -> tuple[float, float]:
    """Return (start_sec, end_sec) covering this interval."""
    beats = track_features.beats
    start_idx = max(0, min(interval.pssi_start_beat - 1, len(beats) - 1))
    end_idx = max(0, min(interval.pssi_end_beat - 1, len(beats) - 1))
    start_sec = beats[start_idx].sec if beats else 0.0
    end_sec = beats[end_idx].sec if beats else 0.0
    return start_sec, end_sec


def _mik_cue_in_interval(
    interval: Interval,
    track_features: TrackFeatures,
    mik_cues_sec: list[float] | None,
) -> float | None:
    """If a MIK cue falls inside this interval's time range, return its time."""
    if not mik_cues_sec:
        return None
    start_sec, end_sec = _interval_time_range(interval, track_features)
    if end_sec <= start_sec:
        return None
    for t in mik_cues_sec:
        if start_sec <= t < end_sec:
            return t
    return None


def find_cue_candidates(
    intervals: list[Interval],
    track_features: TrackFeatures,
    mik_cues_sec: list[float] | None = None,
) -> list[CueCandidate]:
    """Walk intervals, emit ranked cue candidates.

    Detection is based on signal agreement:
      - Bass entry: bass crosses low → high band AND rises significantly
        AND (corroborating: RB chorus phrase starts here, waveform height
        rises). More signals = higher confidence.
      - Break start: inverse — bass drops out.
      - Break end: bass returns after at least one low interval.
      - Chop point: low bass + declining height + post-last-active region.
      - Outro start: first interval after the last RB chorus.

    Candidates are emitted with confidence in [0, 1] (already region-penalty
    adjusted). Pre-chorus candidates get a small penalty but are NEVER hidden.
    Returned list is sorted by (cue_type, -confidence).
    """
    if not intervals:
        return []

    # Find first/last "drop" intervals using simplified RB labels
    first_drop_idx = None
    last_drop_idx = None
    for i, iv in enumerate(intervals):
        if iv.rb_label == "chorus":
            if first_drop_idx is None:
                first_drop_idx = i
            last_drop_idx = i

    candidates: list[CueCandidate] = []
    in_break = False  # tracks state across intervals for break_end detection

    def apply_mik(cue_type: str, iv: Interval, sources: list[str], reasons: list[str], confidence: float) -> float:
        """Add MIK corroboration to a candidate if a MIK cue falls inside iv."""
        if cue_type not in MIK_BOOST_TYPES:
            return confidence
        mik_time = _mik_cue_in_interval(iv, track_features, mik_cues_sec)
        if mik_time is None:
            return confidence
        sources.append(f"mik_cue@{mik_time:.1f}s")
        reasons.append(f"Mixed In Key auto-cue at {mik_time:.1f}s falls inside this interval")
        return confidence + MIK_CONFIDENCE_BOOST

    for i, iv in enumerate(intervals):
        if i == 0:
            continue
        prev = intervals[i - 1]
        region, penalty = _region_for(i, first_drop_idx, last_drop_idx)
        warp_beat, sec = _interval_first_beat(iv, track_features)

        # --- BASS_ENTRY: low → high bass transition ---
        rose, bass_delta = _bass_changed(prev.energy.bass_librosa, iv.energy.bass_librosa, "rise")
        if rose and iv.energy.bass_band == "high":
            sources = [f"librosa_bass+{bass_delta:.0%}"]
            reasons = [
                f"Bass rose from {prev.energy.bass_librosa:.2f} to {iv.energy.bass_librosa:.2f} (track-normalized)"
            ]
            confidence = 0.55
            if iv.rb_label == "chorus" and prev.rb_label != "chorus":
                sources.append("rb_chorus_start")
                reasons.append("Rekordbox chorus phrase begins at this interval")
                confidence += 0.20
            if (iv.energy.waveform_height is not None
                    and prev.energy.waveform_height is not None
                    and iv.energy.waveform_height - prev.energy.waveform_height >= 0.15):
                sources.append("wf_height_rise")
                reasons.append(
                    f"Rekordbox waveform height rose +{(iv.energy.waveform_height - prev.energy.waveform_height):.0%}"
                )
                confidence += 0.10
            confidence = apply_mik("bass_entry", iv, sources, reasons, confidence)
            confidence = min(1.0, confidence) * (1 - penalty)
            if region != "active":
                reasons.append(f"(region: {region}, penalty {penalty:.0%})")
            candidates.append(CueCandidate(
                beat=warp_beat, sec=sec, cue_type="bass_entry",
                confidence=confidence, sources=sources, reasons=reasons,
                interval_index=i, region=region, penalty=penalty,
            ))

        # --- BREAK_START: high → low bass transition ---
        dropped, bass_delta = _bass_changed(prev.energy.bass_librosa, iv.energy.bass_librosa, "drop")
        if dropped and iv.energy.bass_band == "low":
            sources = [f"librosa_bass{bass_delta:.0%}"]
            reasons = [
                f"Bass dropped from {prev.energy.bass_librosa:.2f} to {iv.energy.bass_librosa:.2f}"
            ]
            confidence = 0.55
            if iv.rb_label in ("down", "up") and prev.rb_label == "chorus":
                sources.append("rb_phrase_change_to_break")
                reasons.append(f"Rekordbox transitions from chorus to {iv.rb_label}")
                confidence += 0.20
            if (iv.energy.waveform_height is not None
                    and prev.energy.waveform_height is not None
                    and prev.energy.waveform_height - iv.energy.waveform_height >= 0.15):
                sources.append("wf_height_drop")
                reasons.append(
                    f"Rekordbox waveform height dropped {(prev.energy.waveform_height - iv.energy.waveform_height):.0%}"
                )
                confidence += 0.10
            confidence = apply_mik("break_start", iv, sources, reasons, confidence)
            confidence = min(1.0, confidence) * (1 - penalty)
            if region != "active":
                reasons.append(f"(region: {region}, penalty {penalty:.0%})")
            candidates.append(CueCandidate(
                beat=warp_beat, sec=sec, cue_type="break_start",
                confidence=confidence, sources=sources, reasons=reasons,
                interval_index=i, region=region, penalty=penalty,
            ))
            in_break = True

        # --- BREAK_END: bass returns after low ---
        if in_break and iv.energy.bass_band == "high" and prev.energy.bass_band == "low":
            sources = ["librosa_bass_return"]
            reasons = [
                f"Bass returned to high level ({iv.energy.bass_librosa:.2f}) after break"
            ]
            confidence = 0.55
            if iv.rb_label == "chorus":
                sources.append("rb_chorus_resumes")
                reasons.append("Rekordbox chorus phrase resumes")
                confidence += 0.20
            confidence = apply_mik("break_end", iv, sources, reasons, confidence)
            confidence = min(1.0, confidence) * (1 - penalty)
            if region != "active":
                reasons.append(f"(region: {region}, penalty {penalty:.0%})")
            candidates.append(CueCandidate(
                beat=warp_beat, sec=sec, cue_type="break_end",
                confidence=confidence, sources=sources, reasons=reasons,
                interval_index=i, region=region, penalty=penalty,
            ))
            in_break = False

        # --- CHOP_POINT: low bass + height declining + post-last-active ---
        if (last_drop_idx is not None
                and i > last_drop_idx
                and iv.energy.bass_band == "low"
                and (iv.energy.waveform_height is None
                     or (prev.energy.waveform_height is not None
                         and iv.energy.waveform_height < prev.energy.waveform_height))):
            sources = ["bass_low_post_last_drop"]
            reasons = [f"After last RB chorus, bass at low band ({iv.energy.bass_band})"]
            confidence = 0.55
            if iv.energy.waveform_height is not None and prev.energy.waveform_height is not None:
                drop_amt = prev.energy.waveform_height - iv.energy.waveform_height
                if drop_amt > 0.05:
                    sources.append("wf_height_declining")
                    reasons.append(f"Waveform height declining (-{drop_amt:.0%})")
                    confidence += 0.10
            confidence = min(1.0, confidence) * (1 - penalty)
            if region != "active":
                reasons.append(f"(region: {region}, penalty {penalty:.0%})")
            candidates.append(CueCandidate(
                beat=warp_beat, sec=sec, cue_type="chop_point",
                confidence=confidence, sources=sources, reasons=reasons,
                interval_index=i, region=region, penalty=penalty,
            ))

    # --- OUTRO_START: first interval after the last RB chorus ---
    if last_drop_idx is not None and last_drop_idx + 1 < len(intervals):
        iv = intervals[last_drop_idx + 1]
        region, penalty = _region_for(last_drop_idx + 1, first_drop_idx, last_drop_idx)
        warp_beat, sec = _interval_first_beat(iv, track_features)
        sources = ["post_last_rb_chorus"]
        reasons = ["First 8-bar interval after the last Rekordbox chorus phrase"]
        confidence = 0.70
        if iv.rb_label == "outro":
            sources.append("rb_outro_phrase")
            reasons.append("Rekordbox outro phrase begins")
            confidence += 0.15
        confidence = apply_mik("outro_start", iv, sources, reasons, confidence)
        confidence = min(1.0, confidence) * (1 - penalty)
        candidates.append(CueCandidate(
            beat=warp_beat, sec=sec, cue_type="outro_start",
            confidence=confidence, sources=sources, reasons=reasons,
            interval_index=last_drop_idx + 1, region=region, penalty=penalty,
        ))

    # Sort by (cue_type, -confidence)
    candidates.sort(key=lambda c: (c.cue_type, -c.confidence))
    return candidates


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


FIRST_DROP_WINDOW_SEC = (30.0, 75.0)
"""Dance music structural prior (Sam's rule, 2026-05-19): the FIRST drop
typically lands 30-75s into the track. A bass_entry past 75s is usually a
SECOND chorus (post-break) and would force the listener to hear 1-2 minutes
of the incoming track before the swap — too long for a bass-to-bass mix.
Anything before 30s is usually a teaser/first-kick, not yet a real chorus.
"""


def first_drop_candidate(
    candidates: Iterable[CueCandidate],
    min_confidence: float = 0.5,
) -> CueCandidate | None:
    """Pick the best bass_entry for a bass-to-bass mix.

    Precedence (Sam's rule, 2026-05-19):
      1. Visual hint wins — Sam's eye on the picture overrides everything.
      2. Earliest rb_chorus_start within the first-drop window (30-120s).
         Rekordbox marks chorus phrases — we want the FIRST one (sustained
         main groove, not a teaser drop), but not so late that we'd be
         dropping the incoming 2+ minutes in.
      3. Earliest credible bass_entry within the first-drop window.
      4. Fallback: earliest credible bass_entry anywhere (only if nothing
         in the window — e.g. a short track with no clear first-drop signal).
    """
    pool = [c for c in candidates
            if c.cue_type == "bass_entry" and c.confidence >= min_confidence]
    if not pool:
        return None

    # 1. Visual hints win
    hinted = [c for c in pool if _is_visual_hint(c)]
    if hinted:
        return min(hinted, key=lambda c: c.beat)

    # Filter to candidates within the first-drop window
    lo, hi = FIRST_DROP_WINDOW_SEC
    in_window = [c for c in pool if lo <= c.sec <= hi]

    # 2. Prefer rb_chorus_start within the window (the sustained chorus)
    chorus_in_window = [c for c in in_window if "rb_chorus_start" in c.sources]
    if chorus_in_window:
        return min(chorus_in_window, key=lambda c: c.beat)

    # 3. Earliest credible bass_entry within the window
    if in_window:
        return min(in_window, key=lambda c: c.beat)

    # 4. Last resort: earliest credible anywhere (short tracks, edge cases)
    return min(pool, key=lambda c: c.beat)


# ---------------------------------------------------------------------------
# MIK-only synthesis (used when Rekordbox phrase data isn't available)
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

    Used when Rekordbox phrase data is unavailable. Picks two anchors:
      - bass_entry  = FIRST MIK cue past the intro skip (the first drop —
                      what a DJ cares about)
      - outro_start = LAST MIK cue before the outro tail (the final
                      chorus → outro transition)

    Energy validation is applied as a CONFIDENCE BOOST, not for cue
    selection. Sam's rule (2026-05): "checking the energy in front and
    behind the key point — that's essentially what we're looking for: an
    energy change." We honour that by reporting whether the picked cue
    DOES show an energy delta — if yes, confidence rises and the source
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
        f"({best_drop[0]:.0f} beats from first downbeat) — "
        f"first cue past the {MIK_INTRO_SKIP_BEATS}-beat intro region"
    ]
    confidence = MIK_ONLY_BASE_CONFIDENCE
    if best_drop_delta is None:
        reasons.append("No MIK energy data around this cue — confidence unboosted")
    elif best_drop_delta[0] == "rise":
        _, delta, e_before, e_after = best_drop_delta
        sources.append(f"mik_energy_rise+{delta}")
        reasons.append(
            f"MIK energy rises E{e_before} → E{e_after} (Δ+{delta}) — "
            f"confirms a real drop"
        )
        confidence += MIK_ENERGY_VALIDATED_BONUS
    else:
        _, delta, e_before, e_after = best_drop_delta
        sources.append(f"mik_energy_flat({delta:+d})")
        reasons.append(
            f"MIK energy stays flat (E{e_before} → E{e_after}, Δ{delta:+d}) — "
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
            f"({best_outro[0]:.0f} beats) — last cue before the "
            f"{MIK_OUTRO_TAIL_BEATS}-beat outro tail"
        ]
        confidence = MIK_ONLY_BASE_CONFIDENCE
        if best_outro_delta is None:
            reasons.append("No MIK energy data around this cue — confidence unboosted")
        elif best_outro_delta[0] == "drop":
            _, delta, e_before, e_after = best_outro_delta
            sources.append(f"mik_energy_drop-{delta}")
            reasons.append(
                f"MIK energy drops E{e_before} → E{e_after} (Δ-{delta}) — "
                f"confirms entry into the outro"
            )
            confidence += MIK_ENERGY_VALIDATED_BONUS
        else:
            _, delta, e_before, e_after = best_outro_delta
            sources.append(f"mik_energy_flat({delta:+d})")
            reasons.append(
                f"MIK energy stays flat around this cue (E{e_before} → E{e_after}, "
                f"Δ{delta:+d}) — outro may be a soft fade rather than a hard drop"
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
        # Source order (best → fallback):
        #   1. End of last MIK segment with energy >= MIK_CHOP_ENERGY_FLOOR
        #   2. outro_start + MIK_CHOP_FALLBACK_BEATS_PAST_OUTRO (capped)
        chop_sec = None
        chop_source_label = None
        if mik_energy_segments:
            for s in reversed(mik_energy_segments):
                if int(s.energy) >= MIK_CHOP_ENERGY_FLOOR and s.start_sec > best_outro[1]:
                    chop_sec = s.end_sec
                    chop_source_label = f"end_of_last_E≥{MIK_CHOP_ENERGY_FLOOR}_segment"
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
                    f"Chop placed at {chop_sec:.1f}s ({chop_beat:.0f} beats) — "
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
# confidence hierarchy — above MIK auto-cues, above RB phrases, above
# librosa/amplitude analysis — because a real eye on the picture beats any
# algorithm at the broad-strokes question of "where's the first drop?".
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
    "last_bass_drop_sec": "last_bass_drop",  # NEW (2026-05-19): outgoing-role anchor
                                              # for bass_swap. The musical fill near the
                                              # end of the track where bass naturally
                                              # drops out before final kicks return.
                                              # Aligns to incoming.first_drop_sec.
}


def hint_to_candidates(
    track_hint: dict,
    bpm: float,
    first_downbeat_sec: float,
    mik_cues_sec: list[float] | None = None,
) -> list[CueCandidate]:
    """Convert a human visual hint dict into CueCandidates.

    Each hint timestamp snaps to the nearest MIK cue (within 4s) or to the
    nearest whole beat — the broad-strokes time becomes a precise beat.
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
            reasons.append(f"Snapped to nearest whole beat ({snapped_sec:.1f}s) — no MIK cue within 4s.")
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

    Sam's method: read the broad-stroke shape of the waveform — where it's
    loud and where it's quiet — and use that to find structural anchors.
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
        sources = [f"amplitude_envelope_Δ{delta:+.2f}"]
        reasons = [
            f"{reason_prefix} at {raw_sec:.1f}s (Δ={delta:+.2f}, "
            f"level={level_after:.2f})."
        ]
        if snap_src == "mik_cue":
            confidence += AMP_MIK_CORROBORATED_BONUS
            sources.append(f"snap_to_mik@{snapped_sec:.1f}s")
            reasons.append(
                f"Snapped to MIK cue at {snapped_sec:.1f}s (within "
                f"4s — cross-source corroboration)."
            )
        else:
            sources.append(f"snap_to_beat@{snapped_sec:.1f}s")
            reasons.append(
                f"No MIK cue within 4s — snapped to the nearest whole "
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
