"""Diagnostic #3: detect kick flam in a rendered mix WAV.

Loads a segment of the render, isolates kicks (150Hz lowpass), folds onset
times onto the mix beat grid, and reports the phase distribution. One tight
cluster = locked. Two clusters = the two tracks' kicks are flamming; the
separation is the audible offset.

Usage:
    python Source/probe_render_flam.py <render.wav> <bpm> <label:start_beat:end_beat> [...]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def kick_onsets_segment(path: Path, t0: float, t1: float) -> np.ndarray:
    import librosa
    from scipy.signal import butter, sosfiltfilt
    y, sr = librosa.load(str(path), sr=22050, mono=True,
                         offset=t0, duration=t1 - t0)
    sos = butter(4, 150.0, btype="low", fs=sr, output="sos")
    y_low = sosfiltfilt(sos, y)
    env = librosa.onset.onset_strength(y=np.ascontiguousarray(y_low), sr=sr)
    on = librosa.onset.onset_detect(onset_envelope=env, sr=sr,
                                    units="time", backtrack=False)
    return on + t0


def main() -> int:
    render = Path(sys.argv[1])
    bpm = float(sys.argv[2])
    beat = 60.0 / bpm

    for spec in sys.argv[3:]:
        label, b0, b1 = spec.split(":")
        t0, t1 = float(b0) * beat, float(b1) * beat
        on = kick_onsets_segment(render, t0, t1)
        if len(on) < 20:
            print(f"{label}: only {len(on)} onsets — skip")
            continue
        # fold onto the beat grid (phase in ms relative to nearest beat)
        ph = ((on / beat + 0.5) % 1.0 - 0.5) * beat * 1000.0
        hist, edges = np.histogram(ph, bins=47, range=(-235, 235))
        # report the two largest peaks separated by >25ms
        order = np.argsort(hist)[::-1]
        centers = (edges[:-1] + edges[1:]) / 2
        p1 = centers[order[0]]
        p2 = None
        for o in order[1:]:
            if abs(centers[o] - p1) > 25 and hist[o] >= max(3, hist[order[0]] // 4):
                p2 = centers[o]
                break
        msg = f"{label}: {len(on)} kicks, main cluster {p1:+.0f}ms"
        if p2 is not None:
            msg += f", SECOND cluster {p2:+.0f}ms  ->  FLAM {abs(p2 - p1):.0f}ms"
        else:
            msg += ", no second cluster (locked)"
        print(msg)
        # compact histogram
        peak = hist.max() or 1
        bars = "".join(" .:-=+*#@"[min(8, int(8 * h / peak))] for h in hist)
        print(f"   [{edges[0]:+.0f}ms]{bars}[{edges[-1]:+.0f}ms]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
