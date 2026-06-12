"""Fit every track's beat grid from Ableton's .asd transient ticks and write
them as replace_grid overrides — the RB-less grid source.

BPM seeds come from the previous run's ARRANGEMENT_REPORT (grid-true values,
synced with the project via Dropbox); bar parity (downbeat anchor) comes from
the previous .als warp markers, minus any reverted phase shift, so the new
grids keep the bar alignment the DETECT pictures validated.

Usage:
    python Source/fit_grids_from_ticks.py "<project dir>" "<report.json>" "<prev.als>"
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import numpy as np

from asd_onsets import ableton_onsets_sec, grid_offset_vs_ticks
from probe_als_warp import beats_from_markers, clip_markers_by_source, load_als
from validate_beatgrid import _fit_grid_to_ticks, load_grid_overrides, overrides_path

# phase shifts that were baked into the previous .als but have since been
# reverted — subtract to recover the validated bar anchor
REVERTED_SHIFTS = {
    "Hold Me": 85.9,
    "Blackout": 97.4,
    "Bullerengue": -83.1,
}


def main() -> int:
    project = Path(sys.argv[1])
    report = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    als_root = load_als(Path(sys.argv[3]))
    audio_dir = project / "Audio"

    bpm_by_name = {html.unescape(t["name"]).lower(): float(t["bpm"])
                   for t in report["tracks"]}
    markers = {html.unescape(k): v
               for k, v in clip_markers_by_source(als_root).items()}
    overrides = load_grid_overrides(project)

    import soundfile as sf
    ok = True
    for wav in sorted(audio_dir.glob("*.wav")):
        ticks = ableton_onsets_sec(wav)
        if ticks is None or len(ticks) < 200:
            print(f"[FAIL] {wav.name}: no usable .asd ticks")
            ok = False
            continue
        stem = wav.stem.lower()
        bpm0 = next((b for n, b in bpm_by_name.items() if n == stem), None)
        if bpm0 is None:
            print(f"[FAIL] {wav.name}: no BPM seed in arrangement report")
            ok = False
            continue
        secs = markers.get(wav.name)
        if not secs:
            print(f"[FAIL] {wav.name}: no markers in previous .als for anchor")
            ok = False
            continue
        shift = next((v for k, v in REVERTED_SHIFTS.items()
                      if k.lower() in wav.name.lower()), 0.0)
        anchor0 = min(secs) - shift / 1000.0
        info = sf.info(str(wav))
        dur = info.frames / info.samplerate
        first, bpm, n, dboff, off, drift, hits = _fit_grid_to_ticks(
            ticks, bpm0, dur, anchor0)
        good = not np.isnan(off) and abs(off) <= 3.0 and hits >= 200
        if good and abs(drift) <= 10.0:
            verdict = "PASS"
        elif good and abs(drift) <= 20.0:
            # constant fit on a slightly wobbly track — still tighter than a
            # typical RB grid; flag for the listen instead of blocking
            verdict = "WARN"
        else:
            verdict = "FAIL"
        print(f"[{verdict}] {wav.name[:52]:<54} {bpm:>9.4f} BPM "
              f"first={first:.3f}s off={off:+.1f}ms drift={drift:+.1f}ms "
              f"hits={hits} dboff={dboff}")
        if verdict == "FAIL":
            ok = False
            continue
        overrides[wav.name] = {
            "replace_grid": {
                "bpm": round(bpm, 4),
                "first_ms": round(first * 1000.0, 1),
                "n_beats": int(n),
                "first_downbeat_offset": int(dboff),
            },
            "fit_offset_ms": round(off, 1),
            "fit_drift_ms": round(drift, 1),
            "tick_hits": int(hits),
            "phase_source": "ableton-ticks",
            "reason": "grid fitted to Ableton transient ticks (RB-less mode)",
        }

    p = overrides_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    print(f"\nwrote {p} ({len(overrides)} overrides)")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
