"""Re-run the section-detection layer (grid -> downbeat -> stem sections) for a
project, reproducing the orchestrator's stem path exactly, so a grid/detector
fix can be verified without the full desktop-analysis pipeline.

Overwrites SECTIONS_STEM_*.json + DETECT_*.png in _Stem Analysis.

    set PYTHONPATH=Source
    python -m reverify_sections "Test Project/24.06.26"
"""
import sys
from pathlib import Path

from automated_dj_mixes.stem_grid import detect_beat_grid
from automated_dj_mixes.warping import grid_bpm_and_downbeat
from stem_detector import detect as stem_detect

project = Path(sys.argv[1])
audio = project / "Audio"

print(f"{'track':<38} {'downbeat':>8} {'bpm':>7} {'flag':>4} {'secs':>4}  intro")
print("-" * 90)
for wav in sorted(audio.glob("*.wav")):
    try:
        bg = detect_beat_grid(wav)
    except Exception as e:
        print(f"{wav.stem[:38]:<38}  [grid fail] {type(e).__name__}: {e}")
        continue
    g_bpm, g_db = grid_bpm_and_downbeat(bg.beat_times_ms, bg.first_downbeat_offset, bg.bpm)
    try:
        res = stem_detect(wav, project, bpm=g_bpm, downbeat=g_db, make_viz=True, write_json=True)
    except Exception as e:
        print(f"{wav.stem[:38]:<38}  [detect fail] {type(e).__name__}: {e}")
        continue
    secs = res["sections"] if res else []
    intro = secs[0] if secs else {}
    print(f"{wav.stem[:38]:<38} {g_db:>7.2f}s {g_bpm:>7.2f} {bg.flag:>4} {len(secs):>4}  "
          f"[{intro.get('start_bar')}-{intro.get('end_bar')}b {intro.get('label')}]")
