import sys,json
sys.path.insert(0,'Source'); sys.stdout.reconfigure(encoding='utf-8')
import align_engine as ae
from pathlib import Path
proj=Path("Test Project")/sys.argv[1]
want=sys.argv[2:]
for j in sorted((proj/"_Stem Analysis").glob("SECTIONS_STEM_*.json")):
    t=ae.load_track(j)
    if any(w.lower() in t.name.lower() for w in want):
        print("==",t.name,"| bars",t.n_bars,"| bpm",round(t.bpm,1),"| bass_in",t.bass_in_bar,"bass_out",t.bass_out_bar)
        print("  ", "  ".join(f"{s['name']}[{int(s['start_bar'])}-{int(s['end_bar'])}]" for s in t.sections))
        print("   loop_windows:",[(int(a),int(b)) for a,b in t.loop_windows][:8])
