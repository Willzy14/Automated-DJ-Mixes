"""Diagnostic #4: prove/measure the onset-detection lag that biased the
beatgrid gate's phase zero.

Grades grids two ways — envelope-peak onsets (the gate's current method,
backtrack=False) vs transient-foot onsets (backtrack=True) — on:
  (a) the ear-validated 22.05.26 V4 pool (perceptual truth: these sound right)
  (b) the 11.06.26 tracks, shipped ALS markers AND original RB grids
      (= shipped minus the override shift) for the three phase-overridden ones.

Expectation if the lag theory holds: with foot onsets the V4 pool centers
near 0, the overridden tracks' ORIGINAL grids center near 0, and the
shipped (shifted) grids measure ≈ -shift.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from probe_als_warp import beats_from_markers, clip_markers_by_source, load_als
from validate_beatgrid import _grade


def kick_onsets(audio_path: Path, backtrack: bool) -> np.ndarray | None:
    import librosa
    from scipy.signal import butter, sosfiltfilt
    try:
        y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    except Exception:
        return None
    sos = butter(4, 150.0, btype="low", fs=sr, output="sos")
    y_low = sosfiltfilt(sos, y)
    env = librosa.onset.onset_strength(y=np.ascontiguousarray(y_low), sr=sr)
    return librosa.onset.onset_detect(
        onset_envelope=env, sr=sr, units="time", backtrack=backtrack,
        energy=env)


# track substring -> phase override shift_ms applied in the shipped ALS
SHIFTS = {
    "Hold Me": 85.9,
    "Blackout": 97.4,
    "Bullerengue": -83.1,
}


def grade_both(wav: Path, beats: np.ndarray, label: str) -> None:
    period = float(np.median(np.diff(beats)))
    row = f"{label[:46]:<46}"
    for bt in (False, True):
        on = kick_onsets(wav, backtrack=bt)
        if on is None:
            row += "  load-fail"
            continue
        on = on[(on >= beats[0] - period) & (on <= beats[-1] + period)]
        if len(on) < 60:
            row += "  few-onsets"
            continue
        r, ph, _ = _grade(on, beats, period)
        row += f"  | R {r:.2f} ph {ph * period * 1000:>+7.1f}ms"
    print(row)


def pool(als_path: str, audio_dir: str, title: str,
         only: list[str] | None = None, unshift: bool = False) -> None:
    print(f"\n=== {title} ===")
    print(f"{'track':<46}  | peak (current gate)    | foot (backtracked)")
    root = load_als(Path(als_path))
    wavs = {p.name: p for p in Path(audio_dir).glob("*.wav")}
    for name, secs in sorted(clip_markers_by_source(root).items()):
        if only and not any(s.lower() in name.lower() for s in only):
            continue
        import html
        wav = wavs.get(name) or wavs.get(html.unescape(name))
        if wav is None or len(secs) < 64:
            continue
        beats = beats_from_markers(list(secs))
        shift = next((v for k, v in SHIFTS.items() if k.lower() in name.lower()), None)
        if unshift and shift is not None:
            grade_both(wav, beats - shift / 1000.0, f"{name[:34]} [ORIG RB GRID]")
            grade_both(wav, beats, f"{name[:34]} [shipped +{shift}ms]")
        else:
            grade_both(wav, beats, name)


def main() -> int:
    pool(r"Test Project\22.05.26 Mix\Output\Sections V4.als",
         r"Test Project\22.05.26 Mix\Audio",
         "EAR-VALIDATED GOOD POOL (22.05.26 V4)")
    pool(r"Test Project\Test Mix 11.06.26\Output\In-Key Mix V1.als",
         r"Test Project\Test Mix 11.06.26\Audio",
         "NEW MIX (11.06.26) — overridden tracks shown orig vs shipped",
         unshift=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
