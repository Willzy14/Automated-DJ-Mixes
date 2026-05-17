"""Phrase visualization + factual interval records.

This module produces TWO outputs:

1. Interval list — FACTUAL observations per 8-bar slot:
   - which Rekordbox phrase dominates this interval
   - rms / bass / waveform-height energy levels (raw + percentile bands)
   No interpretation. No "drop/break" labels. That happens in cue_candidates.py.

2. PhraseSegment list — visualization-only collapse of intervals into
   colour-coded clips for Ableton display. Uses position + RB phrase to
   colour-code intro / drop / break / outro.

ANALYSIS_MODEL_VERSION = "cue-candidates-v1"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from automated_dj_mixes.features import (
    ANALYSIS_MODEL_VERSION,
    BeatFeatures,
    FeatureStats,
    TrackFeatures,
)

# Ableton Live 12 color palette indices (18=green, 14=red confirmed by Sam in V2)
COLOR_INTRO = 18   # green
COLOR_BREAK = 50   # blue
COLOR_DROP  = 12   # yellow
COLOR_OUTRO = 14   # red
COLOR_UNKNOWN = 7  # neutral gray

LABEL_TO_COLOR = {
    "intro": COLOR_INTRO,
    "break": COLOR_BREAK,
    "drop":  COLOR_DROP,
    "outro": COLOR_OUTRO,
    "unknown": COLOR_UNKNOWN,
}


@dataclass
class IntervalEnergy:
    """Energy observations for one 8-bar interval. Raw + percentile band."""
    rms_norm: float          # 0-1 normalized RMS (librosa)
    bass_librosa: float      # 0-1 normalized 40-180 Hz band (librosa)
    waveform_height: float | None   # 0-1 normalized PWV5 height; None if unavailable

    rms_band: str            # "low" | "mid" | "high" by track-local p30/p70
    bass_band: str           # same for bass
    wf_height_band: str | None   # same for waveform height


@dataclass
class Interval:
    """One 8-bar slot of a track. Factual observations only — no labels.

    Position is stored in BOTH source-beat coordinates (warp-beat, can be
    negative for tracks with first_downbeat_offset>0) AND PSSI beat indices
    (1-based, matching Rekordbox phrase data).
    """
    index: int
    pssi_start_beat: int        # 1-based PSSI beat where interval starts
    pssi_end_beat: int          # 1-based PSSI beat where interval ends (exclusive)
    source_start_beats: float   # warp-beat coordinate (= pssi - 1 - offset)
    source_end_beats: float
    rb_label: str | None        # majority Rekordbox phrase: intro/up/down/chorus/outro/None
    energy: IntervalEnergy

    analysis_model_version: str = ANALYSIS_MODEL_VERSION


@dataclass
class PhraseSegment:
    """A coloured visualization clip (one segment = N merged intervals)."""
    source_start_beats: float
    source_end_beats: float
    label: str          # intro / drop / break / outro / unknown
    color: int
    name: str


def _band(value: float, stats: FeatureStats) -> str:
    """Classify a value as low/mid/high relative to track-local percentiles."""
    if value < stats.p30:
        return "low"
    if value > stats.p70:
        return "high"
    return "mid"


def _simplify_rb(rb_label: str | None) -> str | None:
    if rb_label is None:
        return None
    return {
        "intro": "intro",
        "up": "break",
        "down": "break",
        "chorus": "drop",
        "outro": "outro",
    }.get(rb_label)


def build_intervals(
    rb_analysis,
    track_features: TrackFeatures,
    interval_bars: int = 8,
) -> list[Interval]:
    """Walk per-beat features in 8-bar slots, emit factual Interval records.

    No interpretation: each Interval carries the dominant RB phrase label and
    the aggregated energy signals. Labels like "drop" or "break" come later
    (cue_candidates.py and segments_from_intervals()).
    """
    if not rb_analysis or not rb_analysis.phrases:
        return []

    offset = track_features.first_downbeat_offset
    interval_beats = interval_bars * 4
    first_downbeat_pssi = 1 + offset
    end_pssi = rb_analysis.end_beat

    boundaries: list[int] = [first_downbeat_pssi]
    pssi = first_downbeat_pssi + interval_beats
    while pssi < end_pssi:
        boundaries.append(pssi)
        pssi += interval_beats
    boundaries.append(end_pssi)

    phrases = rb_analysis.phrases
    phrase_ends = [rb_analysis.phrase_end_beat(i) for i in range(len(phrases))]

    def dominant_rb_label(pssi_start: int, pssi_end: int) -> str | None:
        counts: dict[str, int] = {}
        for p, p_end in zip(phrases, phrase_ends):
            overlap = max(0, min(p_end, pssi_end) - max(p.start_beat, pssi_start))
            if overlap > 0:
                counts[p.label] = counts.get(p.label, 0) + overlap
        if not counts:
            return None
        return max(counts.keys(), key=lambda k: counts[k])

    beats = track_features.beats
    stats = track_features.stats

    intervals: list[Interval] = []
    for i in range(len(boundaries) - 1):
        pssi_start = boundaries[i]
        pssi_end = boundaries[i + 1]
        rb_label = dominant_rb_label(pssi_start, pssi_end)

        # Aggregate beat features within this interval
        start_idx = pssi_start - 1
        end_idx = pssi_end - 1
        slice_ = beats[start_idx:end_idx]
        if slice_:
            rms_avg = sum(b.rms for b in slice_) / len(slice_)
            bass_avg = sum(b.bass for b in slice_) / len(slice_)
            wf_values = [b.wf_height for b in slice_ if b.wf_height is not None]
            wf_avg = sum(wf_values) / len(wf_values) if wf_values else None
        else:
            rms_avg = 0.0
            bass_avg = 0.0
            wf_avg = None

        wf_band = _band(wf_avg, stats["wf_height"]) if wf_avg is not None and stats["wf_height"].p70 > 0 else None
        energy = IntervalEnergy(
            rms_norm=rms_avg,
            bass_librosa=bass_avg,
            waveform_height=wf_avg,
            rms_band=_band(rms_avg, stats["rms"]),
            bass_band=_band(bass_avg, stats["bass"]),
            wf_height_band=wf_band,
        )

        source_start = float(pssi_start - 1 - offset)
        source_end = float(pssi_end - 1 - offset)
        if i == 0 and offset > 0:
            source_start = -float(offset)

        intervals.append(Interval(
            index=i,
            pssi_start_beat=pssi_start,
            pssi_end_beat=pssi_end,
            source_start_beats=source_start,
            source_end_beats=source_end,
            rb_label=rb_label,
            energy=energy,
        ))

    return intervals


def segments_from_intervals(intervals: list[Interval]) -> list[PhraseSegment]:
    """Collapse factual intervals into colour-coded visualization segments.

    Uses position + simplified RB label (intro/drop/break/outro) only.
    Energy data NOT consulted here — that's for cue detection, not viz.
    """
    if not intervals:
        return []

    # Find first/last drop intervals to bracket intro/outro
    simple_labels: list[str] = []
    for iv in intervals:
        s = _simplify_rb(iv.rb_label)
        simple_labels.append(s if s else "unknown")

    first_drop = None
    last_drop = None
    for i, l in enumerate(simple_labels):
        if l == "drop":
            if first_drop is None:
                first_drop = i
            last_drop = i

    final_labels: list[str] = []
    for i, l in enumerate(simple_labels):
        if first_drop is None:
            final_labels.append(l)
        elif i < first_drop:
            final_labels.append("intro")
        elif i > last_drop:
            final_labels.append("outro")
        elif l in ("intro", "outro", "unknown"):
            final_labels.append("break")
        else:
            final_labels.append(l)

    segments: list[PhraseSegment] = []
    counters = {"intro": 0, "drop": 0, "break": 0, "outro": 0, "unknown": 0}

    i = 0
    while i < len(final_labels):
        j = i + 1
        while j < len(final_labels) and final_labels[j] == final_labels[i]:
            j += 1
        label = final_labels[i]
        counters[label] += 1
        segments.append(PhraseSegment(
            source_start_beats=intervals[i].source_start_beats,
            source_end_beats=intervals[j - 1].source_end_beats,
            label=label,
            color=LABEL_TO_COLOR.get(label, COLOR_UNKNOWN),
            name=f"{label}_{counters[label]}",
        ))
        i = j

    return segments
