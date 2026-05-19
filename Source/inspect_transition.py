"""Inspect ONE transition from a real Sam mix — show every chop, gap, and
position so we can see the actual technique. Render as a labelled timeline.
"""

from __future__ import annotations

import sys
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sys.path.insert(0, str(Path(__file__).parent))
from analyze_real_mix import extract_arrangement


import sys as _sys

ALS = Path("G:/Mix CD' Projects/2015 -/Bargrooves Summer Sessions 2015 Mixes Project/Bargrooves Summer Sessions 2015 Mix 1  SW V1.als")

# CLI: python inspect_transition.py <out_substr> <in_substr> <out_filename>
OUTGOING_SUBSTR = _sys.argv[1] if len(_sys.argv) > 1 else "Coupe_De_Ville"
INCOMING_SUBSTR = _sys.argv[2] if len(_sys.argv) > 2 else "brame"
OUT = Path(f"Output/transition_inspect_{_sys.argv[3] if len(_sys.argv) > 3 else 'T1'}.png")


def main():
    tracks, clips = extract_arrangement(ALS)

    out_clips = [c for c in clips if OUTGOING_SUBSTR.lower() in c.file_ref.lower() or OUTGOING_SUBSTR.lower() in c.name.lower()]
    in_clips = [c for c in clips if INCOMING_SUBSTR.lower() in c.file_ref.lower() or INCOMING_SUBSTR.lower() in c.name.lower()]

    print(f"Outgoing clips: {len(out_clips)}")
    print(f"Incoming clips: {len(in_clips)}")

    out_start = min(c.time for c in out_clips)
    out_end = max(c.current_end for c in out_clips)
    in_start = min(c.time for c in in_clips)
    in_end = max(c.current_end for c in in_clips)

    overlap_start = max(out_start, in_start)
    overlap_end = min(out_end, in_end)
    overlap_beats = overlap_end - overlap_start

    print(f"Outgoing arrangement: [{out_start:.1f}..{out_end:.1f}] ({out_end - out_start:.1f} beats = {(out_end - out_start)/4:.1f} bars)")
    print(f"Incoming arrangement: [{in_start:.1f}..{in_end:.1f}] ({in_end - in_start:.1f} beats = {(in_end - in_start)/4:.1f} bars)")
    print(f"OVERLAP: [{overlap_start:.1f}..{overlap_end:.1f}] = {overlap_beats:.1f} beats = {overlap_beats / 4:.1f} bars")

    print(f"\n--- Outgoing clips in/near overlap ---")
    for c in sorted(out_clips, key=lambda c: c.time):
        if c.current_end >= overlap_start - 32 and c.time <= overlap_end + 32:
            dur_beats = c.current_end - c.time
            src_dur = c.loop_end - c.loop_start
            print(f"  arr[{c.time:8.2f}..{c.current_end:8.2f}] ({dur_beats:6.2f}b = {dur_beats/4:5.2f}bars) src[{c.loop_start:7.2f}..{c.loop_end:7.2f}]")

    print(f"\n--- Incoming clips in/near overlap ---")
    for c in sorted(in_clips, key=lambda c: c.time):
        if c.current_end >= overlap_start - 32 and c.time <= overlap_end + 32:
            dur_beats = c.current_end - c.time
            src_dur = c.loop_end - c.loop_start
            print(f"  arr[{c.time:8.2f}..{c.current_end:8.2f}] ({dur_beats:6.2f}b = {dur_beats/4:5.2f}bars) src[{c.loop_start:7.2f}..{c.loop_end:7.2f}]")

    # Visualize: clips as horizontal bars on two tracks
    fig, ax = plt.subplots(figsize=(20, 5))
    OUT_Y = 1
    IN_Y = 0
    cmap_out = plt.colormaps['Blues']
    cmap_in = plt.colormaps['Oranges']
    for i, c in enumerate(sorted(out_clips, key=lambda c: c.time)):
        col = cmap_out(0.4 + (i / max(1, len(out_clips))) * 0.5)
        ax.add_patch(mpatches.Rectangle((c.time, OUT_Y - 0.3), c.current_end - c.time, 0.6, facecolor=col, edgecolor='black', linewidth=0.5))
        ax.text(c.time, OUT_Y + 0.35, f"{c.loop_start:.0f}", fontsize=7, ha='left', va='bottom')
    for i, c in enumerate(sorted(in_clips, key=lambda c: c.time)):
        col = cmap_in(0.4 + (i / max(1, len(in_clips))) * 0.5)
        ax.add_patch(mpatches.Rectangle((c.time, IN_Y - 0.3), c.current_end - c.time, 0.6, facecolor=col, edgecolor='black', linewidth=0.5))
        ax.text(c.time, IN_Y - 0.45, f"{c.loop_start:.0f}", fontsize=7, ha='left', va='top')

    # Overlap region shaded
    ax.axvspan(overlap_start, overlap_end, ymin=0, ymax=1, alpha=0.1, color='red')

    ax.set_yticks([IN_Y, OUT_Y])
    ax.set_yticklabels([f"INCOMING: {INCOMING_SUBSTR}", f"OUTGOING: {OUTGOING_SUBSTR}"])
    ax.set_xlabel("Arrangement beats")
    ax.set_title(f"Transition 1 — {OUTGOING_SUBSTR} → {INCOMING_SUBSTR}\nOverlap: {overlap_beats:.0f} beats ({overlap_beats/4:.1f} bars)")

    # Bar grid
    bar_lines = [b for b in range(int(min(out_start, in_start)) // 16 * 16, int(max(out_end, in_end)) + 1, 16)]
    for b in bar_lines:
        ax.axvline(b, color='gray', alpha=0.15, linewidth=0.5)

    ax.set_xlim(min(out_start, in_start) - 16, max(out_end, in_end) + 16)
    ax.set_ylim(-1, 2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(OUT), dpi=110)
    print(f"\nSaved: {OUT}")


if __name__ == "__main__":
    main()
