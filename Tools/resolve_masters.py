"""Find the best master WAV for a credit across the work folders and the G:/F: backup drives, using Tools/folder_index.json (build it with build_folder_index.py). Prefers Extended/Original, newest SW V / AMENDED, never acapella/instrumental/dub/stems. Usage: python Tools/resolve_masters.py [[\"Artist\",\"Title\",\"optional must-contain\",\"optional exact filename\"],...] (2 to 4 elements, null skips one). Order: files that are not acapella/instrumental/dub/stems first, then the named track (the title as whole words in the name; a remix, rework, refix, bootleg or VIP is never the named track unless the title or the hint says so), then the must hint, then the version rank (the latest SW V / AMENDED wins), then a file named exactly like the title, then path descending. A must hint matches the folder name or a filename. The exact filename (4th element) selects only the file whose full name matches (case-insensitive, no .wav, no folder part); only when none does, and the name you typed has no track number of its own, it ignores a leading track number. Always COPY, never move; use shutil.copyfile (os copy fails on online-only Dropbox files).

Known and left (a judgement call for Sam, not patched with a heuristic): when the newest version of the right track has a filename without the title words (MASTER AMENDED V3.wav, or a typo in the title), a file in the same folder that has the title words wins.
"""
import json,os,re,sys
sys.stdout.reconfigure(encoding='utf-8')
SP=os.path.dirname(__file__)
idx=json.load(open(os.path.join(SP,'folder_index.json')))
def norm(s): return re.sub(r'[^a-z0-9]+',' ',s.lower()).strip()
# Never acapella/instrumental/dub/stems: rank() takes 100 points off and resolve() sorts such a file after every other file.
JUNK=re.compile(r'acapella|instrumental|dub|stems|\.alp|dubstramental|vocal remix only')
def rank(f):
    n=f.lower(); s=0
    if 'extended' in n: s+=40
    if 'original mix' in n or 'main original' in n: s+=30
    if re.search(r'radio|edit\)',n): s-=40
    if JUNK.search(n): s-=100
    m=re.search(r'sw v(\d+)',n); s+= int(m.group(1)) if m else 0
    if 'amended v' in n: s+=int(re.search(r'amended v(\d+)',n).group(1))+8
    elif 'amended' in n: s+=6
    if '24 bit' in n: s+=5
    return s
# A remix and an original are different tracks (Sam, 2026-10-06): a file with one of these whole words or phrases is never "the
# named track" unless the title or the must hint says it. vip can be a word in a real title (VIP Lounge), so it is matched as a
# whole word only (never Viper) and a title or hint that contains it switches the check off for that word. An arbitrary "(Name Mix)"
# is not a marker: that would catch Extended Mix and Original Mix. The spaced forms are what V.I.P., Re-Work and Re-Fix become.
OTHER=('remix','rework','refix','bootleg','vip','v i p','re work','re fix','remode')
def stem(f): return re.sub(r'\.wav$','',f,flags=re.I)
# A leading track number is dropped only when a separator follows it (02 - X, 02. X, 02_X, 02) X) or it is zero-padded (02 X);
# a bare 7 Seconds or 100 Ways keeps its number, because that is part of the title.
def strip_num(s): return re.sub(r'^\s*(?:\d{1,3}\s*[-._)]\s*|0\d\s+)(?=\S)','',s)
def keys(s):
    s=stem(s); return {norm(s),norm(strip_num(s))}-{''}
def variants(n): return {w for w in OTHER if f' {w} ' in f' {n} '}
def lit(s): return ' '.join(stem(re.split(r'[\\/]',s.strip())[-1].strip()).lower().split())
def resolve(artist_key,title_key,must=None,exact=None):
    a=norm(artist_key); t=norm(title_key); nm=norm(stem(must)) if must else ''
    want_t={t,(a+' '+t).strip()}-{''}; want_m=keys(must) if must else set()
    asked=variants(t+' '+nm); asked_junk=set(JUNK.findall(t+' '+nm)); out=[]
    for name,p in idx:
        n=norm(name)
        if a in n and t in n and not name.endswith('.lnk'):
            mr=os.path.join(p,'MASTER RENDERS')
            try: fs=[f for f in os.listdir(mr) if f.lower().endswith('.wav') and os.path.isfile(os.path.join(mr,f))]
            except Exception: continue
            for f in fs:
                k=keys(f); kn=norm(stem(f))
                if nm and nm not in n and nm not in kn: continue
                named=bool(t) and f' {t} ' in f' {kn} ' and not variants(kn)-asked
                junk=bool(set(JUNK.findall(f.lower()))-asked_junk)
                out.append(((not junk,named,bool(k&want_m),bool(nm) and nm in kn),rank(f),bool(k&want_t),os.path.join(mr,f)))
    if exact:
        e=lit(exact); full=[o for o in out if lit(os.path.basename(o[3]))==e]
        out=full or ([o for o in out if lit(strip_num(lit(os.path.basename(o[3]))))==e] if strip_num(e)==e else [])
    out.sort(reverse=True); return [(r,pth) for _,r,_,pth in out[:3]]
if __name__=='__main__':
    def bad(msg): sys.exit('resolve_masters: '+msg)
    picks=json.loads(sys.argv[1])
    if not isinstance(picks,list): bad('the argument must be a JSON list of picks [artist, title, optional must, optional exact filename]')
    for p in picks:
        if not isinstance(p,list): bad(f'each pick must be a list [artist, title, optional must, optional exact filename], got {p!r}')
        if not 2<=len(p)<=4: bad(f'a pick needs 2 to 4 elements [artist, title, optional must, optional exact filename], got {len(p)}')
        if p[0] is None or p[1] is None: bad('artist and title must not be null')
        if len(p)==4 and p[3] is not None and not str(p[3]).strip(): bad('the exact filename (4th element) must not be empty; use null to skip it')
    for a,t,*m in picks:
        must=str(m[0]) if m and m[0] is not None else None; exact=str(m[1]) if len(m)>1 and m[1] is not None else None
        r=resolve(str(a),str(t),must,exact)
        print(f"{a} - {t}:")
        for s,p in r: print(f"   [{s:>4}] {p[:2]} ...{p[-105:]}")
        if not r: print("   NONE")
