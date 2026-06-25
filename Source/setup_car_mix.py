"""Set up the 25.06.26 car-test mix: find the 12 chosen tracks in Stephanes Playlist,
convert FLAC -> WAV (the pipeline globs *.wav), copy into a fresh project Audio folder.
Originals are never moved (CLAUDE.md hard rule) — we read + write a new WAV."""
import sys
from pathlib import Path

import soundfile as sf

SRC = Path("Test Project/Stephanes Playlist/TransferXL-08j7Fyj60NBmm0 (1)")
DST = Path("Test Project/25.06.26 Car Mix/Audio")
DST.mkdir(parents=True, exist_ok=True)

# BPM-ascending order; each entry is a distinctive filename substring.
PICKS = [
    "Cannot Let You Go", "Amin Bird", "Change My Mind", "Aight - Pablo",
    "Chaoss", "Clever - Cee", "Above (N.W.N", "Beautiful Mess",
    "Blues - N.W.N", "Always - N.W.N", "Do That Thang", "Back in the Days",
]
allflac = list(SRC.rglob("*.flac"))
for i, sub in enumerate(PICKS, 1):
    src = next((p for p in allflac if sub.lower() in p.stem.lower()), None)
    if src is None:
        print(f"[MISS] {sub}")
        continue
    out = DST / f"{src.stem}.wav"
    if out.exists():
        print(f"[skip] {out.name[:50]}")
        continue
    data, sr = sf.read(str(src))
    sf.write(str(out), data, sr, subtype="PCM_24")
    print(f"[{i:>2}] {out.name[:54]}")
print(f"\n-> {DST}")
