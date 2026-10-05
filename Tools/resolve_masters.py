"""Find the best master WAV for a credit across the work folders and the G:/F: backup drives, using Tools/folder_index.json (build it with build_folder_index.py). Prefers Extended/Original, newest SW V / AMENDED, never acapella/instrumental/dub. Usage: python Tools/resolve_masters.py [[\"Artist\",\"Title\",\"optional must-contain\"],...]. Always COPY, never move; use shutil.copyfile (os copy fails on online-only Dropbox files)."""
import json,os,re,sys
sys.stdout.reconfigure(encoding='utf-8')
SP=os.path.dirname(__file__)
idx=json.load(open(os.path.join(SP,'folder_index.json')))
def norm(s): return re.sub(r'[^a-z0-9]+',' ',s.lower()).strip()
def rank(f):
    n=f.lower(); s=0
    if 'extended' in n: s+=40
    if 'original mix' in n or 'main original' in n: s+=30
    if re.search(r'radio|edit\)',n): s-=40
    if re.search(r'acapella|instrumental|dub|stems|\.alp|dubstramental|vocal remix only',n): s-=100
    m=re.search(r'sw v(\d+)',n); s+= int(m.group(1)) if m else 0
    if 'amended v' in n: s+=int(re.search(r'amended v(\d+)',n).group(1))+8
    elif 'amended' in n: s+=6
    if '24 bit' in n: s+=5
    return s
def resolve(artist_key,title_key,must=None):
    a=norm(artist_key); t=norm(title_key); out=[]
    for name,p in idx:
        n=norm(name)
        if a in n and t in n and not name.endswith('.lnk') and (must is None or norm(must) in n):
            mr=os.path.join(p,'MASTER RENDERS')
            try: fs=[f for f in os.listdir(mr) if f.lower().endswith('.wav')]
            except Exception: continue
            for f in fs: out.append((rank(f),os.path.join(mr,f)))
    out.sort(reverse=True); return out[:3]
if __name__=='__main__':
    picks=json.loads(sys.argv[1])
    for a,t,*m in picks:
        r=resolve(a,t,m[0] if m else None)
        print(f"{a} - {t}:")
        for s,p in r: print(f"   [{s:>4}] {p[:2]} ...{p[-105:]}")
        if not r: print("   NONE")
