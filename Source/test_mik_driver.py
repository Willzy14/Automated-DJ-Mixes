"""Smoke test for the MIK desktop driver.
Picks 2 tracks from the V2 project and runs them through MIK.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from automated_dj_mixes.desktop_analyzer import analyze_with_mik, is_mik_analyzed


AUDIO_DIR = Path(
    "C:/Users/Carillon/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/"
    "Automated DJ Mixes/Test Project/Black Book x Defected V2/Audio"
)


def main():
    # Pick 2 tracks
    tracks = sorted(AUDIO_DIR.glob("*.wav"))[:2]
    print(f"Test tracks:")
    for t in tracks:
        analyzed = is_mik_analyzed(t)
        print(f"  {t.name} — already analyzed: {analyzed}")

    print()
    analyze_with_mik(tracks)

    print()
    print("Post-run state:")
    for t in tracks:
        print(f"  {t.name} — analyzed: {is_mik_analyzed(t)}")


if __name__ == "__main__":
    main()
