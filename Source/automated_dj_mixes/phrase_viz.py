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
COLOR_INTRO = 18   # green — pure intro (kicks-only, no bass)
COLOR_BUILD = 23   # cyan — intro build-zone / teaser before the first real drop
COLOR_BREAK = 50   # blue — main break / long low-bass section in the body
COLOR_DROP  = 12   # yellow — sustained high-bass drop / chorus
COLOR_OUTRO = 14   # red — outro
COLOR_FILL  = 9    # orange — short drop in energy within the body (middle-8 / 1-4 bar fill)
COLOR_BEAT_DROPOUT = 55  # purple — short raw-kick gap inside a coarse section
COLOR_UNKNOWN = 7  # neutral gray

LABEL_TO_COLOR = {
    "intro": COLOR_INTRO,
    "build": COLOR_BUILD,
    "break": COLOR_BREAK,
    "drop":  COLOR_DROP,
    "outro": COLOR_OUTRO,
    "fill":  COLOR_FILL,
    "beat_dropout": COLOR_BEAT_DROPOUT,
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

    # First pass: collapse adjacent same-label intervals into raw segments.
    raw_segments: list[tuple[int, int, str]] = []  # (start_iv_idx, end_iv_idx, label)
    i = 0
    while i < len(final_labels):
        j = i + 1
        while j < len(final_labels) and final_labels[j] == final_labels[i]:
            j += 1
        raw_segments.append((i, j - 1, final_labels[i]))
        i = j

    # Second pass (Sam's rule 2026-05-19): the LONGEST "break" segment in the
    # body is the MAIN break (blue). Other body "break" segments are short
    # fills / middle-8s (orange). This distinguishes the structural break
    # everyone hears from short low-energy drops that punctuate the body.
    break_idxs = [k for k, (_, _, lbl) in enumerate(raw_segments) if lbl == "break"]
    if len(break_idxs) > 1:
        def _length(k):
            s, e, _ = raw_segments[k]
            return e - s + 1
        # Keep the LONGEST break as the main "break"; all others → "fill".
        # Ties broken by EARLIEST position (first big break is the canonical one).
        main_break_idx = max(break_idxs, key=lambda k: (_length(k), -k))
        for k in break_idxs:
            if k != main_break_idx:
                s, e, _ = raw_segments[k]
                raw_segments[k] = (s, e, "fill")

    segments: list[PhraseSegment] = []
    counters = {"intro": 0, "build": 0, "drop": 0, "break": 0, "outro": 0, "fill": 0, "unknown": 0}
    for s_idx, e_idx, label in raw_segments:
        counters[label] += 1
        segments.append(PhraseSegment(
            source_start_beats=intervals[s_idx].source_start_beats,
            source_end_beats=intervals[e_idx].source_end_beats,
            label=label,
            color=LABEL_TO_COLOR.get(label, COLOR_UNKNOWN),
            name=f"{label}_{counters[label]}",
        ))

    return segments


def segments_from_stem_sections(
    stem_result: dict,
    beat_times_ms: list[int] | None = None,
    first_downbeat_offset: int = 0,
) -> list[PhraseSegment]:
    """Convert a stem_detector.detect() result into PhraseSegment clips.

    The stem detector already labels sections (intro/drop/break/fill/outro) — the
    same label set this module colour-codes. This is the bridge that lets the
    stem detector replace the RB-phrase section source; no refine_segments pass
    is needed (the stem rules are already final).

    ONE-CLOCK RULE (2026-06-11 regression fix): when the track's beat grid is
    supplied, each section boundary is mapped from its detected TIME
    (start_sec/end_sec) onto the clip's warp-beat coordinate through the grid
    itself (sec_to_clip_beats — the same convention the warp markers use),
    then snapped to the nearest bar. Cuts therefore land on the WARPED audio
    by construction, even where the detector's constant-BPM clock and the
    grid disagree. Without a grid, falls back to bar*4 on the detector clock
    (standalone/legacy use).
    """
    sections = stem_result.get("sections", [])
    segments: list[PhraseSegment] = []
    counters: dict[str, int] = {}

    use_grid = (
        beat_times_ms is not None
        and len(beat_times_ms) >= 8
        and all("start_sec" in s and "end_sec" in s for s in sections)
    )
    if use_grid:
        from automated_dj_mixes.warping import sec_to_clip_beats

        def to_bar_beats(t_sec: float) -> float:
            raw = sec_to_clip_beats(t_sec, beat_times_ms, first_downbeat_offset)
            return round(raw / 4.0) * 4.0

    prev_end: float | None = None
    for s in sections:
        label = s["label"]
        counters[label] = counters.get(label, 0) + 1
        if use_grid:
            start = to_bar_beats(float(s["start_sec"]))
            end = to_bar_beats(float(s["end_sec"]))
            # Contiguity + zero-length guards: adjacent sections share a
            # boundary in time, so they snap identically — but if bar
            # rounding ever collapses a 1-bar section, keep it ≥1 bar and
            # monotonic (a zero/negative-length clip corrupts the .als).
            if prev_end is not None and start < prev_end:
                start = prev_end
            if end <= start:
                end = start + 4.0
            prev_end = end
        else:
            start = float(s["start_bar"]) * 4.0
            end = float(s["end_bar"]) * 4.0
        segments.append(PhraseSegment(
            source_start_beats=start,
            source_end_beats=end,
            label=label,
            color=LABEL_TO_COLOR.get(label, COLOR_UNKNOWN),
            name=f"{label}_{counters[label]}",
        ))
    return segments


# ---------------------------------------------------------------------------
# Fine-grained refinement (Sam V2 feedback 2026-05-19)
#
# Coarse segments_from_intervals() works at 8-bar granularity using RB phrase
# labels. That misses two patterns Sam adds manually:
#
#   1. Build-zone within long intros — when an intro is 24+ bars, the last
#      8-16 bars are usually a teaser/build with rising bass energy. Split
#      these out as "build" (cyan).
#   2. Short fills within drops — 1-4 bar bass dips that punctuate the
#      chorus. Need beat-level (not interval-level) detection.
# ---------------------------------------------------------------------------

# Tunable thresholds — derived from V3 vs V4 diff on the Black Book project.
BUILD_MIN_INTRO_BARS = 24            # Don't split intros shorter than this
BUILD_BASS_RATIO_THRESHOLD = 1.2     # Build zone has ≥1.2× pure-intro bass (was 1.4)
BUILD_RMS_RATIO_THRESHOLD = 1.3      # OR overall RMS climbs ≥1.3× (catches synth builds)
BUILD_WINDOW_BARS = 4                # Look at 4-bar windows

FILL_MIN_BARS = 1                    # Min fill length
FILL_MAX_BARS_AS_FILL = 4            # 1-4 bar dip → fill (orange)
                                     # 5+ bar dip → break (blue) — Sam's rule from V4 diff
FILL_BASS_START_RATIO = 0.4          # Dip starts when bass drops below 40% of section mean
FILL_BASS_END_RATIO = 0.7            # Dip ends when bass climbs back above 70% (hysteresis)
DROP_MIN_BARS_FOR_FILL_SCAN = 16     # Only scan drops ≥16 bars for fills

# V8 visual-pass tunings (2026-05-20) — fixes from comparing V8 vs Sam's V7.
DROP_START_BODY_BARS = 8             # Body sample = bars 8..16 of the drop
DROP_START_RMS_RATIO = 0.8           # If bar[i] RMS < 80% of body, it's still build
DROP_START_MAX_PUSH_BARS = 16        # Never push drop start more than 16 bars forward
BREAK_TRIM_MIN_BARS = 5              # Trim breaks that are 5..15 bars
BREAK_TRIM_MAX_BARS = 15             # (16+ bar breaks are real, don't trim)
BREAK_TRIM_BASS_RATIO = 0.6          # Tail bars above 60% of next-drop bass = high, trim them
OUTRO_REFINE_LOOKBACK_BARS = 4       # Look at last 4 bars of preceding drop
OUTRO_REFINE_BASS_RATIO = 0.7        # If those bars have bass < 70% of drop body, extend outro back


def _bass_per_bar(track_features, source_start_beats: float, source_end_beats: float) -> list[float]:
    """Aggregate per-beat bass into 4-beat (1-bar) means for a source range."""
    return _feature_per_bar(track_features, source_start_beats, source_end_beats, "bass")


def _feature_per_bar(track_features, source_start_beats: float, source_end_beats: float,
                     attr: str = "bass") -> list[float]:
    """Aggregate any per-beat feature into 4-beat (1-bar) means."""
    beats = track_features.beats
    start = max(0, int(source_start_beats))
    end = min(len(beats), int(source_end_beats))
    out = []
    i = start
    while i + 4 <= end:
        chunk = [getattr(beats[j], attr) for j in range(i, i + 4)]
        out.append(sum(chunk) / 4)
        i += 4
    if end - i >= 2:
        chunk = [getattr(beats[j], attr) for j in range(i, end)]
        out.append(sum(chunk) / len(chunk))
    return out


def _split_intro_build_zone(seg: PhraseSegment, track_features) -> list[PhraseSegment]:
    """If an intro is long and its tail has rising energy, split into intro + build.

    Uses BOTH bass and overall RMS — synth builds raise RMS without raising
    bass much (Capriati, Route 94 have this pattern).
    """
    duration_beats = seg.source_end_beats - seg.source_start_beats
    bars = duration_beats / 4
    if bars < BUILD_MIN_INTRO_BARS:
        return [seg]

    bar_bass = _feature_per_bar(track_features, seg.source_start_beats, seg.source_end_beats, "bass")
    bar_rms = _feature_per_bar(track_features, seg.source_start_beats, seg.source_end_beats, "rms")
    if len(bar_bass) < BUILD_MIN_INTRO_BARS // 2:
        return [seg]

    # First quarter = pure intro baseline. Look for first 4-bar window where
    # EITHER bass ≥ 1.2× baseline OR overall RMS ≥ 1.3× baseline.
    q = max(4, len(bar_bass) // 4)
    pure_bass = sum(bar_bass[:q]) / q
    pure_rms = sum(bar_rms[:q]) / q if bar_rms else 0.0
    if pure_bass <= 0 and pure_rms <= 0:
        return [seg]

    build_start_bar = None
    for i in range(q, len(bar_bass) - BUILD_WINDOW_BARS + 1):
        bass_window = sum(bar_bass[i : i + BUILD_WINDOW_BARS]) / BUILD_WINDOW_BARS
        rms_window = sum(bar_rms[i : i + BUILD_WINDOW_BARS]) / BUILD_WINDOW_BARS if bar_rms else 0.0
        bass_rose = pure_bass > 0 and bass_window >= BUILD_BASS_RATIO_THRESHOLD * pure_bass
        rms_rose = pure_rms > 0 and rms_window >= BUILD_RMS_RATIO_THRESHOLD * pure_rms
        if bass_rose or rms_rose:
            build_start_bar = i
            break

    if build_start_bar is None or build_start_bar < 4:
        return [seg]

    split_beat = seg.source_start_beats + build_start_bar * 4
    return [
        PhraseSegment(
            source_start_beats=seg.source_start_beats,
            source_end_beats=split_beat,
            label="intro",
            color=COLOR_INTRO,
            name="intro",
        ),
        PhraseSegment(
            source_start_beats=split_beat,
            source_end_beats=seg.source_end_beats,
            label="build",
            color=COLOR_BUILD,
            name="build",
        ),
    ]


def _split_drop_with_fills(seg: PhraseSegment, track_features) -> list[PhraseSegment]:
    """Within a long drop, find bass dips. 1-4 bar dips → fill; 5+ bar dips → break.

    Uses hysteresis: dip starts at <40% of section mean, ends when bass
    climbs back above 70%. This avoids the V3 issue where my fills ended
    too late because bass was still ramping back up.
    """
    duration_beats = seg.source_end_beats - seg.source_start_beats
    bars = duration_beats / 4
    if bars < DROP_MIN_BARS_FOR_FILL_SCAN:
        return [seg]

    bar_bass = _bass_per_bar(track_features, seg.source_start_beats, seg.source_end_beats)
    if not bar_bass:
        return [seg]
    section_mean = sum(bar_bass) / len(bar_bass)
    if section_mean <= 0:
        return [seg]

    start_threshold = section_mean * FILL_BASS_START_RATIO
    end_threshold = section_mean * FILL_BASS_END_RATIO

    # Hysteresis dip detection
    dips: list[tuple[int, int]] = []
    i = 0
    while i < len(bar_bass):
        if bar_bass[i] < start_threshold:
            j = i + 1
            while j < len(bar_bass) and bar_bass[j] < end_threshold:
                j += 1
            length = j - i
            if length >= FILL_MIN_BARS:
                # Don't accept dips touching the section boundaries
                if i > 0 and j < len(bar_bass):
                    dips.append((i, j))
            i = j
        else:
            i += 1

    if not dips:
        return [seg]

    out: list[PhraseSegment] = []
    cursor = 0
    for s_bar, e_bar in dips:
        if s_bar > cursor:
            out.append(PhraseSegment(
                source_start_beats=seg.source_start_beats + cursor * 4,
                source_end_beats=seg.source_start_beats + s_bar * 4,
                label="drop",
                color=COLOR_DROP,
                name="drop",
            ))
        dip_len = e_bar - s_bar
        # Sam's V4 rule: 1-4 bar dip = fill (orange), 5+ bar dip = break (blue).
        if dip_len <= FILL_MAX_BARS_AS_FILL:
            label, color = "fill", COLOR_FILL
        else:
            label, color = "break", COLOR_BREAK
        out.append(PhraseSegment(
            source_start_beats=seg.source_start_beats + s_bar * 4,
            source_end_beats=seg.source_start_beats + e_bar * 4,
            label=label,
            color=color,
            name=label,
        ))
        cursor = e_bar
    if cursor < len(bar_bass):
        out.append(PhraseSegment(
            source_start_beats=seg.source_start_beats + cursor * 4,
            source_end_beats=seg.source_end_beats,
            label="drop",
            color=COLOR_DROP,
            name="drop",
        ))
    return out


def _refine_first_drop_start(segments: list[PhraseSegment],
                              track_features) -> list[PhraseSegment]:
    """V8 visual pass fix A: push the FIRST drop's start forward if its
    opening 4-8 bars are visibly quieter than its sustained body.

    Strategy: compare drop bars [0:4] mean to drop bars [8:12] mean. If
    opening is < 92% of body, push by 8 bars (Savana case). The bars[8:12]
    sample avoids contamination from fills inside the drop body.
    """
    if not segments:
        return segments
    drop_idx = next((i for i, s in enumerate(segments) if s.label == "drop"), None)
    if drop_idx is None:
        return segments
    drop = segments[drop_idx]
    duration_bars = (drop.source_end_beats - drop.source_start_beats) / 4
    if duration_bars < 16:  # Need at least 16 bars to sample body
        return segments

    bar_rms = _feature_per_bar(track_features, drop.source_start_beats,
                                drop.source_end_beats, "rms")
    if len(bar_rms) < 16:
        return segments

    # Opening 4 bars vs body sample at bars 8-12 (past initial bars, before
    # most fills which tend to be at the END of phrases not in middle).
    open_mean = sum(bar_rms[:4]) / 4
    body_mean = sum(bar_rms[8:12]) / 4
    if body_mean <= 0:
        return segments

    ratio = open_mean / body_mean
    if ratio >= 0.92:
        return segments  # No push

    # Push by 8 if ratio in [0.85, 0.92), else 12 bars (steeper build)
    push_bars = 8 if ratio >= 0.85 else 12

    push_beats = push_bars * 4
    new_drop_start = drop.source_start_beats + push_beats
    # Don't push past the drop end
    if new_drop_start >= drop.source_end_beats - 16:
        return segments

    new_segments: list[PhraseSegment] = []
    for i, s in enumerate(segments):
        if i == drop_idx - 1:
            new_segments.append(PhraseSegment(
                source_start_beats=s.source_start_beats,
                source_end_beats=new_drop_start,
                label=s.label,
                color=s.color,
                name=s.name,
            ))
        elif i == drop_idx:
            new_segments.append(PhraseSegment(
                source_start_beats=new_drop_start,
                source_end_beats=s.source_end_beats,
                label=s.label,
                color=s.color,
                name=s.name,
            ))
        else:
            new_segments.append(s)
    return new_segments


def _collapse_fake_first_drop(segments: list[PhraseSegment],
                               track_features) -> list[PhraseSegment]:
    """V8 visual pass fix D: collapse a short first 'drop' (< 16 bars) that's
    followed by another break/fill back into the intro.

    Catches the pattern Marco Strous shows: intro(8) + drop(8) + break(16) +
    real_drop(16) — where the first 'drop' is just a sustained build moment
    that the algorithm mistook for a chorus. Sam's V4 LEARNINGS rule:
    'require a drop to sustain high bass for 16+ bars before counting it as
    a real drop'.

    Action: absorb the fake drop AND the following break/fill into the
    preceding intro, so the real drop is the first 'drop' segment.
    """
    if len(segments) < 3:
        return segments
    drop_idx = next((i for i, s in enumerate(segments) if s.label == "drop"), None)
    if drop_idx is None or drop_idx == 0:
        return segments

    drop = segments[drop_idx]
    drop_bars = (drop.source_end_beats - drop.source_start_beats) / 4
    if drop_bars >= 16:
        return segments  # First drop is real, no collapse

    # Must be followed by break/fill of ≥8 bars (a real build-zone, not an
    # intra-drop fill which is typically 1-4 bars). This avoids collapsing
    # drop-fill-drop patterns inside a real drop (Adam Ten, Crusy x Calussa).
    if drop_idx + 1 >= len(segments):
        return segments
    nxt = segments[drop_idx + 1]
    if nxt.label not in ("break", "fill"):
        return segments
    nxt_bars = (nxt.source_end_beats - nxt.source_start_beats) / 4
    if nxt_bars < 8:
        return segments  # Intra-drop fill, not a build-zone

    # And there must be another drop after that (the real one)
    if drop_idx + 2 >= len(segments):
        return segments
    if segments[drop_idx + 2].label != "drop":
        return segments

    # Collapse: previous intro extends to end of break/fill, drop+break/fill removed
    prev = segments[drop_idx - 1]
    new_intro_end = nxt.source_end_beats
    new_segments: list[PhraseSegment] = []
    for i, s in enumerate(segments):
        if i == drop_idx - 1:
            new_segments.append(PhraseSegment(
                source_start_beats=prev.source_start_beats,
                source_end_beats=new_intro_end,
                label=prev.label,
                color=prev.color,
                name=prev.name,
            ))
        elif i in (drop_idx, drop_idx + 1):
            continue  # absorbed
        else:
            new_segments.append(s)
    return new_segments


def _trim_short_breaks(segments: list[PhraseSegment],
                       track_features) -> list[PhraseSegment]:
    """V8 visual pass fix B: for 'break' segments 5-15 bars long, trim
    high-bass tail bars and absorb them into the next drop.

    Confirmed on Adam Ten: V8 had break@128-136 (8 bars) but bars 132-136
    have high bass — should be fill@128-132 + drop continuation.
    """
    if not segments:
        return segments

    result: list[PhraseSegment] = []
    i = 0
    while i < len(segments):
        s = s_curr = segments[i]
        bars = (s.source_end_beats - s.source_start_beats) / 4
        is_intermediate_break = (
            s.label == "break"
            and BREAK_TRIM_MIN_BARS <= bars <= BREAK_TRIM_MAX_BARS
            and i + 1 < len(segments)
            and segments[i + 1].label == "drop"
        )
        if not is_intermediate_break:
            result.append(s)
            i += 1
            continue

        next_drop = segments[i + 1]
        # Get bass profile of this break + first 4 bars of next drop
        break_bass = _bass_per_bar(track_features, s.source_start_beats,
                                    s.source_end_beats)
        next_drop_sample_end = min(next_drop.source_end_beats,
                                    next_drop.source_start_beats + 16)
        next_drop_bass = _bass_per_bar(track_features, next_drop.source_start_beats,
                                        next_drop_sample_end)
        if not break_bass or not next_drop_bass:
            result.append(s)
            i += 1
            continue

        next_drop_mean = sum(next_drop_bass) / len(next_drop_bass)
        if next_drop_mean <= 0:
            result.append(s)
            i += 1
            continue

        threshold = BREAK_TRIM_BASS_RATIO * next_drop_mean
        # Walk backwards from break end — count trailing bars at high bass
        trim_bars = 0
        for j in range(len(break_bass) - 1, -1, -1):
            if break_bass[j] >= threshold:
                trim_bars += 1
            else:
                break

        if trim_bars == 0:
            result.append(s)
            i += 1
            continue

        new_break_end = s.source_end_beats - trim_bars * 4
        new_break_bars = (new_break_end - s.source_start_beats) / 4

        # Emit (possibly relabelled) break
        if new_break_bars >= 1:
            new_label = "fill" if new_break_bars <= FILL_MAX_BARS_AS_FILL else "break"
            new_color = COLOR_FILL if new_label == "fill" else COLOR_BREAK
            result.append(PhraseSegment(
                source_start_beats=s.source_start_beats,
                source_end_beats=new_break_end,
                label=new_label,
                color=new_color,
                name=new_label,
            ))
        # Extend next drop backwards to absorb the trimmed bars
        absorbed_drop = PhraseSegment(
            source_start_beats=new_break_end,
            source_end_beats=next_drop.source_end_beats,
            label=next_drop.label,
            color=next_drop.color,
            name=next_drop.name,
        )
        result.append(absorbed_drop)
        i += 2  # consumed both break and next drop

    return result


def _refine_outro_start(segments: list[PhraseSegment],
                        track_features) -> list[PhraseSegment]:
    """V8 visual pass fix C: pull outro start back if the last 4 bars of
    the preceding drop are quieter than its body.

    Confirmed on Ease My Mind + Adam Ten: V8 outro starts 4 bars later
    than V7 because the last 4 bars of the drop have a tail-off that
    Sam considers part of the outro.
    """
    if len(segments) < 2:
        return segments
    outro_idx = next((i for i, s in enumerate(segments) if s.label == "outro"), None)
    if outro_idx is None or outro_idx == 0:
        return segments

    prev = segments[outro_idx - 1]
    if prev.label != "drop":
        return segments

    bars = (prev.source_end_beats - prev.source_start_beats) / 4
    if bars < OUTRO_REFINE_LOOKBACK_BARS * 2:
        return segments

    bar_bass = _bass_per_bar(track_features, prev.source_start_beats,
                              prev.source_end_beats)
    if len(bar_bass) < OUTRO_REFINE_LOOKBACK_BARS * 2:
        return segments

    # Body sample = middle of the drop
    body_start = max(0, len(bar_bass) // 2 - 4)
    body_end = min(len(bar_bass), len(bar_bass) // 2 + 4)
    body_bass = sum(bar_bass[body_start:body_end]) / (body_end - body_start)
    if body_bass <= 0:
        return segments

    threshold = OUTRO_REFINE_BASS_RATIO * body_bass
    tail = bar_bass[-OUTRO_REFINE_LOOKBACK_BARS:]
    if all(b < threshold for b in tail):
        # Last N bars are quiet — pull outro back
        pull_beats = OUTRO_REFINE_LOOKBACK_BARS * 4
        new_outro_start = segments[outro_idx].source_start_beats - pull_beats
        if new_outro_start <= prev.source_start_beats:
            return segments  # Would eat the whole drop, abort

        new_segments = list(segments)
        new_segments[outro_idx - 1] = PhraseSegment(
            source_start_beats=prev.source_start_beats,
            source_end_beats=new_outro_start,
            label=prev.label,
            color=prev.color,
            name=prev.name,
        )
        new_segments[outro_idx] = PhraseSegment(
            source_start_beats=new_outro_start,
            source_end_beats=segments[outro_idx].source_end_beats,
            label="outro",
            color=COLOR_OUTRO,
            name="outro",
        )
        return new_segments
    return segments


def _absorb_short_segments_before_outro(segments: list[PhraseSegment],
                                         track_features) -> list[PhraseSegment]:
    """Fix G (V14 visual pass) — if outro is preceded by short fills /
    short drops totalling ≤8 bars, absorb them into the outro.

    Catches Marco-style: drop_3 + fill_2(4b) + spurious drop_4(1b) + outro.
    The algorithm correctly identified the amplitude collapse (placed fill_2
    there) but labelled the post-collapse region as drop+outro instead of
    outro. This fix consolidates those short post-collapse segments into the
    outro.

    Does NOT trigger when the preceding segment is a real ≥8-bar drop, so
    tracks where the algorithm got the boundary right (Savana, Sapian,
    Capriati, etc.) are unaffected.
    """
    if len(segments) < 3:
        return segments
    outro_idx = next((i for i, s in enumerate(segments) if s.label == "outro"), None)
    if outro_idx is None or outro_idx < 2:
        return segments

    # Walk back from outro, accumulating only SHORT segments (≤4 bars each)
    consumed_idx = outro_idx
    total_consumed = 0.0
    while consumed_idx > 1:
        seg = segments[consumed_idx - 1]
        seg_bars = (seg.source_end_beats - seg.source_start_beats) / 4
        if seg_bars > 4:
            break  # Reached a real segment, stop
        if seg.label not in ("fill", "build", "drop", "break"):
            break
        consumed_idx -= 1
        total_consumed += seg_bars
        if total_consumed >= 8:
            break  # Don't absorb more than 8 bars total

    if consumed_idx == outro_idx or total_consumed < 1:
        return segments  # Nothing to absorb

    # The segment BEFORE consumed_idx must be a real drop/break that ends
    # where outro should start. Snap new outro start to that boundary.
    if consumed_idx == 0:
        return segments
    boundary_seg = segments[consumed_idx - 1]
    new_outro_start_beats = boundary_seg.source_end_beats

    # Build new segments
    new_segments = list(segments[:consumed_idx])
    outro_end = segments[outro_idx].source_end_beats
    new_segments.append(PhraseSegment(
        source_start_beats=new_outro_start_beats,
        source_end_beats=outro_end,
        label="outro",
        color=COLOR_OUTRO,
        name="outro",
    ))
    new_segments.extend(segments[outro_idx + 1:])
    return new_segments


def validate_bar_math(segments: list[PhraseSegment], track_name: str = "") -> list[str]:
    """Check each chop's delta-from-previous against nice multiples.

    Returns a list of warning strings. Each chop should land at a delta
    that's a multiple of 4 bars (with sub-bar slop for sub-bar Fills).
    Doesn't modify segments — just flags suspicious chops for review.
    """
    NICE = {4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 80, 96, 128}
    warnings: list[str] = []
    if len(segments) < 2:
        return warnings
    for i in range(1, len(segments)):
        prev_start = segments[i - 1].source_start_beats
        curr_start = segments[i].source_start_beats
        delta_bars = (curr_start - prev_start) / 4
        rounded = round(delta_bars)
        deviation = abs(delta_bars - rounded)
        # Sub-bar fills (deviation > 0.1 from integer) are intentional event markers
        if deviation > 0.1:
            continue
        if rounded not in NICE and rounded >= 2:
            warnings.append(
                f"{track_name} {segments[i].label}_{i} at bar {curr_start/4:.1f}: "
                f"delta {rounded}b from prev {segments[i-1].label} — not in nice grid"
            )
    return warnings


def refine_segments(segments: list[PhraseSegment], track_features) -> list[PhraseSegment]:
    """Apply build-zone, fill detection, and fill→break-by-length on top of
    coarse segments. Per-track. Uses per-beat features.

    Sam's V4 rules (2026-05-19):
      - Long intros split into intro + build (cyan)
      - Bass dips inside drops: 1-4 bars → fill (orange), 5+ → break (blue)
      - Any "fill" from the coarse pass that's 5+ bars → relabel as break
    """
    if not segments or not track_features or not track_features.beats:
        return segments

    refined: list[PhraseSegment] = []
    for seg in segments:
        if seg.label == "intro":
            refined.extend(_split_intro_build_zone(seg, track_features))
        elif seg.label == "drop":
            refined.extend(_split_drop_with_fills(seg, track_features))
        elif seg.label == "fill":
            # Length-based relabel: 5+ bars = break (not fill)
            bars = (seg.source_end_beats - seg.source_start_beats) / 4
            if bars > FILL_MAX_BARS_AS_FILL:
                refined.append(PhraseSegment(
                    source_start_beats=seg.source_start_beats,
                    source_end_beats=seg.source_end_beats,
                    label="break",
                    color=COLOR_BREAK,
                    name="break",
                ))
            else:
                refined.append(seg)
        else:
            refined.append(seg)

    # V8 visual-pass fixes (2026-05-20). Order matters:
    #   D. Collapse fake first drops (<16 bars then break) into intro
    #   A. Push first drop start forward if its opening is quieter than body
    #      (SKIPPED if D fired — D already absorbed the build into intro,
    #      pushing further would overshoot. Marco Strous case.)
    #   B. Trim sub-16-bar breaks whose tail is actually high-bass (fill+drop)
    #   C. Pull outro start back if last 4 bars of preceding drop are quiet
    #   G. Absorb short fill/drop sequences before outro (V14 — Marco pattern
    #      where the algorithm placed fill+1-bar-drop between the amplitude
    #      collapse and the outro label).
    before_d = len(refined)
    refined = _collapse_fake_first_drop(refined, track_features)
    if len(refined) == before_d:
        refined = _refine_first_drop_start(refined, track_features)
    refined = _trim_short_breaks(refined, track_features)
    refined = _refine_outro_start(refined, track_features)
    refined = _absorb_short_segments_before_outro(refined, track_features)

    # Renumber names per label
    counters: dict[str, int] = {}
    for seg in refined:
        counters[seg.label] = counters.get(seg.label, 0) + 1
        seg.name = f"{seg.label}_{counters[seg.label]}"

    return refined
