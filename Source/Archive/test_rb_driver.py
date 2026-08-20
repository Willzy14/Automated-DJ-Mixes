"""Smoke test for the Rekordbox desktop driver."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from automated_dj_mixes.desktop_analyzer import (
    analyze_folder_with_rekordbox, is_rekordbox_analyzed,
)


AUDIO_DIR = Path(
    "C:/Users/Carillon/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/"
    "Automated DJ Mixes/Test Project/Black Book x Defected V2/Audio"
)


def main():
    tracks = sorted(AUDIO_DIR.glob("*.wav"))
    print(f"Pre-run state ({len(tracks)} tracks):")
    for t in tracks:
        print(f"  {is_rekordbox_analyzed(t)}  {t.name}")

    print()
    analyze_folder_with_rekordbox(AUDIO_DIR, expected_tracks=tracks)

    print()
    print("Post-run state:")
    for t in tracks:
        print(f"  {is_rekordbox_analyzed(t)}  {t.name}")


if __name__ == "__main__":
    main()
