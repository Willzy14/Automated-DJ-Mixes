import sys,json,re
from pathlib import Path
sys.path.insert(0,'Source'); sys.stdout.reconfigure(encoding='utf-8')
import align_engine as ae
from automated_dj_mixes.transition_policy import get_policy
proj=Path("Test Project")/sys.argv[1]
stem_dir=proj/"_Stem Analysis"
stems={}
for j in sorted(stem_dir.glob("SECTIONS_STEM_*.json")):
    t=ae.load_track(j); stems[t.name]=t
sec=json.load(open(proj/"Sections Review"/"Sections_V2.json",encoding='utf-8'))
order=[t if isinstance(t,str) else t.get('name') for t in (sec if isinstance(sec,list) else sec.get('tracks',sec))] if not isinstance(sec,dict) or 'tracks' in sec else list(sec.keys())
hints=json.load(open(proj/"Hints"/"track_hints.json",encoding='utf-8'))
import html
def key_for(nm):
    nm=html.unescape(nm); return ae._resolve_stem_key(nm,stems)
res=[]
for nm in order:
    k=key_for(nm); t=stems[k]
    h=next((v for kk,v in hints.items() if kk.startswith(nm[:30]) or html.unescape(nm)[:30] in kk),{})
    t.first_drop_bar=ae._sec_to_bar(h.get('first_drop_sec'),t.downbeat,t.spb)
    t.first_break_bar=ae._sec_to_bar(h.get('first_break_sec'),t.downbeat,t.spb)
    t.outro_start_bar=ae._sec_to_bar(h.get('outro_start_sec'),t.downbeat,t.spb)
    t.last_bass_drop_bar=ae._sec_to_bar(h.get('last_bass_drop_sec'),t.downbeat,t.spb)
    res.append(t)
pol=get_policy("interim_v1")
for n,(o,i) in enumerate(zip(res,res[1:]),1):
    try:
        al=ae.align_pair(o,i,pol); print(n,"OK  ",{k:getattr(al,k,None) for k in ("arr_offset_bars","handoff_bar_out","handoff_bar_in","overlap_bars")},o.name[:34],"->",i.name[:34],"| ovl",round(al.overlap_bars,1))
    except Exception as e:
        print(n,"FAIL",o.name[:34],"->",i.name[:34],"|",str(e)[:90])
