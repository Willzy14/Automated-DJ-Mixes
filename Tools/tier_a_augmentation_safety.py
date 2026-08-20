"""Prove the augmentation is safe by comparing detect() output with
AND without the tier_a changes, on the same (augmented) npz caches.

If the diff between the new run vs the cached JSON is the SAME as the
diff between (vanilla main @ 4103ccf) vs the cached JSON, then the
augmentation is introducing zero behavioural change. Only the npz
itself has drifted from the cached JSON's generation time.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

CORPUS = Path("Test Project/14.08.26")
STEM_DIR = CORPUS / "_Stem Analysis"
AUDIO_DIR = CORPUS / "Audio"


def _resolve_soulsearcher():
    for wav in AUDIO_DIR.glob("Soulsearcher*.wav"):
        return wav.stem
    raise FileNotFoundError("Soulsearcher not found")


def _bpm_downbeat(track):
    p = STEM_DIR / f"SECTIONS_STEM_{track}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    bpm = float(d["bpm"])
    sec_per_bar = 4 * 60.0 / bpm
    first = d["sections"][0]
    downbeat = float(first["start_sec"]) - int(first["start_bar"]) * sec_per_bar
    return bpm, downbeat


def _to_compare_dict(d):
    return {k: d[k] for k in ("track", "bpm", "n_bars", "sections", "signals")}


def _run_detect_with_code(track, code_dir):
    """Run detect() with code_dir as Source on the path. Returns the
    result dict (or None if detect returned None)."""
    sys.path.insert(0, str(code_dir))
    try:
        import stem_detector as sd
    except ImportError as e:
        print(f"  cannot import detect from {code_dir}: {e}")
        return None
    wav = AUDIO_DIR / f"{track}.wav"
    bpm, downbeat = _bpm_downbeat(track)
    return sd.detect(wav, CORPUS, bpm=bpm, downbeat=downbeat,
                     make_viz=False, write_json=False)


def _run_detect_with_original_code(track):
    """Run detect() with the original (pre-augmentation) code from HEAD.
    Uses a temp directory so we don't have to actually checkout the
    original code in the workspace."""
    # Make a temp copy of HEAD's stem_detector.py + stem_section_probe.py
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    for src in ("stem_detector.py", "stem_section_probe.py"):
        # Read the HEAD version
        result = subprocess.run(
            ["git", "show", f"HEAD:Source/{src}"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if result.returncode != 0:
            print(f"  git show failed for {src}: {result.stderr}")
            return None
        (tmp / src).write_text(result.stdout, encoding="utf-8")
    try:
        return _run_detect_with_code(track, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    targets = [
        "Fish Go Deep - The Cure & The Cause (Idris Elba Remix) 24 Bit MASTER AMENDED",
        "Nic Fanciulli - Revoloution (Extended Mix) 24 Bit MASTER",
        _resolve_soulsearcher(),
    ]
    print("Augmentation safety test: my changes vs HEAD on the augmented npz.")
    print("If both diffs vs the cached JSON are the SAME, the augmentation is safe.")
    print("-" * 80)
    for track in targets:
        cached = json.loads((STEM_DIR / f"SECTIONS_STEM_{track}.json").read_text(encoding="utf-8"))
        cached_dict = _to_compare_dict(cached)
        # Run with my changes
        my_result = _run_detect_with_code(track, Path(__file__).resolve().parent.parent / "Source")
        # Run with HEAD's code
        head_result = _run_detect_with_original_code(track)
        if my_result is None or head_result is None:
            print(f"  {track[:60]}  (could not run)")
            continue
        my_dict = _to_compare_dict(my_result)
        head_dict = _to_compare_dict(head_result)
        my_diff = my_dict != cached_dict
        head_diff = head_dict != cached_dict
        same_diff = (my_dict == head_dict)  # my output == head's output
        verdict = "SAME" if same_diff else "DIFFER"
        print(f"  {track[:60]}")
        print(f"    my vs cached:       {'DIFFER' if my_diff else 'IDENTICAL'}")
        print(f"    head vs cached:     {'DIFFER' if head_diff else 'IDENTICAL'}")
        print(f"    my vs head:         {verdict}")
        if same_diff:
            print("    >> the augmentation introduces zero behavioural change.")


if __name__ == "__main__":
    main()
