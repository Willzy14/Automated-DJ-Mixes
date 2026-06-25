"""Corpus robustness harness — run the full detector stack (beat grid + downbeat +
sections + fills + outro) over a folder of tracks, flag anomalies per track, and print
ONE compact summary. Token-light by design: all analysis is done here in Python; only the
SUMMARY block at the end needs reading. Full per-track detail is written to a JSONL.

    set PYTHONPATH=Source
    python -m validate_corpus "<folder>" [N]
"""
import gc
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

from automated_dj_mixes.stem_grid import detect_beat_grid
from automated_dj_mixes.warping import grid_bpm_and_downbeat
from stem_detector import detect as stem_detect


def _free_gpu():
    """Release GPU/torch memory between tracks — under sustained batch load a rare
    method_descriptor type-confusion surfaces in band_onsets (C-level, not a logic bug;
    the track detects fine in isolation). Clearing the cache + gc between tracks avoids it."""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    gc.collect()


def analyze_track(wav, root):
    bg = detect_beat_grid(wav)
    db = round(bg.beat_times_ms[bg.first_downbeat_offset] / 1000, 2)
    rec = dict(track=wav.stem[:54], bpm=bg.bpm, db=db, off=bg.first_downbeat_offset,
               method=bg.downbeat_method, agree=bg.downbeat_agree, flag=bg.flag,
               gvk=bg.grid_vs_kick_ms, tsrc=bg.timing_src)
    g_bpm, g_db = grid_bpm_and_downbeat(bg.beat_times_ms, bg.first_downbeat_offset, bg.bpm)
    res = stem_detect(wav, root, bpm=g_bpm, downbeat=g_db, make_viz=False, write_json=False)
    secs = res["sections"] if res else []
    rec["secs"] = [(s["label"], s["end_bar"] - s["start_bar"]) for s in secs]
    rec["nfills"] = len(res["signals"]["fills"]) if res else 0
    rec["nbars"] = res["n_bars"] if res else 0
    fl = []
    if bg.flag:
        fl.append(bg.flag)
    if bg.grid_vs_kick_ms > 15:
        fl.append("BADWARP")
    if bg.downbeat_method in ("first-kick", "perc-intro"):
        fl.append(bg.downbeat_method)
    if secs:
        n = len(secs)
        ilen = secs[0]["end_bar"] - secs[0]["start_bar"]
        outro = next((s for s in secs if s["label"] == "outro"), None)
        olen = (outro["end_bar"] - outro["start_bar"]) if outro else None
        drops = [s["end_bar"] - s["start_bar"] for s in secs if s["label"] == "drop"]
        intros = [s for s in secs if s["label"] == "intro"]
        if secs[0]["label"] != "intro":
            fl.append("INTRO?")
        if len(intros) > 1:
            fl.append("DBLINTRO")
        if outro is None:
            fl.append("NOOUTRO")
        elif olen > 32:
            fl.append("BIGOUTRO")
        elif olen <= 1:
            fl.append("TINYOUTRO")
        if ilen > 64:
            fl.append("LONGINTRO")
        if drops and max(drops) > 80:
            fl.append("LONGDROP")
        if n < 3:
            fl.append("UNDERSEG")
        if n > 14:
            fl.append("OVERSEG")
        if any(s["label"] != "outro" and (s["end_bar"] - s["start_bar"]) % 4 for s in secs):
            fl.append("OFFGRID")
    else:
        fl.append("NOSECS")
    rec["flags"] = fl
    return rec

root = Path(sys.argv[1])
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10 ** 9
EXT = {".flac", ".wav", ".aiff", ".aif", ".mp3"}
audio = sorted(p for p in root.rglob("*") if p.suffix.lower() in EXT)[:limit]
out_path = root / "_validation_results.jsonl"
out_path.write_text("", encoding="utf-8")

records = []
t0 = time.time()
print(f"{len(audio)} tracks to analyze\n")
for i, wav in enumerate(audio):
    rec = None
    for attempt in (1, 2):                       # one retry — the rare crash is load-state, not logic
        try:
            rec = analyze_track(wav, root)
            break
        except Exception as e:
            rec = {"track": wav.stem[:54], "error": f"{type(e).__name__}: {str(e)[:90]}", "flags": ["ERROR"]}
            _free_gpu()
    records.append(rec)
    _free_gpu()
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"[{i+1}/{len(audio)}] {wav.stem[:40]:<40} {rec.get('bpm','?')!s:>6} "
          f"{rec.get('flag',''):>4} gvk={rec.get('gvk','?')!s:>5} {','.join(rec.get('flags',[]))}")

# ---------------- COMPACT SUMMARY (the only part worth reading) ----------------
ok = [r for r in records if "error" not in r]
gvk = [r["gvk"] for r in ok if "gvk" in r]
print("\n" + "=" * 72 + "\nSUMMARY\n" + "=" * 72)
print(f"processed {len(records)}  ok {len(ok)}  errors {len(records)-len(ok)}  time {time.time()-t0:.0f}s")
if gvk:
    print(f"grid_vs_kick ms: median {np.median(gvk):.1f}  mean {np.mean(gvk):.1f}  max {max(gvk):.1f}  >15ms {sum(g>15 for g in gvk)}")
print("downbeat methods:", dict(Counter(r.get("method", "?") for r in ok)))
print("BPM range:", f"{min((r['bpm'] for r in ok), default=0):.0f}-{max((r['bpm'] for r in ok), default=0):.0f}")
flagc = Counter(f for r in records for f in r.get("flags", []))
print("flag counts:", dict(flagc.most_common()))
print("\nflagged tracks by category (up to 14 each):")
for flag in ["ERROR", "NOSECS", "BADWARP", "JIT", "LOWC", "DB?", "perc-intro", "first-kick",
             "LONGINTRO", "BIGOUTRO", "TINYOUTRO", "NOOUTRO", "INTRO?", "DBLINTRO", "LONGDROP", "UNDERSEG", "OVERSEG"]:
    hits = [r["track"] for r in records if flag in r.get("flags", [])]
    if hits:
        print(f"  {flag:11} ({len(hits):>3}): " + " | ".join(h[:30] for h in hits[:14]))
print(f"\ndetail: {out_path}")
