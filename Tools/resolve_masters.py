"""Find the best master WAV for a credit across the work folders and the G:/F: backup drives, using Tools/folder_index.json (build it with build_folder_index.py). Prefers Extended/Original, newest SW V / AMENDED, never acapella/instrumental/dub/stems. Usage: python Tools/resolve_masters.py [[\"Artist\",\"Title\",\"optional must-contain\",\"optional exact filename\"],...] (2 to 4 elements, null skips one). Order: files that are not acapella/instrumental/dub/stems first, then the named track (the title as whole words in the name; a remix, rework, refix, bootleg or VIP is never the named track unless the title or the hint says so), then the must hint, then the mix (Extended, Original Mix, Radio Edit), then the version (the latest SW V / AMENDED wins), then a file named exactly like the title, then the newest date modified, then path descending. A must hint matches the folder name or a filename. The exact filename (4th element) selects only the file whose full name matches (case-insensitive, no .wav, no folder part); only when none does, and the name you typed has no track number of its own, it ignores a leading track number. Always COPY, never move; use shutil.copyfile (os copy fails on online-only Dropbox files).

The newest version always wins (Sam, 2026-11-05): a file whose name says only a version (MASTER AMENDED V3, SW V3, 24 Bit MASTER) is the newest version of the track when its folder holds one track, and it takes that track's best mix, so it beats an older titled file; a typo in the title is not a version-only name and still loses. In a folder of several tracks a version-only file cannot be placed, so it is not chosen, and the command line prints a NOTE line that names every newer one it passed over.
"""
import json,os,re,sys
sys.stdout.reconfigure(encoding='utf-8')
SP=os.path.dirname(__file__)
idx=json.load(open(os.path.join(SP,'folder_index.json')))
def norm(s): return re.sub(r'[^a-z0-9]+',' ',s.lower()).strip()
# Never acapella/instrumental/dub/stems: mix() (so rank()) takes 100 points off and resolve() sorts such a file after every other file.
JUNK=re.compile(r'acapella|instrumental|dub|stems|\.alp|dubstramental|vocal remix only')
# mix() is WHICH mix (Extended, Original, Radio Edit, junk); ver() is WHICH version. resolve() compares mix first and then ver, never
# their sum, so a mix bonus cannot outweigh a higher version inside one mix. rank() is the sum, shown as the score.
def mix(f):
    n=f.lower(); s=0
    if 'extended' in n: s+=40
    if 'original mix' in n or 'main original' in n: s+=30
    if re.search(r'radio|edit\)',n): s-=40
    if JUNK.search(n): s-=100
    return s
def ver(f):
    n=f.lower(); s=0
    m=re.search(r'sw v(\d+)',n); s+= int(m.group(1)) if m else 0
    if 'amended v' in n: s+=int(re.search(r'amended v(\d+)',n).group(1))+8
    elif 'amended' in n: s+=6
    if '24 bit' in n: s+=5
    return s
def rank(f): return mix(f)+ver(f)
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
# A version-only file (MASTER AMENDED V3, SW V3, 24 Bit MASTER, Final Master SW V2) names no track: nothing is left of its name after the
# version words, a leading track number and .wav. A word of its own (the artist, a title, a typo, Dub) makes it a named file.
VERS=re.compile(r'\b(?:sw v\d+|amended v\d+|amended|master|24 bit|final)\b')
def vonly(f):
    s=norm(strip_num(stem(f))); return bool(VERS.search(s)) and not VERS.sub(' ',s).split()
def variants(n): return {w for w in OTHER if f' {w} ' in f' {n} '}
def lit(s): return ' '.join(stem(re.split(r'[\\/]',s.strip())[-1].strip()).lower().split())
def scan(artist_key,title_key,must=None,exact=None):
    # Returns ([(score, path), ...] best 3, names of the newer version-only files that were passed over: only in a folder of several tracks).
    a=norm(artist_key); t=norm(title_key); nm=norm(stem(must)) if must else ''
    want_t={t,(a+' '+t).strip()}-{''}; want_m=keys(must) if must else set()
    asked=variants(t+' '+nm); asked_junk=set(JUNK.findall(t+' '+nm)); out=[]; held=[]
    for name,p in idx:
        n=norm(name)
        if a in n and t in n and not name.endswith('.lnk'):
            mr=os.path.join(p,'MASTER RENDERS')
            try: fs=[f for f in os.listdir(mr) if f.lower().endswith('.wav') and os.path.isfile(os.path.join(mr,f))]
            except Exception: continue
            rows=[(f,kn,bool(t) and f' {t} ' in f' {kn} ' and not variants(kn)-asked,bool(set(JUNK.findall(f.lower()))-asked_junk),vonly(f))
                  for f,kn in ((f,norm(stem(f))) for f in fs)]
            # One track: every file that names a track carries the title, and none is another variant (a remix is another track).
            # Then a version-only file is that track: it counts as named and takes the best mix of the titled files.
            body=[r for r in rows if not r[4]]
            one=all(r[2] for r in body) and len({frozenset(variants(r[1])) for r in body})<=1
            pref=max([mix(r[0]) for r in rows if r[2] and not r[3]] or [0])
            for f,kn,named,junk,vo in rows:
                if nm and nm not in n and nm not in kn: continue
                k=keys(f); fp=os.path.join(mr,f); up=vo and one
                try: mt=os.path.getmtime(fp)
                except OSError: mt=0.0
                out.append(((not junk,named or up,bool(k&want_m),bool(nm) and nm in kn),pref if up else mix(f),ver(f),bool(k&want_t),mt,fp))
                if vo and not one: held.append((ver(f),mt,fp))
    if exact:
        e=lit(exact); full=[o for o in out if lit(os.path.basename(o[-1]))==e]
        out=full or ([o for o in out if lit(strip_num(lit(os.path.basename(o[-1]))))==e] if strip_num(e)==e else [])
    out.sort(reverse=True); notes=[]
    if out and not exact:
        top=out[0]; notes=list(dict.fromkeys(os.path.basename(fp) for v,mt,fp in sorted(held,reverse=True) if fp!=top[-1] and (v,mt)>(top[2],top[4])))
    return [(o[1]+o[2],o[-1]) for o in out[:3]],notes
def resolve(artist_key,title_key,must=None,exact=None): return scan(artist_key,title_key,must,exact)[0]
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
        r,notes=scan(str(a),str(t),must,exact)
        print(f"{a} - {t}:")
        for s,p in r: print(f"   [{s:>4}] {p[:2]} ...{p[-105:]}")
        if not r: print("   NONE")
        if notes: print(f"   NOTE: newer, not chosen: {', '.join(notes)} ({'the name says' if len(notes)==1 else 'the names say'} only a version and the folder holds several tracks)")
