"""Render the all-stems placement viz for a hand-picked list of corpus tracks (by name
substring) into a _REVIEW folder, so flagged failure modes can be eyeballed. Reuses the
cached stem envelopes; only the beat grid re-runs."""
import sys
from pathlib import Path

from automated_dj_mixes.stem_grid import detect_beat_grid
from automated_dj_mixes.warping import grid_bpm_and_downbeat
from stem_detector import detect as stem_detect
import section_placement_viz as spv

project = Path(sys.argv[1])
names = sys.argv[2].split(";")
out = project / "_REVIEW"
EXT = {".flac", ".wav", ".aiff", ".aif", ".mp3"}
allwav = [p for p in project.rglob("*") if p.suffix.lower() in EXT]
for sub in names:
    wav = next((p for p in allwav if sub.lower() in p.stem.lower()), None)
    if wav is None:
        print(f"[miss] {sub}")
        continue
    try:
        bg = detect_beat_grid(wav)
        g_bpm, g_db = grid_bpm_and_downbeat(bg.beat_times_ms, bg.first_downbeat_offset, bg.bpm)
        res = stem_detect(wav, project, bpm=g_bpm, downbeat=g_db, make_viz=False, write_json=False)
        o = spv.render(project, wav, res, out)
        seq = " ".join(f"{s['label']}{s['end_bar']-s['start_bar']}" for s in res["sections"])
        print(f"[ok] {wav.stem[:34]:<34} db={g_db:.2f} {bg.flag:4} | {seq}")
        print(f"      -> {o.name}")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[FAIL] {sub}: {e}")
