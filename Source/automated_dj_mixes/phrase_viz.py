"""Section clips for Ableton display.

PhraseSegment is the colour-coded clip record (intro green / build cyan /
break blue / drop yellow / fill orange / outro red) that als_generator lays
onto each track. segments_from_stem_sections() is the ONLY producer: it maps
stem_detector.detect() sections onto warp-beat coordinates through the beat
grid (the one-clock rule). The old Rekordbox per-beat interval/refinement
layer was removed 2026-08-18 (see Source/Archive/ and git history).
"""

from __future__ import annotations

from dataclasses import dataclass

# Ableton Live 12 color palette indices (18=green, 14=red confirmed by Sam in V2)
COLOR_INTRO = 18   # green - pure intro (kicks-only, no bass)
COLOR_BUILD = 23   # cyan - intro build-zone / teaser before the first real drop
COLOR_BREAK = 50   # blue - main break / long low-bass section in the body
COLOR_DROP  = 12   # yellow - sustained high-bass drop / chorus
COLOR_OUTRO = 14   # red - outro
COLOR_FILL  = 9    # orange - short drop in energy within the body (middle-8 / 1-4 bar fill)
COLOR_BEAT_DROPOUT = 55  # purple - short raw-kick gap inside a coarse section
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
class PhraseSegment:
    """A coloured visualization clip (one segment = N merged intervals)."""
    source_start_beats: float
    source_end_beats: float
    label: str          # intro / drop / break / outro / unknown
    color: int
    name: str


def segments_from_stem_sections(
    stem_result: dict,
    beat_times_ms: list[int] | None = None,
    first_downbeat_offset: int = 0,
) -> list[PhraseSegment]:
    """Convert a stem_detector.detect() result into PhraseSegment clips.

    The stem detector already labels sections (intro/drop/break/fill/outro) - the
    same label set this module colour-codes. This is the bridge that lets the
    stem detector replace the RB-phrase section source; no refine_segments pass
    is needed (the stem rules are already final).

    ONE-CLOCK RULE (2026-06-11 regression fix): when the track's beat grid is
    supplied, each section boundary is mapped from its detected TIME
    (start_sec/end_sec) onto the clip's warp-beat coordinate through the grid
    itself (sec_to_clip_beats - the same convention the warp markers use),
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
            # boundary in time, so they snap identically - but if bar
            # rounding ever collapses a 1-bar section, keep it >=1 bar and
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


def validate_bar_math(segments: list[PhraseSegment], track_name: str = "") -> list[str]:
    """Check each chop's delta-from-previous against nice multiples.

    Returns a list of warning strings. Each chop should land at a delta
    that's a multiple of 4 bars (with sub-bar slop for sub-bar Fills).
    Doesn't modify segments - just flags suspicious chops for review.
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
                f"delta {rounded}b from prev {segments[i-1].label} - not in nice grid"
            )
    return warnings
