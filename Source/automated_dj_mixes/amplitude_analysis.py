"""Amplitude-envelope structural detection.

Sam's method (2026-05): "Look at the picture broadly — where the waveform
is big vs small. The amplitude changes tell you where the drops, breaks,
and outro are, roughly. Then use the precise data to nail the beat."

This module computes the 1-second RMS envelope of an audio file and
locates the major structural transitions purely from amplitude. It's the
signal that's MISSING for MIK-only tracks — MIK cues are sparse and
sometimes skip the actual drop, so we can't rely on them alone.

Outputs are emitted as CueCandidates that drop into the same pipeline
as RB-derived and MIK-derived candidates. `first_drop_candidate` picks
the earliest credible bass_entry across all sources.

Key heuristics:
  - First drop: largest amplitude RISE inside the first 90 seconds.
  - First break: first significant amplitude DROP after the first drop.
  - Outro start: most recent significant amplitude DROP in the final
    90 seconds of the track.

Amplitude points snap to a nearby MIK cue (±4s) when one exists, else
to the nearest whole beat.
"""

from __future__ import annotations

import librosa
import numpy as np
from pathlib import Path

# Envelope construction
ENVELOPE_SR = 4000          # downsample for speed; plenty for structure
ENVELOPE_HOP_SEC = 1.0      # 1s frames for the broad-strokes view
SMOOTH_WINDOW_SEC = 4       # smoothing window — 1 bar at typical BPM

# Tier thresholds (fractions of per-track peak)
LOW_TIER = 0.30
HIGH_TIER = 0.55

# Drop detection
DROP_SEARCH_START_SEC = 8.0     # skip "music starts" jump — that's intro, not the drop
DROP_SEARCH_END_SEC = 90.0      # "first drop is within 90s" structural prior
DROP_MIN_RISE = 0.25            # min envelope rise (fraction of peak)
DROP_MIN_LEVEL_AFTER = 0.65     # require HIGH level after rise — intros reach ~0.5, drops reach ~0.7+

# Break detection
BREAK_MIN_DROP = 0.20           # min amplitude fall to count as a break
BREAK_SEARCH_WINDOW_SEC = 90.0  # look this far past the first drop

# Outro detection
OUTRO_SEARCH_BACK_SEC = 90.0    # last 90s of track
OUTRO_MIN_DROP = 0.20           # significant drop required
OUTRO_TAIL_EXCLUDE_SEC = 20.0   # ignore the final N seconds (pure fadeout)

# Snap-to-MIK tolerance
MIK_SNAP_TOLERANCE_SEC = 4.0

# Confidence levels
AMP_BASE_CONFIDENCE = 0.70
AMP_MIK_CORROBORATED_BONUS = 0.15  # snapped to a MIK cue → +0.15


def compute_envelope(audio_path: Path, sr_target: int = ENVELOPE_SR) -> tuple[np.ndarray, np.ndarray]:
    """Return (times_sec, normalised_envelope) using 1-second RMS frames."""
    y, sr = librosa.load(str(audio_path), sr=sr_target, mono=True)
    hop = int(sr * ENVELOPE_HOP_SEC)
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    peak = float(np.max(rms)) or 1.0
    env = rms / peak
    times = np.arange(len(env)) * ENVELOPE_HOP_SEC
    return times, env


def _smooth(env: np.ndarray, window: int = SMOOTH_WINDOW_SEC) -> np.ndarray:
    if window <= 1 or len(env) < window:
        return env
    kernel = np.ones(window) / window
    return np.convolve(env, kernel, mode="same")


def find_first_drop(env: np.ndarray, times: np.ndarray,
                    search_start_sec: float = DROP_SEARCH_START_SEC,
                    search_end_sec: float = DROP_SEARCH_END_SEC) -> tuple[float, float, float] | None:
    """Find the largest sustained amplitude RISE in `[search_start_sec, search_end_sec]`.

    Returns (time_sec, delta, level_after) or None.

    Algorithm: smooth the envelope, then for each frame inside the search
    window, compute (level_now - level_4s_ago). The frame with the biggest
    positive delta whose post-rise level exceeds DROP_MIN_LEVEL_AFTER wins.

    Why the search_start_sec: most extended-edit tracks have at least 4-8s
    of silence before the music kicks in. The "silence → first audible
    sound" jump is the intro starting, NOT the first drop. The drop is
    the next big rise (typically kick+bass coming in around 30-90s). We
    skip the very-start jump and only count rises that happen after the
    intro is established.
    """
    smoothed = _smooth(env)
    best_t: float | None = None
    best_delta = 0.0
    best_after = 0.0
    for i in range(SMOOTH_WINDOW_SEC, len(smoothed)):
        t = float(times[i])
        if t < search_start_sec:
            continue
        if t > search_end_sec:
            break
        delta = float(smoothed[i] - smoothed[i - SMOOTH_WINDOW_SEC])
        if delta >= DROP_MIN_RISE and smoothed[i] >= DROP_MIN_LEVEL_AFTER:
            if delta > best_delta:
                best_delta = delta
                best_t = t
                best_after = float(smoothed[i])
    if best_t is None:
        return None
    return best_t, best_delta, best_after


def find_first_break(env: np.ndarray, times: np.ndarray, after_sec: float,
                     window_sec: float = BREAK_SEARCH_WINDOW_SEC) -> tuple[float, float, float] | None:
    """Find the first significant amplitude DROP after `after_sec`.

    Returns (time_sec, drop_magnitude, level_after) or None.
    """
    smoothed = _smooth(env)
    search_lo = after_sec + SMOOTH_WINDOW_SEC * 2  # don't fire immediately after the drop
    search_hi = after_sec + window_sec
    for i in range(SMOOTH_WINDOW_SEC, len(smoothed)):
        t = float(times[i])
        if t < search_lo:
            continue
        if t > search_hi:
            break
        drop_amt = float(smoothed[i - SMOOTH_WINDOW_SEC] - smoothed[i])
        if drop_amt >= BREAK_MIN_DROP:
            return t, drop_amt, float(smoothed[i])
    return None


def find_outro_start(env: np.ndarray, times: np.ndarray,
                     duration_sec: float,
                     search_back_sec: float = OUTRO_SEARCH_BACK_SEC,
                     tail_exclude_sec: float = OUTRO_TAIL_EXCLUDE_SEC) -> tuple[float, float, float] | None:
    """Find the FIRST significant amplitude DROP in the outro search window.

    The window is `[duration - search_back_sec, duration - tail_exclude_sec]`.
    The tail exclusion matters: tracks that fade to silence produce a big
    amplitude drop right at the end — that's the fadeout, not the outro
    entry. Outro starts at the FIRST big drop (chorus → stripped outro),
    not the LAST one (fade → silence).

    Returns (time_sec, drop_magnitude, level_after) or None.
    """
    smoothed = _smooth(env)
    search_lo = max(0.0, duration_sec - search_back_sec)
    search_hi = max(search_lo, duration_sec - tail_exclude_sec)
    for i in range(SMOOTH_WINDOW_SEC, len(smoothed)):
        t = float(times[i])
        if t < search_lo:
            continue
        if t > search_hi:
            break
        drop_amt = float(smoothed[i - SMOOTH_WINDOW_SEC] - smoothed[i])
        if drop_amt >= OUTRO_MIN_DROP:
            return t, drop_amt, float(smoothed[i])
    return None


def find_clean_loop_window(audio_path: Path, search_start_sec: float,
                            search_end_sec: float, window_beats: int,
                            bpm: float) -> tuple[float, float] | None:
    """Find an `window_beats`-beat sustained-amplitude window inside the
    search range, avoiding any "dead air" (silence/decay).

    Sam's rule (2026-05): "from the finish of the track working backwards,
    take sixteen beats, then chop four beats from that sixteen beat point".
    We scan the window for sub-frames where amplitude drops below a silence
    threshold; if found, skip them and look earlier.

    Returns (clean_start_sec, clean_end_sec) or None if no clean window
    fits inside the range.
    """
    if bpm <= 0 or search_end_sec <= search_start_sec:
        return None
    sec_per_beat = 60.0 / bpm
    window_sec = window_beats * sec_per_beat
    if search_end_sec - search_start_sec < window_sec:
        return None

    y, sr = librosa.load(str(audio_path), sr=ENVELOPE_SR, mono=True,
                          offset=max(0.0, search_start_sec - 1.0),
                          duration=min(search_end_sec - search_start_sec + 2.0, 90.0))
    if len(y) == 0:
        return None
    peak = float(np.max(np.abs(y))) or 1.0
    yn = np.abs(y) / peak

    # 1-frame-per-100ms envelope
    hop = max(1, sr // 10)
    frames = []
    for i in range(0, len(yn) - hop, hop):
        frames.append(float(np.max(yn[i:i + hop])))
    if not frames:
        return None
    frame_sec = hop / sr
    silence_threshold = 0.10

    # Walk backwards from search_end through the available frames, looking
    # for the LAST sustained-amplitude window that's at least `window_sec`
    # long with NO frame below silence_threshold.
    offset_sec = max(0.0, search_start_sec - 1.0)
    window_frames = max(1, int(window_sec / frame_sec))
    n = len(frames)
    for start_idx in range(n - window_frames, -1, -1):
        window = frames[start_idx:start_idx + window_frames]
        if min(window) >= silence_threshold:
            clean_start_sec = offset_sec + start_idx * frame_sec
            clean_end_sec = clean_start_sec + window_sec
            # Only return if it falls inside the requested search range
            if clean_start_sec >= search_start_sec - 0.5 and clean_end_sec <= search_end_sec + 0.5:
                return float(clean_start_sec), float(clean_end_sec)
    return None


def snap_to_mik_or_beat(time_sec: float, bpm: float, first_downbeat_sec: float,
                        mik_cues_sec: list[float] | None = None,
                        tolerance_sec: float = MIK_SNAP_TOLERANCE_SEC) -> tuple[float, str]:
    """Pull an amplitude-derived time to the nearest MIK cue or whole beat.

    Returns (snapped_sec, snap_source). snap_source is one of
    "mik_cue", "beat", or "raw" (no snapping applied — only if bpm <= 0).
    """
    if bpm <= 0:
        return time_sec, "raw"

    if mik_cues_sec:
        # Closest MIK cue within tolerance wins
        closest = min(mik_cues_sec, key=lambda t: abs(t - time_sec))
        if abs(closest - time_sec) <= tolerance_sec:
            return float(closest), "mik_cue"

    # Snap to nearest whole beat
    sec_per_beat = 60.0 / bpm
    beats_since_downbeat = (time_sec - first_downbeat_sec) / sec_per_beat
    snapped_beat = round(beats_since_downbeat)
    snapped_sec = first_downbeat_sec + snapped_beat * sec_per_beat
    return float(snapped_sec), "beat"
