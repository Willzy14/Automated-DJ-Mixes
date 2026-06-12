"""Diagnostic #2: arrangement-side warp integrity of a shipped .als.

Per clip: is it placed on the bar grid? Is its length whole beats? If looped,
is the loop region whole beats — and does the loop length divide the clip?
Fractional anything = the clip plays off-grid no matter how good the source
warp markers are. Compounded by loop repeats.

Usage:
    python Source/probe_als_arrangement.py "<path to .als>"
"""
from __future__ import annotations

import gzip
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def f(el, tag, default=None):
    n = el.find(tag)
    return float(n.get("Value")) if n is not None and n.get("Value") else default


def frac(x: float) -> float:
    """Distance to nearest integer."""
    return abs(x - round(x))


def main() -> int:
    als = Path(sys.argv[1])
    with gzip.open(als, "rb") as fh:
        root = ET.fromstring(fh.read())

    print(f"ALS: {als.name}")
    hdr = (f"{'clip':<34} {'start':>9} {'len':>8} {'bar?':>5} {'loopOn':>6} "
           f"{'loopLen':>8} {'reps':>6} {'fracMk':>6}")
    print(hdr)
    print("-" * len(hdr))

    bad = 0
    for track in root.iter("AudioTrack"):
        tname = ""
        for n in track.iter("EffectiveName"):
            tname = n.get("Value", "")
            break
        for clip in track.iter("AudioClip"):
            name_el = clip.find("Name")
            cname = name_el.get("Value", "?") if name_el is not None else "?"
            start = f(clip, "CurrentStart")
            end = f(clip, "CurrentEnd")
            if start is None or end is None:
                continue
            length = end - start
            loop = clip.find("Loop")
            loop_on = loop is not None and (loop.find("LoopOn") is not None
                       and loop.find("LoopOn").get("Value") == "true")
            lstart = f(loop, "LoopStart") if loop is not None else None
            lend = f(loop, "LoopEnd") if loop is not None else None
            llen = (lend - lstart) if (lstart is not None and lend is not None) else None
            reps = (length / llen) if (loop_on and llen) else None

            # warp marker BeatTimes: should be on integer clip-beats if the
            # chop was bar-snapped on the grid
            n_frac_mk = 0
            for wm in clip.iter("WarpMarker"):
                bt = wm.get("BeatTime")
                if bt is not None and frac(float(bt)) > 1e-6:
                    n_frac_mk += 1

            flags = []
            if frac(start) > 1e-6:
                flags.append("START-OFF-BEAT")
            elif round(start) % 4 != 0:
                flags.append("start-off-bar")
            if frac(length) > 1e-6:
                flags.append("FRACTIONAL-LEN")
            if loop_on and llen is not None and frac(llen) > 1e-6:
                flags.append("FRACTIONAL-LOOP")
            if loop_on and reps is not None and frac(reps) > 1e-3:
                flags.append("PARTIAL-REPEAT")

            interesting = flags or loop_on or n_frac_mk
            if not interesting:
                continue
            if flags:
                bad += 1
            print(f"{(tname + '/' + cname)[:34]:<34} {start:>9.3f} {length:>8.3f} "
                  f"{'y' if round(start) % 4 == 0 and frac(start) < 1e-6 else 'N':>5} "
                  f"{'on' if loop_on else '-':>6} "
                  f"{llen if llen is not None else 0:>8.3f} "
                  f"{reps if reps is not None else 0:>6.2f} "
                  f"{n_frac_mk:>6} {' '.join(flags)}")

    print(f"\n{bad} clips with integrity flags "
          f"(only looped/flagged/fractional-marker clips listed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
