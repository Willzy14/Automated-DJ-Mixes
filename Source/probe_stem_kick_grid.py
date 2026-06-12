"""Diagnostic: measure a track's TRUE kick positions (Demucs drum stem,
full resolution) against its shipped grid — the clean ruler for
percussion-dense tracks where full-mix onsets and Ableton ticks both smear
(La Trumpter: trumpet + congas bury the kick; R=0.28).

Reports:
  - kick-onset phase vs the grid (offset ms, concentration)
  - per-eighth-of-track offsets (drift / tempo error)
  - bass-stem onset bar-parity histogram (which beat of the bar the bass
    lands on — downbeat sanity)

Usage:
    python Source/probe_stem_kick_grid.py "<wav>" "<project dir>"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def stem_audio(wav_path: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """(drums_mono, bass_mono, sr) via in-memory Demucs htdemucs."""
    import torch
    from demucs.apply import apply_model
    sys.path.insert(0, str(Path(__file__).parent))
    from stem_section_probe import _device, _model

    data, sr = sf.read(str(wav_path), always_2d=True)
    wav = data.T.astype(np.float32)
    if wav.shape[0] == 1:
        wav = np.vstack([wav, wav])
    model = _model()
    t = torch.from_numpy(wav)
    ref = t.mean(0)
    t = (t - ref.mean()) / (ref.std() + 1e-8)
    print(f"  separating {wav_path.name} ({_device().upper()}, full-res)...")
    with torch.no_grad():
        out = apply_model(model, t[None], device=_device(), progress=False)[0]
    out = out * (ref.std() + 1e-8) + ref.mean()
    names = list(model.sources)
    drums = out[names.index("drums")].mean(0).cpu().numpy()
    bass = out[names.index("bass")].mean(0).cpu().numpy()
    return drums, bass, sr


def attack_onsets(y: np.ndarray, sr: int, lowpass_hz: float = 150.0,
                  min_gap_s: float = 0.25) -> np.ndarray:
    """Sample-accurate attack onsets on an ISOLATED stem: lowpass, envelope,
    pick peaks, then backtrack each to the 10%-of-peak rising edge."""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, lowpass_hz, btype="low", fs=sr, output="sos")
    low = sosfiltfilt(sos, y)
    env = np.abs(low)
    win = max(1, int(sr * 0.005))
    k = np.ones(win) / win
    env = np.convolve(env, k, mode="same")
    thresh = 0.25 * np.percentile(env, 99)
    gap = int(min_gap_s * sr)
    onsets = []
    i = win
    while i < len(env) - 1:
        if env[i] >= thresh and env[i] > env[i - 1]:
            seg_end = min(len(env), i + gap)
            peak_i = i + int(np.argmax(env[i:seg_end]))
            peak = env[peak_i]
            j = peak_i
            floor = 0.1 * peak
            while j > max(0, peak_i - int(0.08 * sr)) and env[j] > floor:
                j -= 1
            onsets.append(j / sr)
            i = peak_i + gap
        else:
            i += 1
    return np.asarray(onsets)


def main() -> int:
    wav = Path(sys.argv[1])
    project = Path(sys.argv[2])
    ov = json.loads((project / "Hints" / "grid_overrides.json").read_text(
        encoding="utf-8"))[wav.name]["replace_grid"]
    bpm, first = float(ov["bpm"]), float(ov["first_ms"]) / 1000.0
    dboff = int(ov.get("first_downbeat_offset", 0))
    period = 60.0 / bpm

    drums, bass, sr = stem_audio(wav)
    kicks = attack_onsets(drums, sr)
    print(f"  {len(kicks)} kick attacks from the drum stem")

    # phase vs grid (signed, kick - nearest gridline, ms)
    k = np.round((kicks - first) / period)
    res = (kicks - (first + k * period)) * 1000.0
    m = np.abs(res) <= period * 1000 / 2
    res = res[m]
    print(f"\nKick-vs-grid: median {np.median(res):+.1f}ms, "
          f"IQR {np.percentile(res,25):+.1f}..{np.percentile(res,75):+.1f}ms, "
          f"n={len(res)}")
    for i, chunk in enumerate(np.array_split(res, 8)):
        if len(chunk):
            print(f"  segment {i+1}: median {np.median(chunk):+.1f}ms (n={len(chunk)})")

    # bass-onset bar parity: which beat of the bar does the bass attack on?
    bons = attack_onsets(bass, sr, lowpass_hz=200.0, min_gap_s=0.4)
    bb = np.round((bons - first) / period).astype(int)
    bres = (bons - (first + bb * period)) * 1000.0
    ok = np.abs(bres) <= 90
    beats_in_bar = ((bb[ok] - dboff) % 4)
    counts = np.bincount(beats_in_bar, minlength=4)
    print(f"\nBass attacks per beat-of-bar (downbeat offset {dboff}): "
          f"beat1={counts[0]} beat2={counts[1]} beat3={counts[2]} beat4={counts[3]}")
    print("  (a healthy parity has beat1 dominant)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
