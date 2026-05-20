"""Diff V1 (Claude's section analysis) vs V2 (Sam's manual edits).

Shows per-track:
  - what Sam moved (and by how many bars)
  - what Sam added (fills/breaks I missed)
  - what Sam relabelled (e.g. my "drop_1" → his "break")
  - any text annotations Sam wrote

Output is a markdown report so we can see the diff pattern at a glance.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_label(name: str) -> str:
    """Map any clip name to a canonical label, ignoring index suffix and free text."""
    n = name.lower().strip()
    # Strip trailing _N
    parts = n.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        n = parts[0]
    # First word usually = label
    first = n.split()[0] if n else ""
    if "intro" in first: return "intro"
    if first in ("drop",): return "drop"
    if first in ("break",): return "break"
    if first in ("fill",): return "fill"
    if first in ("outro",): return "outro"
    if "pre outro" in n: return "pre_outro_marker"
    if "drop/outro" in n or "back end" in n.lower() or "could mix" in n.lower(): return "free_outro"
    return first or "unknown"


def summarize_track(clips: list, label_prefix: str) -> dict:
    """Compress consecutive same-label clips into segments."""
    out = []
    for c in clips:
        lbl = normalize_label(c["name"])
        start = c["arr_time"]
        end = c.get("arr_end") or c["arr_time"]
        src_start = c.get("source_start_beats")
        src_end = c.get("source_end_beats")
        name_text = c["name"]
        if out and out[-1]["label"] == lbl and abs(out[-1]["end"] - start) < 0.5:
            out[-1]["end"] = end
            out[-1]["src_end"] = src_end
            if name_text not in out[-1]["names"]:
                out[-1]["names"].append(name_text)
        else:
            out.append({
                "label": lbl, "start": start, "end": end,
                "src_start": src_start, "src_end": src_end,
                "names": [name_text],
            })
    return out


def fmt_bar(beats):
    return f"{beats / 4:.1f}" if beats is not None else "?"


def fmt_src(src_start, src_end):
    if src_start is None or src_end is None:
        return ""
    return f"src[{src_start:.0f}..{src_end:.0f}]"


def diff_track(track_name: str, v1_clips: list, v2_clips: list) -> str:
    """Return markdown diff for one track."""
    v1 = summarize_track(v1_clips, "v1")
    v2 = summarize_track(v2_clips, "v2")

    lines = [f"\n## {track_name}\n"]

    # Side-by-side table
    lines.append("| Left                                        | Right                                              |")
    lines.append("|---------------------------------------------|----------------------------------------------------|")
    rows = max(len(v1), len(v2))
    for i in range(rows):
        l = ""
        r = ""
        if i < len(v1):
            s = v1[i]
            l = f"bar {fmt_bar(s['start']):>6}..{fmt_bar(s['end']):>6}  {s['label']:<8} {fmt_src(s['src_start'], s['src_end'])}"
        if i < len(v2):
            s = v2[i]
            # Show Sam's clip name if it's not just the boring label_n form
            extra = ""
            for n in s["names"]:
                if not (n.lower().startswith(("intro_", "drop_", "break_", "fill_", "outro_"))
                        and n.split("_")[-1].isdigit()):
                    extra = f' "{n[:50]}"'
                    break
            r = f"bar {fmt_bar(s['start']):>6}..{fmt_bar(s['end']):>6}  {s['label']:<8}{extra}"
        lines.append(f"| {l:<43} | {r:<50} |")

    # Quick stats — section counts
    def counts(segs):
        d: dict[str, int] = {}
        for s in segs:
            d[s["label"]] = d.get(s["label"], 0) + 1
        return d
    c1 = counts(v1)
    c2 = counts(v2)
    all_labels = sorted(set(c1.keys()) | set(c2.keys()))
    diff_summary = []
    for k in all_labels:
        if c1.get(k, 0) != c2.get(k, 0):
            diff_summary.append(f"{k}: V1={c1.get(k, 0)} → V2={c2.get(k, 0)}")
    if diff_summary:
        lines.append(f"\n**Count changes:** {'; '.join(diff_summary)}")

    return "\n".join(lines)


def main():
    base = Path("Test Project/Black Book x Defected V2")
    # CLI: python diff_sections.py <left.json> <right.json> <out.md>
    if len(sys.argv) >= 4:
        v1 = load(Path(sys.argv[1]))
        v2 = load(Path(sys.argv[2]))
        out_md = Path(sys.argv[3])
        left_name = sys.argv[1]
        right_name = sys.argv[2]
    else:
        v1 = load(base / "Sections Review" / "V1_baseline.json")
        v2 = load(base / "Output" / "Sections Review" / "Sections_V2.json")
        out_md = base / "Sections Review" / "V1_vs_V2_diff.md"
        left_name = "V1 (Claude)"
        right_name = "V2 (Sam)"

    lines = [
        f"# Section analysis diff — {left_name} vs {right_name}",
        "",
        "Per-track comparison. Bar numbers are ARRANGEMENT bars (each = 4 beats).",
        "",
    ]
    for track_name in v1.keys():
        if not track_name or "Audio" in track_name:
            continue
        # V2 may have HTML-escaped apostrophe for Sapian — normalise
        candidates = [track_name, track_name.replace("'", "&apos;")]
        v2_clips = None
        for c in candidates:
            if c in v2:
                v2_clips = v2[c]
                break
        if v2_clips is None:
            lines.append(f"\n## {track_name}\n\n(no V2 entry found)")
            continue
        lines.append(diff_track(track_name, v1[track_name], v2_clips))

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_md}")
    print(f"\nPreview:")
    for line in lines[:60]:
        print(line)


if __name__ == "__main__":
    main()
