import json,re,sys
proj=sys.argv[1]
log=open(f"Test Project/{proj}/Output/hints_gate.log",encoding='utf-8').read()
hp=f"Test Project/{proj}/Hints/track_hints.json"
H=json.load(open(hp,encoding='utf-8'))
names=list(H.keys())
changed=[]
for line in log.splitlines():
    if '✗' not in line or not line.startswith('|'): continue
    c=[x.strip() for x in line.strip('|').split('|')]
    track,key,hs,hb,sb=c[0],c[1],c[2],c[3],c[4]
    if key=='last_bass_drop_sec': continue
    try: hs=float(hs); hb=float(hb); sb=float(sb)
    except ValueError: continue
    nm=[n for n in names if n.startswith(track.strip()[:30])]
    if len(nm)!=1: print("AMBIGUOUS",track,nm); continue
    spb=hs/hb; new=round(sb*spb,1)
    old=H[nm[0]][key]; H[nm[0]][key]=new
    H[nm[0]]["notes"]=(H[nm[0]].get("notes","")+f" | Corrected 2026-10-05: {key} {old}->{new} to the section boundary (bar {sb:g}); auto value sat on a drum-only/early move, checked vs DETECT picture.").strip(" |")
    changed.append((nm[0][:40],key,old,new))
json.dump(H,open(hp,'w',encoding='utf-8'),indent=2)
for x in changed: print(x)
