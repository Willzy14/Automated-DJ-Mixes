"""Phrase visualization — group Rekordbox phrases into musical sections and
colour-code so Sam can SEE what the analysis identifies.

THREE-SIGNAL classification (per 8-bar interval):
  1. Rekordbox phrase majority   — what RB tagged the section as
  2. Energy level                — overall RMS + bass-band RMS via librosa
  3. Energy CHANGE at boundaries — bar-before vs bar-after to detect cue points

A section is labeled "drop" when bass + overall energy are high AND the RB
phrase data agrees (or one strongly suggests it). "break" when bass is low.
Intro/outro come from POSITION (before first drop / after last drop).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Ableton Live 12 color palette indices
# (18=green and 14=red confirmed; 12 and 50 are guesses for yellow/blue)
COLOR_INTRO = 18   # green
COLOR_BREAK = 50   # blue
COLOR_DROP  = 12   # yellow
COLOR_OUTRO = 14   # red
COLOR_UNKNOWN = 7  # neutral gray

# Legacy mapping for raw-phrase visualization (kept for reference)
LABEL_MAP = {
    "intro":  ("intro",  COLOR_INTRO),
    "up":     ("break",  COLOR_BREAK),
    "down":   ("break",  COLOR_BREAK),
    "chorus": ("drop",   COLOR_DROP),
    "outro":  ("outro",  COLOR_OUTRO),
}


@dataclass
class PhraseSegment:
    """One coloured clip segment representing a musical section."""
    source_start_beats: float
    source_end_beats: float
    label: str          # "intro" / "drop" / "break" / "outro" / "unknown"
    color: int          # Ableton color index
    name: str           # clip name shown in arrangement


def _simplify_rb_label(rb_label: str) -> str:
    """Map Rekordbox phrase label to Sam's musical category."""
    return {
        "intro": "intro",
        "up": "break",
        "down": "break",
        "chorus": "drop",
        "outro": "outro",
    }.get(rb_label, "unknown")


def compute_interval_energy(
    audio_path: Path,
    bpm: float,
    first_downbeat_sec: float,
    interval_beats: int,
    num_intervals: int,
) -> tuple[list[float], list[float]]:
    """Return (rms_per_interval, bass_per_interval) measured from the audio.

    RMS = overall energy. Bass = energy in the 40-180 Hz band (kick + bassline).
    Both are normalized to the track's max for use as 0-1 ratios.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(audio_path, sr=None, mono=True)

    hop = 512
    rms_frames = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]

    bass_mel = librosa.feature.melspectrogram(
        y=y, sr=sr, hop_length=hop, n_mels=8, fmin=40, fmax=180,
    )
    bass_frames = np.mean(bass_mel, axis=0)

    beat_dur = 60.0 / bpm
    interval_dur = interval_beats * beat_dur

    rms_per = []
    bass_per = []
    for i in range(num_intervals):
        t0 = first_downbeat_sec + i * interval_dur
        t1 = t0 + interval_dur
        f0 = max(0, int(t0 * sr / hop))
        f1 = min(len(rms_frames), int(t1 * sr / hop))
        if f1 <= f0:
            rms_per.append(0.0)
            bass_per.append(0.0)
            continue
        rms_per.append(float(np.mean(rms_frames[f0:f1])))
        bass_per.append(float(np.mean(bass_frames[f0:f1])))

    # Normalize to track max
    rms_max = max(rms_per) if rms_per else 1.0
    bass_max = max(bass_per) if bass_per else 1.0
    rms_norm = [r / rms_max for r in rms_per] if rms_max > 0 else rms_per
    bass_norm = [b / bass_max for b in bass_per] if bass_max > 0 else bass_per

    return rms_norm, bass_norm


def _classify_interval_combined(
    rb_label: str,
    rms_norm: float,
    bass_norm: float,
) -> str:
    """Combine the RB simplified label with energy levels for a final call.

    Rules (in priority order):
      - HIGH bass (>= 0.55) AND HIGH overall (>= 0.55)  → drop
      - LOW bass (< 0.35)                                → break
      - Otherwise: trust RB label (could be ambiguous middle ground)
    """
    if bass_norm >= 0.55 and rms_norm >= 0.55:
        return "drop"
    if bass_norm < 0.35:
        return "break"
    # Middle ground — defer to RB
    return rb_label


def group_phrases_into_sections(
    rb_analysis,
    total_beats: float,
    first_downbeat_offset: int = 0,
    interval_bars: int = 8,
    rms_norm: list[float] | None = None,
    bass_norm: list[float] | None = None,
) -> list[PhraseSegment]:
    """Group RB phrases into musical sections SNAPPED to an N-bar interval.

    Dance music structural changes land on 8-bar / 16-bar boundaries. Rekordbox
    sometimes tags micro-phrases at non-standard positions (5-bar, 11-bar,
    etc.) — those are usually sub-phrase markers, not real structural changes.

    Algorithm:
      1. Walk in N-bar intervals from the first downbeat.
      2. Classify each interval by the MAJORITY RB phrase label inside it.
      3. If energy data is provided, COMBINE RB label with bass + overall RMS:
         high bass+rms → drop; low bass → break; otherwise trust RB.
      4. Force pre-first-drop intervals to 'intro', post-last-drop to 'outro'.
      5. Merge consecutive same-type intervals into sections.

    Section boundaries are guaranteed to be on N-bar marks (relative to the
    first downbeat). The first section is extended back by `first_downbeat_offset`
    beats so any pre-downbeat audio is included.
    """
    if not rb_analysis or not rb_analysis.phrases:
        return [PhraseSegment(
            source_start_beats=0.0,
            source_end_beats=total_beats,
            label="unknown",
            color=COLOR_UNKNOWN,
            name="no_phrase_data",
        )]

    phrases = rb_analysis.phrases
    end_pssi = rb_analysis.end_beat
    interval_beats = interval_bars * 4
    first_downbeat_pssi = 1 + first_downbeat_offset

    # Build N-bar interval boundaries from the first downbeat onward
    boundaries: list[int] = [first_downbeat_pssi]
    pssi = first_downbeat_pssi + interval_beats
    while pssi < end_pssi:
        boundaries.append(pssi)
        pssi += interval_beats
    boundaries.append(end_pssi)

    # Pre-compute phrase end_beats once
    phrase_ends = [rb_analysis.phrase_end_beat(i) for i in range(len(phrases))]

    def classify_interval(pssi_start: int, pssi_end: int) -> str:
        """Return the dominant RB label by overlap beat count."""
        counts: dict[str, int] = {}
        for p, p_end in zip(phrases, phrase_ends):
            overlap = max(0, min(p_end, pssi_end) - max(p.start_beat, pssi_start))
            if overlap > 0:
                counts[p.label] = counts.get(p.label, 0) + overlap
        if not counts:
            return "unknown"
        return max(counts.keys(), key=lambda k: counts[k])

    raw_labels = [
        classify_interval(boundaries[i], boundaries[i + 1])
        for i in range(len(boundaries) - 1)
    ]
    rb_simple = [_simplify_rb_label(l) for l in raw_labels]

    # Step 1: RB defines the STRUCTURE — find first and last "drop" intervals
    # using ONLY Rekordbox phrase data. This is the active musical section.
    # Intervals before first_drop = intro, after last_drop = outro.
    first_drop = None
    last_drop = None
    for i, l in enumerate(rb_simple):
        if l == "drop":
            if first_drop is None:
                first_drop = i
            last_drop = i

    # Step 2: WITHIN the active section, use energy to refine drop vs break.
    # Outside the active section (intro/outro), trust the position label.
    if rms_norm is not None and bass_norm is not None and first_drop is not None:
        refined = list(rb_simple)
        for i in range(first_drop, last_drop + 1):
            rms_i = rms_norm[i] if i < len(rms_norm) else 0.0
            bass_i = bass_norm[i] if i < len(bass_norm) else 0.0
            refined[i] = _classify_interval_combined(rb_simple[i], rms_i, bass_i)
        rb_simple = refined

    # Step 3: Force final labels by position
    final_labels: list[str] = []
    for i, l in enumerate(rb_simple):
        if first_drop is None:
            final_labels.append(l)
        elif i < first_drop:
            final_labels.append("intro")
        elif i > last_drop:
            final_labels.append("outro")
        elif l in ("intro", "outro", "unknown"):
            # Anything mid-track that doesn't classify cleanly as drop/break:
            # treat as break (low-energy section within the active body)
            final_labels.append("break")
        else:
            final_labels.append(l)

    # Merge consecutive same-type intervals into sections
    label_to_color = {
        "intro": COLOR_INTRO,
        "break": COLOR_BREAK,
        "drop":  COLOR_DROP,
        "outro": COLOR_OUTRO,
        "unknown": COLOR_UNKNOWN,
    }
    sections: list[PhraseSegment] = []
    counters = {"intro": 0, "drop": 0, "break": 0, "outro": 0, "unknown": 0}

    i = 0
    while i < len(final_labels):
        j = i + 1
        while j < len(final_labels) and final_labels[j] == final_labels[i]:
            j += 1
        section_pssi_start = boundaries[i]
        section_pssi_end = boundaries[j]
        label = final_labels[i]
        counters[label] += 1
        source_start = float(section_pssi_start - 1 - first_downbeat_offset)
        source_end = float(section_pssi_end - 1 - first_downbeat_offset)
        # Extend the first section back to include any pre-downbeat audio
        if i == 0 and first_downbeat_offset > 0:
            source_start = -float(first_downbeat_offset)
        sections.append(PhraseSegment(
            source_start_beats=source_start,
            source_end_beats=source_end,
            label=label,
            color=label_to_color.get(label, COLOR_UNKNOWN),
            name=f"{label}_{counters[label]}",
        ))
        i = j

    return sections
