"""Dump detect() output for a whole corpus with ALL flags at their defaults.

The Phase 2 byte-identical proof: run this once with main's Source on
PYTHONPATH and once with the branch's, against the SAME frozen corpus of
cached envelopes, then diff the two JSON files. Any byte of difference means a
default-flag behaviour change, which Phase 2 is not allowed to make.

Usage:
    ADJ_SOURCE_DIR=<Source dir> python Tools/sections_off_snapshot.py \
        "<corpus root>" "<out json>"
(ADJ_SOURCE_DIR defaults to this repo's own Source/.)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ADJ_SOURCE_DIR lets the byte-identical proof point one run at main's Source
# and the other at the branch's, unambiguously, from the same tool file.
sys.path.insert(0, os.environ.get(
    "ADJ_SOURCE_DIR",
    str(Path(__file__).resolve().parent.parent / "Source")))

import stem_detector as sd


def main():
    corpus = Path(sys.argv[1])
    out = Path(sys.argv[2])
    stem_dir = corpus / "_Stem Analysis"
    results = {}
    for meta_path in sorted(stem_dir.glob("SECTIONS_STEM_*.json")):
        track = meta_path.name[len("SECTIONS_STEM_"):-len(".json")]
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        bpm = float(meta["bpm"])
        sec_per_bar = 4 * 60.0 / bpm
        first = meta["sections"][0]
        downbeat = (float(first["start_sec"])
                    - int(first["start_bar"]) * sec_per_bar)
        wav = corpus / "Audio" / f"{track}.wav"   # cache hit: never read
        res = sd.detect(wav, corpus, bpm=bpm, downbeat=downbeat,
                        make_viz=False, write_json=False)
        results[track] = res
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"wrote {out} ({len(results)} tracks)")


if __name__ == "__main__":
    main()
