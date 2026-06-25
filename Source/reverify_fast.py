"""Fast section re-detect: reuse the (already-fixed) downbeat+bpm stored in each
SECTIONS_STEM json and re-run ONLY stem_detect (cached envelopes, no Demucs grid
pass). Seconds, not minutes — for iterating the section-label logic.

    set PYTHONPATH=Source
    python -m reverify_fast "Test Project/24.06.26"
"""
import json
import sys
from pathlib import Path

from stem_detector import detect as stem_detect

project = Path(sys.argv[1])
audio = project / "Audio"
for jp in sorted((project / "_Stem Analysis").glob("SECTIONS_STEM_*.json")):
    track = jp.stem[len("SECTIONS_STEM_"):]
    wav = audio / f"{track}.wav"
    if not wav.exists():
        continue
    d = json.loads(jp.read_text(encoding="utf-8"))
    bpm = d["bpm"]
    downbeat = d["sections"][0]["start_sec"]
    res = stem_detect(wav, project, bpm=bpm, downbeat=downbeat, make_viz=True, write_json=True)
    secs = res["sections"] if res else []
    summary = "  ".join(f"{s['label']}{s['end_bar'] - s['start_bar']}" for s in secs)
    print(f"{track[:34]:<34} | {summary}")
