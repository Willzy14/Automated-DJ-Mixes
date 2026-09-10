"""Turn N rendered comparison clips into a sealed, randomised listening test.

Blind means blind: the clips are DECODED AND RE-WRITTEN to neutral numbered
names (not byte-copied - a byte copy carries across any WAV metadata chunks
the source file has: BWF/bext, LIST/INFO tags, anything a DAW stamped into
it, which can disclose which side a clip is), the mapping is written to a
separate sealed file, and one side is duplicated as an A-vs-A noise twin. If
the twin is reported as a confident difference, the protocol is not
discriminating and the result is void - that check is the whole reason the
twin exists.

Randomisation is seeded from a caller-supplied value so a run is reproducible
for audit.

N-way (2026-09-10): generalised from a hardcoded A/B pair, so a 3-way
candidate comparison (e.g. interim_v1 / sam_v1 / sam_v1+introloop) seals in
one pass instead of needing a second, separately-seeded A-vs-C run. Also
fixed the same round: the prior version's docstring claimed "metadata is not
copied across (the WAVs are written fresh)" while the code used
shutil.copyfile - a raw byte copy that carries every chunk of the source
file across. Found in Codex's review of the SAM_V2 candidate plan.

Usage:
    python Source/seal_listening_test.py \\
        --side A=<interim_v1.wav> --side B=<sam_v1.wav> --side C=<sam_v2.wav> \\
        --twin-of A --out-dir "<out dir>" --seed 12345
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def _parse_side(spec: str) -> tuple[str, Path]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError(
            f"--side must be LABEL=path, got: {spec!r}")
    label, _, path = spec.partition("=")
    label = label.strip()
    if not label:
        raise argparse.ArgumentTypeError(f"--side has an empty label: {spec!r}")
    return label, Path(path)


def _write_clean_copy(source: Path, target: Path) -> None:
    """Decode and re-write the audio only - no chunk of the source file's
    metadata survives. Preserves sample rate, channel count and bit depth
    (the file's own `subtype`), since those are audible/technical, not
    identifying."""
    import soundfile as sf

    info = sf.info(str(source))
    data, sr = sf.read(str(source), always_2d=True)
    sf.write(str(target), data, sr, subtype=info.subtype, format="WAV")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--side", action="append", type=_parse_side,
                        dest="sides", required=True,
                        help="LABEL=path, repeatable, at least 2 required")
    parser.add_argument("--twin-of", required=True,
                        help="label of the side to duplicate as the noise-twin control")
    parser.add_argument("--out-dir", type=Path, required=True, dest="out_dir")
    parser.add_argument("--seed", type=int, required=True,
                        help="stamped into the sealed record for reproducibility")
    args = parser.parse_args()

    if len(args.sides) < 2:
        raise SystemExit(f"need at least 2 --side entries, got {len(args.sides)}")
    labels = [label for label, _ in args.sides]
    if len(set(labels)) != len(labels):
        raise SystemExit(f"--side labels must be unique, got: {labels}")
    by_label = dict(args.sides)
    if args.twin_of not in by_label:
        raise SystemExit(
            f"--twin-of {args.twin_of!r} is not one of the --side labels: {labels}")

    for label, path in args.sides:
        if not path.is_file():
            raise SystemExit(f"missing render for side {label!r}: {path}")

    listen = args.out_dir / "Listen"
    sealed_dir = args.out_dir / "_sealed"
    listen.mkdir(parents=True, exist_ok=True)
    sealed_dir.mkdir(parents=True, exist_ok=True)

    entries = list(args.sides) + [(f"{args.twin_of}_twin", by_label[args.twin_of])]
    rng = random.Random(args.seed)
    rng.shuffle(entries)

    mapping = []
    for position, (side, source) in enumerate(entries, 1):
        target = listen / f"Clip {position}.wav"
        _write_clean_copy(source, target)
        mapping.append({"clip": target.name, "side": side,
                        "source": str(source)})

    (sealed_dir / "MAPPING.json").write_text(
        json.dumps({"seed": args.seed, "twin_of": args.twin_of,
                    "mapping": mapping}, indent=2),
        encoding="utf-8")

    (listen / "HOW TO LISTEN.txt").write_text(
        f"{len(entries)} clips. Two of them are the SAME render (the "
        f"{args.twin_of!r} side, duplicated) - that pair is the control. If "
        "you confidently hear a difference between the two identical clips, "
        "the test is not discriminating and the result is void, which is "
        "exactly what the control is for.\n\n"
        "For each clip, note:\n"
        "  - how the incoming track's entry feels (too early / too late / right)\n"
        "  - whether anything clashes or sounds repetitive\n"
        "  - which clip(s) you prefer, and how confident you are\n\n"
        "Do not open the _sealed folder until you have called it.\n",
        encoding="utf-8")

    print(f"Wrote {len(mapping)} clips to {listen}")
    print(f"Sealed mapping: {sealed_dir / 'MAPPING.json'} (do not open first)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
