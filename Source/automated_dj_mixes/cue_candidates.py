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


def find_cue_candidates(
    intervals: list[Interval],
    track_features: TrackFeatures,
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


def first_credible(
    candidates: Iterable[CueCandidate],
    cue_type: str,
    min_confidence: float = 0.5,
) -> CueCandidate | None:
    """Convenience: top candidate of a type. Returns None if nothing credible."""
    ranked = candidates_for(candidates, cue_type, min_confidence)
    return ranked[0] if ranked else None
