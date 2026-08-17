"""Find the musical end of an audio file.

Distinct from the file's end: trailing silence and reverb decay push the file's
end past the last moment of real musical content. The musical end is the
position a mix engineer would mark as "the last bar of music" -- the point
after which the file is silent or just reverb tail.

Why peak-relative: a kick dropping out is roughly a 9 dB step. Body-relative
thresholds stop there and report 15+ seconds of "tail" on tracks whose
kick-less drum outro IS music. Peak-relative looks for the energy CLIFF,
which on real endings is huge (-22 dB -> -62 dB inside one second). The peak
level is observed in the analysed window (the last 90 s of the file), not the
whole track, so the method is robust against an unexpectedly quiet master.
The peak being observed inside the window is what makes the 30 dB drop
insensitive to the exact number -- any threshold from -20 to -40 dB relative
to that peak returns the same answer on real endings.
"""

from __future__ import annotations

import numpy as np
import soundfile as sf


_TAIL_SEC = 90.0
_SILENCE_FLOOR_DBFS = -100.0


def musical_end_sec(path, *, drop_db: float = 30.0, frame_sec: float = 0.05) -> float:
    info = sf.info(str(path))
    sr = info.samplerate
    n = info.frames
    if sr <= 0 or n <= 0:
        return 0.0

    tail_frames = int(round(_TAIL_SEC * sr))
    start = max(0, n - tail_frames)

    with sf.SoundFile(str(path)) as f:
        f.seek(start)
        x = f.read(dtype="float32")

    if x.ndim > 1:
        x = x.mean(axis=1)
    if x.size == 0:
        return 0.0

    frame = max(1, int(round(frame_sec * sr)))
    n_frames = x.size // frame
    if n_frames == 0:
        n_frames = 1
        frame = x.size

    trimmed = x[: n_frames * frame].reshape(n_frames, frame)
    rms = np.sqrt(np.mean(trimmed * trimmed, axis=1) + 1e-12)
    db = 20.0 * np.log10(rms + 1e-12)

    peak = float(np.max(db))
    if not np.isfinite(peak) or peak < _SILENCE_FLOOR_DBFS:
        return 0.0

    above = db >= (peak - drop_db)
    if not np.any(above):
        return 0.0

    last = int(np.flatnonzero(above)[-1])
    end_in_window_frames = (last + 1) * frame
    return (start + end_in_window_frames) / sr


def musical_end_bars(path, bpm: float, downbeat_sec: float = 0.0, **kw) -> float:
    sec = musical_end_sec(path, **kw)
    sec_per_bar = 4.0 * 60.0 / bpm
    return (sec - downbeat_sec) / sec_per_bar
