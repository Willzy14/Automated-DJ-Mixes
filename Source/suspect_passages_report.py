"""Short, scannable list of suspect passages for Sam to listen to first.

Burn list D6: vocal regions already gate loop-source selection WITHIN a
track, but nothing compares two tracks' audible vocals ACROSS a
transition, and the entry-extension density signal is measured but never
surfaced anywhere a human would actually read it. Report-only - this never
gates or rejects anything the pipeline builds; it only tells Sam where to
listen first. A transition with nothing to flag is OMITTED entirely, not
listed as "clean" - a short list only stays worth reading if it stays
short.

Usage:
    python Source/suspect_passages_report.py <ARRANGEMENT_REPORT.json> [--out <path>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _mmss(beats: float, bpm: float) -> str:
    seconds = beats / (bpm * 4.0 / 60.0)
    m, s = divmod(max(0.0, seconds), 60.0)
    return f"{int(m)}:{s:04.1f}"


def _bpm_for(track_name: str, tracks_by_name: dict) -> float | None:
    t = tracks_by_name.get(track_name)
    return t.get("bpm") if t else None


def build_report_lines(report: dict) -> list[str]:
    """The report body as a list of lines, excluding the header. Split out
    from main() so tests can check content without touching the filesystem.
    """
    # `.get(key, [])` only substitutes the default when the key is ABSENT -
    # a key present with value null (legal JSON, e.g. a hand-edited report)
    # still returns None and crashes the loop below. `or []` catches both
    # shapes (MiniMax code review, 2026-09-15).
    tracks = report.get("tracks") or []
    transitions = report.get("transitions") or []
    tracks_by_name = {t["name"]: t for t in tracks}
    lines: list[str] = []

    density_values = [
        tr["density_score"] for tr in transitions
        if tr.get("density_status") == "measured" and tr.get("density_score") is not None
    ]
    if density_values:
        lines.append(
            "Entry-extension density ({} measured): min {:+.1f}dB, "
            "max {:+.1f}dB, median {:+.1f}dB".format(
                len(density_values), min(density_values), max(density_values),
                sorted(density_values)[len(density_values) // 2],
            )
        )
        lines.append("")

    flagged_any = False
    for tr in transitions:
        out_track = tr.get("out_track", "?")
        in_track = tr.get("in_track", "?")
        pair_lines: list[str] = []

        for clash in tr.get("vocal_clash_ranges") or []:
            bpm = _bpm_for(out_track, tracks_by_name) or _bpm_for(in_track, tracks_by_name)
            if bpm:
                out_s, out_e = clash["outgoing_range"]
                in_s, in_e = clash["incoming_range"]
                pair_lines.append(
                    "  vocal clash: {} vocal @{}-{} overlaps {} vocal @{}-{}".format(
                        out_track, _mmss(out_s, bpm), _mmss(out_e, bpm),
                        in_track, _mmss(in_s, bpm), _mmss(in_e, bpm),
                    )
                )
            else:
                pair_lines.append(
                    f"  vocal clash: {out_track} <-> {in_track} "
                    f"(beats {clash['clash_range'][0]:.0f}-{clash['clash_range'][1]:.0f}, "
                    f"no BPM on record to convert to mm:ss)"
                )

        if tr.get("density_status") == "measured" and tr.get("density_score") is not None:
            pair_lines.append(
                f"  entry-extension density: {tr['density_score']:+.1f}dB "
                f"vs this track's own average"
            )

        if pair_lines:
            flagged_any = True
            lines.append(f"{out_track} -> {in_track}:")
            lines.extend(pair_lines)
            lines.append("")

    if not flagged_any:
        lines.append("No suspect passages flagged this build.")

    return lines


def generate_suspect_passages_report(report: dict) -> str:
    header = [
        "# Suspect Passages",
        "",
        "Report-only - nothing here changed what got built. A quick list of",
        "passages worth a listen before trusting the mix blind: vocal clashes",
        "across a transition, and entry-extension windows measured as",
        "notably busy or quiet against that track's own average.",
        "",
    ]
    return "\n".join(header + build_report_lines(report))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_json", type=Path)
    parser.add_argument("--out", type=Path, default=None,
                        help="Output path (default: 'Suspect Passages.md' "
                             "next to the report JSON's own Output/ dir)")
    args = parser.parse_args()

    if not args.report_json.exists():
        print(f"ERROR: {args.report_json} not found")
        return 1

    try:
        report = json.loads(args.report_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse {args.report_json}: {exc}")
        return 1

    text = generate_suspect_passages_report(report)

    out_path = args.out or (args.report_json.parent / "Suspect Passages.md")
    out_path.write_text(text, encoding="utf-8")
    print(f"Saved: {out_path}")
    print()
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
