"""Find the best master WAV for a credit across the work folders and the G:/F: backup drives, using Tools/folder_index.json (build it with build_folder_index.py). Prefers Extended/Original, newest SW V / AMENDED, never acapella/instrumental/dub. Usage: python Tools/resolve_masters.py [[\"Artist\",\"Title\",\"optional must-contain\",\"optional exact filename\"],...]. In a multi-track EP folder a file named exactly like the title (case-insensitive, no .wav, any leading track number dropped) comes first, then files whose name contains the title, then the usual version rank. A must hint matches the folder name or a filename; the exact filename (4th element, null to skip must) selects only the file with that name. Always COPY, never move; use shutil.copyfile (os copy fails on online-only Dropbox files)."""
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
def stem(f): return re.sub(r'\.wav$','',f,flags=re.I)
def strip_num(s): return re.sub(r'^\s*\d{1,3}(?:\s*[-._)]\s*|\s+)(?=\S)','',s)
def keys(s):
    s=stem(s); return {norm(s),norm(strip_num(s))}-{''}
def resolve(artist_key,title_key,must=None,exact=None):
    a=norm(artist_key); t=norm(title_key); nm=norm(stem(must)) if must else ''
    want_t={t,(a+' '+t).strip()}-{''}; want_m=keys(must) if must else set()
    sel=keys(exact) if exact else None; out=[]
    for name,p in idx:
        n=norm(name)
        if a in n and t in n and not name.endswith('.lnk'):
            mr=os.path.join(p,'MASTER RENDERS')
            try: fs=[f for f in os.listdir(mr) if f.lower().endswith('.wav') and os.path.isfile(os.path.join(mr,f))]
            except Exception: continue
            for f in fs:
                k=keys(f); kn=norm(stem(f))
                if sel is not None and not k&sel: continue
                if nm and nm not in n and nm not in kn: continue
                has_t=bool(t) and f' {t} ' in f' {kn} '
                out.append(((bool(k&want_t),bool(k&want_m),has_t,bool(nm) and nm in kn),rank(f),os.path.join(mr,f)))
    out.sort(reverse=True); return [(r,pth) for _,r,pth in out[:3]]
if __name__=='__main__':
    picks=json.loads(sys.argv[1])
    for a,t,*m in picks:
        r=resolve(a,t,m[0] if m else None,m[1] if len(m)>1 else None)
        print(f"{a} - {t}:")
        for s,p in r: print(f"   [{s:>4}] {p[:2]} ...{p[-105:]}")
        if not r: print("   NONE")
