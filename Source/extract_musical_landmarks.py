"""Refresh Kick Detector V3 landmarks without changing certified sections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from automated_dj_mixes.musical_landmarks import extract_kick_dropout_landmarks


LANDMARK_SCHEMA_VERSION = "kick_dropout_landmarks_v1"


def _section_signature(sections: list[dict]) -> str:
    return json.dumps(
        sections, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _render(path: Path, result: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    from stem_section_probe import _seccol

    fig, ax = plt.subplots(figsize=(20, 2.5))
    for section in result["sections"]:
        start = float(section["start_bar"])
        end = float(section["end_bar"])
        ax.add_patch(Rectangle(
            (start, 0.15), end - start, 0.7,
            facecolor=_seccol(section["label"]), edgecolor="#555",
            linewidth=0.5, alpha=0.38,
        ))
        ax.text(
            (start + end) / 2.0, 0.5,
            f"{section['name']}\n{end - start:g} bars",
            ha="center", va="center", fontsize=7, fontweight="bold",
        )
    for landmark in result["signals"].get("musical_landmarks", []):
        start = float(landmark["start_bar"])
        end = float(landmark["end_bar"])
        colour = "#c2185b" if landmark["type"] == "pre_drop_kick_gap" else "#6a1b9a"
        ax.axvspan(start, end, 0.08, 0.92, color=colour, alpha=0.78)
        ax.text(
            (start + end) / 2.0, 0.94,
            f"{landmark['duration_beats']}b",
            ha="center", va="bottom", fontsize=6, color=colour,
            fontweight="bold",
        )
    ax.set_xlim(0, result["n_bars"])
    ax.set_ylim(0, 1.12)
    ax.set_yticks([])
    ax.set_xlabel("track bars (purple = kick dropout, pink = short pre-drop kick gap)")
    ax.set_title(
        f"{result['track']} - report-only musical landmarks "
        f"({len(result['signals'].get('musical_landmarks', []))})"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def refresh_track_landmarks(
    project: Path,
    track_name: str,
    *,
    provider=None,
    render: bool = True,
) -> dict:
    stem_path = project / "_Stem Analysis" / f"SECTIONS_STEM_{track_name}.json"
    wav_path = project / "Audio" / f"{track_name}.wav"
    if not stem_path.is_file():
        raise FileNotFoundError(f"Certified stem analysis not found: {stem_path}")
    if not wav_path.is_file():
        raise FileNotFoundError(f"Track WAV not found: {wav_path}")

    result = json.loads(stem_path.read_text(encoding="utf-8"))
    before = _section_signature(result["sections"])
    bpm = float(result["bpm"])
    seconds_per_bar = 240.0 / bpm
    first = result["sections"][0]
    downbeat = float(first["start_sec"]) - float(first["start_bar"]) * seconds_per_bar
    n_beats = int(result["n_bars"]) * 4

    if provider is None:
        from kick_model_adapter import get_provider
        provider = get_provider()
    readout = provider.presence_per_beat(
        wav_path, bpm=bpm, downbeat=downbeat, n_beats=n_beats
    )
    landmarks = extract_kick_dropout_landmarks(
        readout.raw,
        readout.section,
        result["sections"],
        bpm=bpm,
        downbeat=downbeat,
        source="kick-detector-v3-raw",
    )
    result.setdefault("signals", {})["musical_landmarks"] = landmarks
    result["signals"]["musical_landmark_schema"] = LANDMARK_SCHEMA_VERSION
    if _section_signature(result["sections"]) != before:
        raise RuntimeError(f"{track_name}: landmark refresh changed certified sections")

    stem_path.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    persisted = json.loads(stem_path.read_text(encoding="utf-8"))
    if _section_signature(persisted["sections"]) != before:
        raise RuntimeError(f"{track_name}: persisted sections changed during landmark refresh")
    if render:
        _render(
            project / "_Stem Analysis" / f"LANDMARKS_{track_name}.png",
            result,
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--track", action="append", required=True)
    args = parser.parse_args()

    for track_name in args.track:
        result = refresh_track_landmarks(args.project, track_name)
        landmarks = result["signals"]["musical_landmarks"]
        print(f"PASS: {track_name}: {len(landmarks)} landmarks; sections unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
