"""Find the best master WAV for a credit across the work folders and the G:/F: backup drives, using Tools/folder_index.json (build it with build_folder_index.py). Prefers Extended/Original, newest SW V / AMENDED, never acapella/instrumental/dub/stems. Usage: python Tools/resolve_masters.py [[\"Artist\",\"Title\",\"optional must-contain\",\"optional exact filename\"],...] (2 to 4 elements, null skips one). Order: files that are not acapella/instrumental/dub/stems first, then the named track (the title as whole words in the name; a remix, rework, refix, bootleg or VIP is never the named track unless the title or the hint says so), then the must hint, then the mix (Extended, Original Mix, Radio Edit), then the version (the latest SW V / AMENDED wins), then a file named exactly like the title, then the newest date modified, then path descending. A must hint matches the folder name or a filename. The exact filename (4th element) selects only the file whose full name matches (case-insensitive, no .wav, no folder part); only when none does, and the name you typed has no track number of its own, it ignores a leading track number. Always COPY, never move; use shutil.copyfile (os copy fails on online-only Dropbox files).

The newest version always wins (Sam, 2026-11-05). For a DJ mix the Extended mix comes first, then the newest version of it; a newer plain version does not beat an older Extended. A file whose name says only a version (MASTER AMENDED V3, Artist - MASTER AMENDED V3, SW V3, 24 Bit MASTER, Extended SW V3) is a version of the searched track ONLY in an exact single-track folder: the folder is named exactly artist - title, and every file in it that is not junk is a titled file (its name, without the track number, the version tokens, the (..) groups of only mix words and the artist prefix, is exactly the title) or a version-only file, with at least one titled file; it then takes the folder's best mix, or keeps its own if its name has a mix word. Anywhere else it is never chosen, and the command line prints a NOTE with the whole path of every newer version-only file (or file with only versions and decorations in its name; in a folder named exactly artist - title also any other file with a version token in its name) that was not chosen, in every folder scanned, and says so when a must hint matched nothing. Known limits, older than this rule and Sam's to decide: the folder filter is a substring test; a titled file is matched as whole words, so Artist - Love Me Right can beat Artist - Love when searching Love; a remix-only folder still resolves when no original exists; a name with an SW number is decided by that number first and amended or 24 bit only break a tie at the same number (SW V10 < SW V11 AMENDED < SW V12 < SW V12 AMENDED), while a name with no SW number keeps its old score and is compared with an SW name by the old numbers (SW V12 beats MASTER AMENDED V3); resolve() returns only the ranked files, the NOTE is printed only by the command line. A NOTE compares a file with the best file of its own folder; a newer titled version that loses to an older Extended is not noted (the DJ rule); a title made only of version words (Master, Final) is never promoted, and its version-only files get a NOTE and are never the answer; when the only answers of a folder are junk and a version-only file of that folder was held there is no answer from it, only the NOTE. A hint or an exact filename that matches nothing is named on a NONE line: the hint first, never both.
"""
import json,os,re,sys
sys.stdout.reconfigure(encoding='utf-8')
SP=os.path.dirname(__file__)
with open(os.path.join(SP,'folder_index.json')) as _f: idx=json.load(_f)
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
    # An SW number decides first; amended (0.5), amended V<K> (K/100, K at most 49) and 24 bit (0.005) only break a tie at the same number, and the
    # tie-break is always below 1. A name with no SW number keeps the old score (AMENDED V<K> = K+8, AMENDED = 6, 24 bit +5).
    n=f.lower(); sw=re.search(r'sw v(\d+)',n); m=re.search(r'amended v(\d+)',n)
    if sw: return int(sw.group(1))+(0.5 if 'amended' in n else 0)+(min(int(m.group(1)),49)/100 if m else 0)+(0.005 if '24 bit' in n else 0)
    return (int(m.group(1))+8 if m else 6 if 'amended' in n else 0)+(5 if '24 bit' in n else 0)
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
def variants(n): return {w for w in OTHER if f' {w} ' in f' {n} '}
# VERS are the version tokens, VMIX the mix tokens (VMIX also decides "has a mix word"), DECO the words of a decoration group.
VERS=re.compile(r'\b(?:sw v\d+|amended v\d+|amended|master|24 bit|final)\b')
VMIX=re.compile(r'\b(?:extended|original|main|mix|radio|edit)\b')
DECO=re.compile(r'\b(?:extended|original|main|mix|radio|edit|club|vocal)\b')
# A file name without its decorations: the track number, (..) and [..] groups of only mix or version words (Extended Mix, Radio Edit, Club Mix;
# not Part 2, Reprise, Re-Edit, feat. X or a remix name), the artist once at the front and the version tokens.
def bare(f,a):
    s=norm(re.sub(r'\([^)]*\)|\[[^\]]*\]',lambda m:m.group() if DECO.sub(' ',VERS.sub(' ',norm(m.group()))).split() else ' ',strip_num(stem(f))))
    return ' '.join(VERS.sub(' ',s[len(a)+1:] if a and s.startswith(a+' ') else s).split())
# A decoration-only name (MASTER AMENDED V3, Artist - SW V3, (Extended Mix), Artist - Extended Mix) says nothing about a track: it does not hold the searched
# title as whole words (a file that does is a titled file, whatever else it holds), and nothing is left of it once bare() has taken out the decorations and the
# artist prefix and then the mix tokens. A version-only file is a decoration-only name with a version token. Neither is ever an answer unless it is promoted
# (a version-only file, in an exact folder); both are held for the NOTE.
def deco(f,a='',t=''): return f' {t} ' not in f' {norm(f)} ' and not VMIX.sub(' ',bare(f,a)).split()
def vonly(f,a='',t=''): return bool(VERS.search(norm(f))) and deco(f,a,t)
# A project folder name without its trailing [Label] Project (or a standalone Project, never a word that ends in project) and a leading track number (01 - Artist, 01. Artist, 07 Artist; a bare 1 or 50 stays).
def ftail(name): return strip_num(re.sub(r'(?:\s*\[[^\]]*\]\s*|\s+)project\s*$','',name.strip(),flags=re.I).strip())
def lit(s): return ' '.join(stem(re.split(r'[\\/]',s.strip())[-1].strip()).lower().split())
def scan(artist_key,title_key,must=None,exact=None):
    # Returns ([(score, path), ...] best 3, [(reason, path), ...]: the newer files that were not chosen, [(filter, value), ...]: what matched nothing).
    a=norm(artist_key); t=norm(title_key); nm=norm(stem(must)) if must else ''; at=(a+' '+t).strip()
    if not a or not t: raise ValueError('resolve_masters: the artist and the title need a letter or digit')
    want_t={t,at}-{''}; want_m=keys(must) if must else set()
    asked=variants(t+' '+nm); asked_junk=set(JUNK.findall(t+' '+nm)); out=[]; held=[]; pool=[]; seen=0; hit=False
    vt=not VERS.sub(' ',t).split()      # a title made only of version words (Master, Final): the one case where a tokens-only name can be the title itself
    for name,p in idx:
        n=norm(name)
        if a in n and t in n and not name.endswith('.lnk'):
            mr=os.path.join(p,'MASTER RENDERS')
            try: fs=[f for f in os.listdir(mr) if f.lower().endswith('.wav') and os.path.isfile(os.path.join(mr,f))]
            except Exception: continue
            rows=[]
            for f in fs:
                kn=norm(stem(f)); dc=deco(f,a,t)
                tk=vt and bool(VERS.search(kn)) and not VMIX.sub(' ',VERS.sub(' ',norm(strip_num(stem(f))))).split() and not kn.startswith(a+' ')     # a version-word title and a name of only tokens, no artist: never "named"
                rows.append((f,kn,bool(t) and f' {t} ' in f' {kn} ' and not variants(kn)-asked and not tk,bool(set(JUNK.findall(f.lower()))-asked_junk),dc and bool(VERS.search(kn)),dc,tk))
            # A version-only file is a version of the searched track ONLY in an exact single-track folder: the folder name is exactly
            # artist - title (no brackets, parentheses, & or other words), every file that is not junk is either a titled file (its name,
            # without the decorations in bare(), is exactly the title) or version-only, and at least one titled file exists. Anything
            # else (another title, an extra word, a copy, a typo, a remix word) and no version-only file is promoted: it is held and noted.
            tail=ftail(name); live=[r for r in rows if not r[3]]; same={r[0] for r in live if bare(r[0],a)==t}
            fok=norm(tail)==at and not re.search(r'[()\[\]&]',tail)
            one=fok and bool(same) and all(r[0] in same or r[4] for r in live)
            pref=max([mix(f) for f in same] or [0])
            whole=f' {a} ' in f' {norm(tail)} ' and f' {t} ' in f' {norm(tail)} '      # from the cut folder name: a title word in the label does not count
            why=('ranked lower on mix, hint or exact title' if one else 'the folder may hold other tracks' if fok else
                 'the folder name is not exactly artist and title' if whole else 'the folder name does not name this track')
            start=len(out); gone=False; mine=[]
            for f,kn,named,junk,vo,dc,tk in rows:
                k=keys(f); fp=os.path.join(mr,f); up=vo and one; seen+=1
                ks=norm(strip_num(stem(f))); ks=ks[len(a)+1:] if ks.startswith(a+' ') else ks      # the track number and the artist taken off once
                own=bool(VMIX.search(ks)); cj=bool(set(JUNK.findall(ks))-asked_junk)               # a mix word of its own / junk for the clearance (not a junk word in the artist's name)
                try: mt=os.path.getmtime(fp)
                except OSError: mt=0.0
                # Held for the NOTE (before the must filter, so a hint cannot silence it): a decoration-only file, and in a folder named exactly artist - title
                # also any other file that is not a titled file and carries a version token (a typo, a copy, a backup).
                if not junk and (dc or tk or fok and f not in same and VERS.search(kn)):
                    held.append((ver(f),mt,fp,why if vo or tk and not dc else 'cannot tell which track it belongs to'))
                # A version-only file with no mix word (the artist does not count) takes the folder's best mix; one with a mix word keeps its own. Exact title stays ahead of the date.
                row=((not junk,named or up,bool(k&want_m),bool(nm) and nm in kn),pref if up and not own else mix(f),ver(f),bool(k&want_t),mt,fp)
                if not junk: pool.append(row)
                ok=not (nm and nm not in n and nm not in kn); hit=hit or ok
                if (dc or tk) and not up and not exact:      # a decoration-only or tokens-only file that is not promoted is never an answer (unless asked for by name)
                    gone=gone or ok and not junk
                    continue
                if not ok: continue
                out.append(row); mine.append(cj)
            if gone and mine and all(mine): del out[start:]      # this folder has only junk left and the promotion rule held a master: no answer from it, the NOTE names it
    if exact:
        e=lit(exact); full=[o for o in out if lit(os.path.basename(o[-1]))==e]
        out=full or ([o for o in out if lit(strip_num(lit(os.path.basename(o[-1]))))==e] if strip_num(e)==e else [])
    out.sort(reverse=True); notes=[]
    if not exact:
        top=out[0] if out else None; fbest={}
        for o in sorted(pool,reverse=True): fbest.setdefault(os.path.dirname(o[-1]),o)
        # Each file is compared with the best file of ITS OWN folder (all its non-junk files count; the best file of a folder is compared with the
        # chosen file): a newer one is listed, never passed over in silence. With nothing chosen (a hint that matched nothing) all are listed.
        for v,mt,fp,why in sorted(held,reverse=True):
            fb=fbest.get(os.path.dirname(fp)); ref=fb if fb is not None and fb[-1]!=fp else top
            if (top is None or fp!=top[-1]) and fp not in [q for _,q in notes] and (ref is None or (v,mt)>(ref[2],ref[4])): notes.append((why,fp))
    miss=[(w,v) for w,v,hit in (('hint',must,must and seen and not hit),('exact filename',exact,exact and seen and not out)) if hit][:1]      # the hint first, never both
    return [(o[1]+o[2],o[-1]) for o in out[:3]],notes,miss
def resolve(artist_key,title_key,must=None,exact=None): return scan(artist_key,title_key,must,exact)[0]
if __name__=='__main__':
    def bad(msg): sys.exit('resolve_masters: '+msg)
    picks=json.loads(sys.argv[1])
    if not isinstance(picks,list): bad('the argument must be a JSON list of picks [artist, title, optional must, optional exact filename]')
    for p in picks:
        if not isinstance(p,list): bad(f'each pick must be a list [artist, title, optional must, optional exact filename], got {p!r}')
        if not 2<=len(p)<=4: bad(f'a pick needs 2 to 4 elements [artist, title, optional must, optional exact filename], got {len(p)}')
        if p[0] is None or p[1] is None: bad('artist and title must not be null')
        if not norm(str(p[0])): bad('the artist (1st element) must have a letter or digit')
        if not norm(str(p[1])): bad('the title (2nd element) must have a letter or digit')
        if len(p)==4 and p[3] is not None and not str(p[3]).strip(): bad('the exact filename (4th element) must not be empty; use null to skip it')
    for a,t,*m in picks:
        must=str(m[0]) if m and m[0] is not None else None; exact=str(m[1]) if len(m)>1 and m[1] is not None else None
        r,notes,miss=scan(str(a),str(t),must,exact)
        print(f"{a} - {t}:")
        for s,p in r: print(f"   [{s:>4g}] {p[:2]} ...{p[-105:]}")
        if not r:
            for w,v in miss or [(0,0)]: print(f'   NONE: no file matched the {w} "{v}"' if w else "   NONE")
        for why in dict.fromkeys(w for w,_ in notes):
            print("   NOTE: newer file not chosen.")
            print(f"   Why: {why}")
            for w,q in notes:
                if w==why: print(f"      {q}")
