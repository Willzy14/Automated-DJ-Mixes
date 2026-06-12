"""Read Ableton's cached transient analysis (the clip-view ticks) from a
.wav.asd file. Sample-accurate, zero detector lag — the same positions Live
draws, so a grid aligned to these is visually aligned in Ableton.

The .asd is a serialized object tree; rather than decode the schema, locate
the OnSets Positions data directly: the longest monotonically-increasing
uint32 run with transient-plausible spacing.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def ableton_onsets_sec(wav_path: Path) -> np.ndarray | None:
    """Ableton transient positions (seconds) for wav_path, from its .asd."""
    import soundfile as sf
    asd = Path(str(wav_path) + ".asd")
    if not asd.exists():
        return None
    info = sf.info(str(wav_path))
    return _scan_onset_array(asd.read_bytes(), info.frames, info.samplerate)


def _scan_onset_array(data: bytes, n_samples: int, sr: float) -> np.ndarray | None:
    """Locate the OnSets Positions array in raw .asd bytes (pure, testable)."""
    best: np.ndarray | None = None
    for align in range(4):
        usable = (len(data) - align) // 4 * 4
        a = np.frombuffer(data[align:align + usable], dtype="<u4")
        ok = (a > 0) & (a < n_samples)
        i = 0
        while i < len(a):
            if not ok[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(a) and ok[j + 1] and a[j + 1] > a[j]:
                j += 1
            run = a[i:j + 1]
            if len(run) >= 200:
                med = float(np.median(np.diff(run.astype(float))))
                span = float(run[-1] - run[0])
                # transient spacing: 40ms..2s; must cover most of the file
                if (0.04 * sr) < med < (2.0 * sr) and span > 0.5 * n_samples:
                    if best is None or len(run) > len(best):
                        best = run.copy()
            i = j + 1
    if best is None:
        return None
    return best.astype(float) / sr


def grid_offset_vs_ticks(beats_sec: np.ndarray, ticks_sec: np.ndarray,
                         window_ms: float = 60.0
                         ) -> tuple[float, int, list[float]]:
    """Median signed offset (ms) from each beat gridline to Ableton's nearest
    tick (positive = tick AFTER gridline = grid early). Only beats with a
    tick within ±window_ms count. Also returns per-quartile medians (drift)."""
    idx = np.searchsorted(ticks_sec, beats_sec)
    idx = np.clip(idx, 1, len(ticks_sec) - 1)
    d_prev = beats_sec - ticks_sec[idx - 1]
    d_next = ticks_sec[idx] - beats_sec
    nearest = np.where(d_prev <= d_next, -d_prev, d_next) * 1000.0
    m = np.abs(nearest) <= window_ms
    hits = nearest[m]
    if len(hits) < 32:
        return float("nan"), len(hits), []
    quarts = [float(np.median(q)) for q in np.array_split(hits, 4)]
    return float(np.median(hits)), len(hits), quarts
