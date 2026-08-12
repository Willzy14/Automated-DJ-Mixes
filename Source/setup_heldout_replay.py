"""Set up the 12.08.26 held-out replay project.

Same pattern as setup_car_mix.py: find each chosen track in Stephanes Playlist,
convert FLAC -> WAV (the pipeline globs *.wav), write into a fresh project Audio
folder. Originals are never moved (CLAUDE.md hard rule) - we read + write a new WAV.

These 15 are verified held out: none appears in the 23.06.26, 24.06.26,
25.06.26 Car Mix or 16.07.26 Fresh Mix projects, so none informed the
Sam-Tweaks correction rules this replay is meant to test.
"""
from pathlib import Path

import soundfile as sf

SRC = Path("Test Project/Stephanes Playlist/TransferXL-08j7Fyj60NBmm0 (1)")
DST = Path("Test Project/12.08.26 Heldout Replay/Audio")
DST.mkdir(parents=True, exist_ok=True)

# Deep/soulful house, same family as the 16.07.26 Fresh Mix the rules came from.
# Each entry is a distinctive filename substring.
PICKS = [
    "Forevermore(Sebb Junior Remix)",
    "Ghetto Boy (extended mix)",
    "Living In Harmony (Sebb Junior Remix)",
    "It Is What It Is (Richard Earnshaw Remix)",
    "High Standards",
    "Hypnotised (Extended)",
    "Really Nice",
    "Love My Baby (Deep Mix)",
    "Not That Kind Of Girl (Original Mix)",
    "Free Falling",
    "Real Love (Mallin's 'Sweet Touch' Remix)",
    "Got 2 Say",
    "Heat (feat. Nathan Thomas)",
    "Fools (Extended Mix)",
    "Let You Go - DJOKO",
]

allflac = list(SRC.rglob("*.flac"))
written = 0
for i, sub in enumerate(PICKS, 1):
    src = next((p for p in allflac if sub.lower() in p.stem.lower()), None)
    if src is None:
        print(f"[MISS] {sub}")
        continue
    out = DST / f"{src.stem}.wav"
    if out.exists():
        print(f"[skip] {out.name[:54]}")
        continue
    data, sr = sf.read(str(src))
    sf.write(str(out), data, sr, subtype="PCM_24")
    written += 1
    print(f"[{i:>2}] {out.name[:54]}")

print(f"\n-> {DST}  ({written} written, {len(PICKS)} picked)")
