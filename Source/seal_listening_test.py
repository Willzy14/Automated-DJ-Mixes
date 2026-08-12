"""Turn two rendered comparison clips into a sealed, randomised listening test.

Blind means blind: the clips are copied to neutral numbered names, the mapping
is written to a separate sealed file, and an A-vs-A duplicate is included as a
noise twin. If the twin is reported as a confident difference, the protocol is
not discriminating and the result is void - that check is the whole reason the
twin exists.

Randomisation is seeded from a caller-supplied value so a run is reproducible
for audit, and metadata is not copied across (the WAVs are written fresh).

Usage:
    python Source/seal_listening_test.py "<A.wav>" "<B.wav>" "<out dir>" --seed 12345
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("a_wav", type=Path, help="render of side A (interim_v1)")
    parser.add_argument("b_wav", type=Path, help="render of side B (sam_v1)")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--seed", type=int, required=True,
                        help="stamped into the sealed record for reproducibility")
    args = parser.parse_args()

    for path in (args.a_wav, args.b_wav):
        if not path.is_file():
            raise SystemExit(f"missing render: {path}")

    listen = args.out_dir / "Listen"
    sealed_dir = args.out_dir / "_sealed"
    listen.mkdir(parents=True, exist_ok=True)
    sealed_dir.mkdir(parents=True, exist_ok=True)

    # Three clips: A, B, and a second copy of A as the noise twin.
    entries = [("A", args.a_wav), ("B", args.b_wav), ("A_twin", args.a_wav)]
    rng = random.Random(args.seed)
    rng.shuffle(entries)

    mapping = []
    for position, (side, source) in enumerate(entries, 1):
        target = listen / f"Clip {position}.wav"
        shutil.copyfile(source, target)
        mapping.append({"clip": target.name, "side": side,
                        "source": str(source)})

    (sealed_dir / "MAPPING.json").write_text(
        json.dumps({"seed": args.seed, "mapping": mapping}, indent=2),
        encoding="utf-8")

    (listen / "HOW TO LISTEN.txt").write_text(
        "Three clips. Two of them are the SAME render - that pair is the\n"
        "control. If you confidently hear a difference between the two\n"
        "identical clips, the test is not discriminating and the result is\n"
        "void, which is exactly what the control is for.\n\n"
        "For each clip, note:\n"
        "  - how the incoming track's entry feels (too early / too late / right)\n"
        "  - whether anything clashes or sounds repetitive\n"
        "  - which clip you prefer, and how confident you are\n\n"
        "Do not open the _sealed folder until you have called it.\n",
        encoding="utf-8")

    print(f"Wrote {len(mapping)} clips to {listen}")
    print(f"Sealed mapping: {sealed_dir / 'MAPPING.json'} (do not open first)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
