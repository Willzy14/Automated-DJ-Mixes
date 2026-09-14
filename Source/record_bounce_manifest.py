"""Record a cryptographic binding between a manually bounced WAV and the
side (ALS + arrangement report) it was bounced from.

Codex review, 2026-09-14 (burn list A4, excerpt-extraction half, round 2):
`extract_transition_excerpts.py`'s `_sanity_check_wav_duration` only catches
a GROSSLY wrong `--side` file - it does nothing against the realistic
mistake of swapping two renders of similar length (exactly the likely case
for A/B/C comparison variants, which all come from the same held-out
tracks). "A simple post-bounce manifest of side label + WAV SHA-256 +
ALS/report hashes, generated at the handoff point and required on
extraction, is needed before treating this as an auditable sealed test."

This is that handoff-point step. Run it immediately after bouncing a side's
`Mix {side}.als` to a WAV, while you still know for certain which file you
just exported - that "still know for certain" moment is the only thing that
can ever make this binding trustworthy; nothing after it can recover that
certainty if it's lost. `extract_transition_excerpts.py` then verifies the
manifest (if one exists next to the ALS) before extracting from that side,
refusing on any mismatch - proving the WAV it was actually given still
matches the WAV that was hashed at bounce time, and that the ALS/report
haven't changed since. It cannot prove a human recorded the RIGHT file in
the first place - only that whatever was recorded hasn't silently drifted.

Usage (run once per side, right after bouncing that side):
    python Source/record_bounce_manifest.py \\
        --ab-root "<project>/Output/AB" --side A \\
        --wav "<project>/Output/Tech House Heldout Mix A (14.09.26).wav"
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path

_HASH_CHUNK_BYTES = 1024 * 1024  # stream in 1MB chunks - a whole-mix WAV
                                 # can be hundreds of MB; never load it whole.


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record(ab_root: Path, side: str, wav_path: Path) -> Path:
    side_dir = ab_root / side
    als_path = side_dir / f"Mix {side}.als"
    report_path = side_dir / f"Arranged {side}_ARRANGEMENT_REPORT.json"
    if not als_path.is_file():
        raise SystemExit(f"missing final ALS for side {side!r}: {als_path}")
    if not report_path.is_file():
        raise SystemExit(f"missing arrangement report for side {side!r}: {report_path}")
    if not wav_path.is_file():
        raise SystemExit(f"missing render: {wav_path}")

    manifest = {
        "side": side,
        "als_sha256": sha256_file(als_path),
        "report_sha256": sha256_file(report_path),
        "wav_sha256": sha256_file(wav_path),
        "wav_filename": wav_path.name,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc)
                       .isoformat(timespec="seconds"),
    }
    manifest_path = side_dir / f"Mix {side}.bounce_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ab-root", type=Path, required=True, dest="ab_root")
    parser.add_argument("--side", required=True)
    parser.add_argument("--wav", type=Path, required=True)
    args = parser.parse_args()

    manifest_path = record(args.ab_root, args.side, args.wav)
    print(f"Recorded bounce manifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
