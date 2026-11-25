"""Build a mix playlist with a musical arc (housier -> harder by GENRE, not BPM) from the Neon credits database.

The Neon credits ledger is the ONLY source for what goes in a mix (Sam, 2026-10-06). Filters mirror the website: label, artist,
year, genre family, streams. Output is a draft playlist for Sam's veto, not a build order.

Usage:
  python Tools/playlist_builder.py sql   --label Defected --label DFTD            # print the SELECT (run it yourself)
  python Tools/playlist_builder.py build --label Defected --arc rise --n 12       # live: uses the read-only mix_planner_ro Neon role
  python Tools/playlist_builder.py build --rows rows.json --arc rise --n 12       # offline: rows = JSON list from the SELECT
Add --resolve to build to print paths to matching master WAVs.
Selection presets (--preset): hits | cool | cult | any.  Arcs (--arc): rise | wave | flat.
build prints a MANIFEST before the track rows: requested vs built, filters, pool counts, spreads, repeats against earlier mixes and the
cached beat-grid verdict per pick (read from earlier runs' logs only; unknown is never a pass). --credible refuses --preset any.
Read-only: only SELECT is ever sent, and the live session is set read-only."""
import argparse, glob, importlib.util, json, math, os, re, sys, unicodedata
sys.stdout.reconfigure(encoding='utf-8')

# Genre -> heat 0..10 (how housey -> how hard). DB genre tags are unapproved enrichment drafts: this is a coarse ladder
# only, and anything not on it is left OUT rather than guessed.
HEAT = {
    'House / Soulful': 1, 'Nu Disco / Disco': 1, 'Funky House': 2, 'Deep House': 2, 'Dance / Pop': 2, 'Indie Dance': 3,
    'House': 3, 'Afro House': 4, 'Progressive House': 4, 'Jackin House': 5, 'Melodic House & Techno': 5,
    'Tech House': 6, 'Bass House': 6, 'Minimal / Deep Tech': 6, 'Techno (Peak Time / Driving)': 8,
    'Techno (Raw / Deep / Hypnotic)': 8,
}
TEMPO_BAND = (118, 132)   # DB bpm is only on ~17% of credits; applied only when present
ARCS = {
    'rise': lambda p: 1 + 6.5 * p,                              # housey start, tech-house finish, techno crossover at the end
    'wave': lambda p: 3.5 + 2.5 * math.sin(math.pi * (2 * p - 0.5)) * (0.6 + 0.4 * p),
    'flat': lambda p: 5.0,
}
SELECT = """select c.credit_key, c.artist, c.title, c.mix_name, c.remixer, c.roles, c.label, c.genre, c.release_date,
       c.bpm, c.musical_key, c.streams_plays, c.streams_confidence, c.enrichment_status, p.display_tier
from public.credits c join public.credits_page p using (credit_key)
where {where}
order by c.streams_plays desc nulls last"""

def where_clause(a):
    w = ["c.streams_plays > 0"]
    for key, col in (('label', 'c.label'), ('artist', 'c.artist')):
        vals = getattr(a, key)
        if vals: w.append("(" + " or ".join(f"{col} ilike '%{v.replace(chr(39), chr(39)*2)}%'" for v in vals) + ")")
    if a.year_from: w.append(f"extract(year from c.release_date) >= {int(a.year_from)}")
    if a.year_to: w.append(f"extract(year from c.release_date) <= {int(a.year_to)}")
    if a.min_streams: w.append(f"c.streams_plays >= {int(a.min_streams)}")
    return " and ".join(w)

def ro_url_from_credentials_file():
    """The read-only role (SELECT on credits + credits_page only) lives in <GITHUB folder>/Credentials/Credentials.txt."""
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Credentials', 'Credentials.txt')
    try: m = re.search(r'^MIX_PLANNER_RO_DATABASE_URL=(\S+)', open(f, encoding='utf-8').read(), re.M)
    except OSError: return None
    return m.group(1) if m else None

def fetch(a):
    if a.rows: return json.load(open(a.rows, encoding='utf-8'))
    url = os.environ.get('MIX_PLANNER_RO_DATABASE_URL') or ro_url_from_credentials_file()
    if not url: sys.exit("No read-only Neon URL (MIX_PLANNER_RO_DATABASE_URL in env or the Credentials file). Run `sql`, execute it against Neon, save rows as JSON, pass --rows.")
    import psycopg2, psycopg2.extras
    con = psycopg2.connect(url, connect_timeout=8); con.set_session(readonly=True)
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(SELECT.format(where=where_clause(a))); rows = [dict(r) for r in cur.fetchall()]
    for r in rows: r['release_date'] = str(r['release_date']) if r['release_date'] else None
    return rows

def year(r): return int(r['release_date'][:4]) if r.get('release_date') else None

def norm(x): return re.sub(r'[^a-z0-9]+', ' ', (x or '').lower()).strip()

def song_keys(r):
    """One key per VERSION. A remix and an original are different tracks with their own streams (Sam, 2026-10-06), so the key is
    title + remixer + mix name; never the bare song title and never the stream count. Only repeat rows of the SAME version collapse."""
    return {'v:' + norm(r['title']) + '|' + norm(r.get('remixer')) + '|' + norm(r.get('mix_name'))}

NOT_A_PLAYLIST_TRACK = re.compile(r'acap|instrumental|instrumenatal|\bdub\b|from stems|radio edit|apella|dj tool', re.I)

def prepare(rows, a, drops=None):
    """Usable pool. drops (optional dict) is filled with {reason: count} so the manifest can say what was lost and why."""
    out = []
    def drop(why):
        if drops is not None: drops[why] = drops.get(why, 0) + 1
    for r in rows:
        if NOT_A_PLAYLIST_TRACK.search(' '.join(str(r.get(k) or '') for k in ('title', 'mix_name', 'genre'))): drop('not a playlist track'); continue
        h = HEAT.get(r.get('genre'))
        if h is None: drop('genre not on the ladder'); continue                                                 # unknown/odd genre: never guess
        if not a.heat_min <= h <= a.heat_max: drop('heat outside range'); continue
        if r.get('bpm') and not TEMPO_BAND[0] <= r['bpm'] <= TEMPO_BAND[1]: drop('tempo outside band'); continue
        if a.year_from and (not year(r) or year(r) < a.year_from): drop('year filter'); continue
        if a.year_to and (not year(r) or year(r) > a.year_to): drop('year filter'); continue
        r = dict(r); r['heat'] = h; r['streams_plays'] = int(r['streams_plays']); out.append(r)
    return out

def preset_score(pool, preset):
    """Return score(r) in 0..1. hits = most streamed; cool = streamed-but-not-mega, credible labels weighted via Sam's veto;
    cult = older records with a real but modest audience. These are PROPOSED definitions for Sam to tune."""
    st = sorted(r['streams_plays'] for r in pool); n = len(st)
    pct = lambda r: sum(1 for s in st if s <= r['streams_plays']) / n if n else 0
    now = 2026
    def score(r):
        p = pct(r); age = (now - year(r)) if year(r) else 0
        if preset == 'hits': return p
        if preset == 'cool': return 1 - abs(p - 0.75) * 1.6                 # strong, not the most obvious
        if preset == 'cult': return (min(age, 12) / 12) * 0.6 + (1 - abs(p - 0.55) * 1.6) * 0.4
        return 0.5 + 0.5 * p
    return score

def build(rows, a):
    pool = prepare(rows, a)
    if len(pool) < a.n: print(f"[warn] only {len(pool)} usable tracks for n={a.n}", file=sys.stderr)
    score = preset_score(pool, a.preset) if pool else (lambda r: 0)
    picked, used_songs, used_artists = [], set(), {}
    for i in range(min(a.n, len(pool))):
        target = ARCS[a.arc](i / max(a.n - 1, 1))
        best, best_v = None, -1e9
        for r in pool:
            if r in picked or song_keys(r) & used_songs or used_artists.get(r['artist'], 0) >= a.max_per_artist: continue
            v = score(r) * 2.0 - abs(r['heat'] - target) * 0.9
            if v > best_v: best, best_v = r, v
        if not best: break
        picked.append(best); used_songs |= song_keys(best); used_artists[best['artist']] = used_artists.get(best['artist'], 0) + 1
    if a.arc == 'rise': picked.sort(key=lambda r: r['heat'])                # slots chose WHO; a rise is then ordered by heat
    return picked

def load_resolver():
    path = os.path.join(os.path.dirname(__file__), 'resolve_masters.py')
    spec = importlib.util.spec_from_file_location('resolve_masters', path)
    resolver = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(resolver)
        if not isinstance(resolver.idx, list) or not all(
                isinstance(entry, list) and len(entry) == 2 and all(isinstance(s, str) and s.strip() for s in entry)
                for entry in resolver.idx):
            raise ValueError('expected a list of folder names and paths')
    except (OSError, ValueError):
        sys.exit('Cannot read Tools/folder_index.json. Run python Tools/build_folder_index.py.')
    return resolver

ORIGINAL_MIXES = ('', 'original', 'original mix', 'main', 'main mix', 'main original', 'main original mix',
                  'extended', 'extended mix')

def master_name_matches(resolver, filename, versions, *bases):
    name = resolver.norm(resolver.strip_num(resolver.stem(filename)))
    for base in bases:
        if base and name != base and not name.startswith(base + ' '): continue
        tail = name[len(base):].strip()
        for version in versions:
            if version and tail != version and not tail.startswith(version + ' '): continue
            if not resolver.VERS.sub(' ', tail[len(version):]).strip(): return True
    return False

def master_matches(resolver, path, artist, title, versions):
    name = os.path.basename(path)
    full_title = artist + ' ' + title
    if master_name_matches(resolver, name, versions, full_title): return True
    folder = resolver.ftail(os.path.basename(os.path.dirname(os.path.dirname(path))))
    folder_artist, sep, _ = folder.partition(' - ')
    if not sep or resolver.norm(folder_artist) != artist: return False
    if master_name_matches(resolver, name, versions, title): return True
    # scan() can promote an untitled delivery in an exact folder, even when its named anchor is a radio edit.
    if not resolver.vonly(name, artist, title) or not master_name_matches(resolver, name, versions, artist, ''):
        return False
    try:
        with os.scandir(os.path.dirname(path)) as entries:
            files = [e.name for e in entries if e.name.lower().endswith('.wav') and e.is_file()]
    except OSError:
        return False
    named = {f for f in files if master_name_matches(resolver, f, versions, full_title, title)}
    return bool(named) and all(f in named or (resolver.vonly(f, artist, title)
                                             and master_name_matches(resolver, f, versions, artist, '')) for f in files)

def resolve_pick(resolver, r):
    artist, title = r['artist'], r['title']
    a, t = resolver.norm(artist or ''), resolver.norm(title or '')
    if not a or not t: return None, [], 'artist and title need a letter or digit'
    remixer, mix_name = ((r.get(k) or '').strip() for k in ('remixer', 'mix_name'))
    rm, mix = resolver.norm(remixer), resolver.norm(mix_name)
    if (remixer and not rm) or (mix_name and not mix): return None, [], 'release version needs a letter or digit'
    version = mix or (rm + ' remix' if rm else '')
    if rm and f' {rm} ' not in f' {version} ': version = rm + ' ' + version
    if version and not resolver.VERS.sub(' ', version).strip(): return None, [], 'release version is a delivery label'
    versions = (version,) if version else ORIGINAL_MIXES
    candidates, notes, miss = resolver.scan(artist, title, must=version or None)
    # The hint may match only the folder, and scan() can return unrelated or alternate files as fallbacks.
    for _, path in candidates:
        if master_matches(resolver, path, a, t, versions): return os.path.abspath(path), notes, None
    if miss:
        kind, value = miss[0]
        return None, notes, f'no file matched the {kind} "{value}"'
    return None, notes, "no candidate matches the credit's title and version"

def report(picked, a, resolver=None):
    resolved = with_notes = 0
    print(f"# Playlist draft: arc={a.arc} preset={a.preset} n={len(picked)}  (source: Neon credits ledger)\n")
    print("Each row is one VERSION with its own streams (a remix and an original are different tracks). Genre is a DB DRAFT tag.\n")
    for i, r in enumerate(picked, 1):
        ver = r.get('mix_name') or (f"{r['remixer']} remix" if r.get('remixer') else 'original/unspecified')
        print(f"{i:>2}. [{r['heat']}] {r['artist']} - {r['title']}  ({ver}) | {r['label']} {year(r) or '?'} | {r['genre']} | "
              f"{r['streams_plays']:,} streams | bpm {r.get('bpm') or '-'} | key {r.get('musical_key') or '-'} | tier {r.get('display_tier')}")
        if resolver is not None:
            path, notes, reason = resolve_pick(resolver, r)
            if path:
                print(f"   MASTER: {path}")
                resolved += 1
            else: print(f"   NONE: {reason}")
            for why, note_path in notes: print(f"   NOTE: {why}: {note_path}")
            with_notes += bool(notes)
    print("\nHeat curve:", ' '.join(str(r['heat']) for r in picked))
    if resolver is None:
        print("Next: Tools/resolve_masters.py to find audio + confirm Sam's version; BPM/key are measured by the pipeline.")
    else:
        print(f"Resolved {resolved} of {len(picked)}; {with_notes} with notes; {len(picked) - resolved} not found")

# --- manifest: what the picker did, said before any stem run ---------------------------------------------------------------
GRID_FAIL_MS = 15.0     # same rule as validate_beatgrid.STEM_KF_FAIL_MS: a stem grid >15 ms off its own kicks fails the gate
DELIVERY_JUNK = re.compile(r'\b(sw v\d+( \d+)*|\d+ ?bit|master|amended|v\d+( \d+)*|wav)\b')
GRID_LINE = re.compile(r'^\s*\[(PASS|FAIL|WARN)\s*\] (.{56}) (.*)$')

def fold(x): return norm(unicodedata.normalize('NFKD', x or '').encode('ascii', 'ignore').decode())

def version_class(v):
    """A version as a comparable token set; every plain original/extended label is the empty set."""
    v = DELIVERY_JUNK.sub(' ', fold(v)); v = ' '.join(v.split())
    return frozenset() if v in ORIGINAL_MIXES else frozenset(v.split()) - {'mix', 'remix'}

def pick_version(r):
    mix, rm = fold(r.get('mix_name')), fold(r.get('remixer'))
    v = mix or (rm + ' remix' if rm else '')
    return rm + ' ' + v if rm and f' {rm} ' not in f' {v} ' else v

def parse_track_name(name):
    """'Artist - Title (Mix) SW V3.wav' -> (artist tokens, folded rest); None when there is no 'Artist - Title' shape."""
    stem = re.sub(r'\.wav$', '', (name or '').strip(), flags=re.I)
    stem = re.sub(r'^\d{1,2}[AB] - ', '', stem, flags=re.I)
    a, sep, rest = stem.partition(' - ')
    if not sep or not fold(a) or not fold(rest): return None
    return frozenset(fold(a).split()), fold(rest)

def entry_matches(entry, r):
    """Same artist (token subset either way), same title at a word boundary, same VERSION: a remix is not its original."""
    tn, pa = fold(r.get('title')), frozenset(fold(r.get('artist')).split())
    if not entry or not tn or not pa: return False
    fa, rest = entry
    if not (pa <= fa or fa <= pa) or not (rest == tn or rest.startswith(tn + ' ')): return False
    return version_class(rest[len(tn):]) == version_class(pick_version(r))

def default_mix_roots():
    """Folders whose children are earlier mix projects (<mix>/Audio/*.wav). Any may be absent on this machine."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = [p for p in os.environ.get('DJMIX_MIX_ROOTS', '').split(os.pathsep) if p]
    try:                                                         # in a git worktree the hub's own folders live in the main checkout
        g = open(os.path.join(here, '.git'), encoding='utf-8').read().strip()
        if g.startswith('gitdir:'): roots.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(g[7:].strip()))), 'Test Project'))
    except OSError: pass
    return roots + [os.path.join(here, 'Test Project'), os.path.join(here, 'Listening Sets'),
                    r'D:\Claude Workspace\Automated DJ Mixes\Projects', r'E:\AI Work\Scratch\Automated DJ Mixes\Projects']

def mix_dirs(roots):
    """Existing earlier-mix folders under the roots (those with an Audio folder); names starting with _ are corpora, skipped."""
    seen, out = set(), []
    for root in roots:
        try: names = sorted(os.listdir(root))
        except OSError: continue
        for n in names:
            d = os.path.join(root, n); k = os.path.normcase(os.path.abspath(d))
            if k in seen or n.startswith('_') or not os.path.isdir(os.path.join(d, 'Audio')): continue
            seen.add(k); out.append(d)
    return out

def wavs_in(d):
    try: return sorted(n for n in os.listdir(d) if n.lower().endswith('.wav'))
    except OSError: return None

def read_earlier_mixes(dirs):
    """-> (mixes, unreadable): mixes = [(mix name, [parsed entries])] for every folder whose Audio listing could be read."""
    mixes, bad = [], 0
    for d in dirs:
        names = wavs_in(os.path.join(d, 'Audio'))
        if not names: bad += names is None; continue
        mixes.append((os.path.basename(d), [e for e in map(parse_track_name, names) if e]))
    return mixes, bad

def repeats_in(r, mixes): return [name for name, entries in mixes if any(entry_matches(e, r) for e in entries)]

def read_grid_cache(dirs):
    """Per-track grid verdicts from earlier runs, read from files only: the gate lines in <mix>/Output/*.log (verdict + name cut at
    54 chars) and <mix>/Hints/grid_overrides.json (grid_vs_kick_ms). -> [(mtime, entry, verdict, ms or None, mix name)]."""
    out = []
    for d in dirs:
        known = [n for sub in ('Audio', '_Excluded Audio', 'Held Back') for n in (wavs_in(os.path.join(d, sub)) or [])]
        full = lambda raw: next((n for n in known if n.startswith(raw)), raw)
        for path in glob.glob(os.path.join(glob.escape(d), 'Output', '*.log')):
            try: lines, mt = open(path, encoding='utf-8', errors='replace').read().splitlines(), os.path.getmtime(path)
            except OSError: continue
            for ln in lines:
                m = GRID_LINE.match(ln)
                if not m: continue
                ms = re.search(r'(\d+(?:\.\d+)?) ?ms OFF', m.group(3)) or re.search(r'grid_vs_kick (\d+(?:\.\d+)?) ?ms', m.group(3))
                e = parse_track_name(full(m.group(2).strip()))
                if e: out.append((mt, e, m.group(1), float(ms.group(1)) if ms else None, os.path.basename(d)))
        p = os.path.join(d, 'Hints', 'grid_overrides.json')
        try: ov, mt = json.load(open(p, encoding='utf-8')), os.path.getmtime(p)
        except (OSError, ValueError): continue                     # absent, corrupt or half written: no cache from this file
        for name, v in (ov.items() if isinstance(ov, dict) else ()):
            ms = v.get('grid_vs_kick_ms') if isinstance(v, dict) and v.get('phase_source') == 'drum-stem-kicks' else None
            e = parse_track_name(name)
            if e and isinstance(ms, (int, float)) and not isinstance(ms, bool):
                out.append((mt, e, 'PASS' if ms <= GRID_FAIL_MS else 'FAIL', float(ms), os.path.basename(d)))
    return out

def grid_for(r, cache):
    """Newest cached verdict for this pick: (verdict, ms, mix name), or None when it was never analysed."""
    hits = [c for c in cache if entry_matches(c[1], r)]
    if not hits: return None
    c = max(hits, key=lambda c: c[0]); return c[2], c[3], c[4]

def spread(items):
    c = {}
    for i in items: c[i] = c.get(i, 0) + 1
    return ', '.join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))) or '-'

def era(r): y = year(r); return f"{y // 5 * 5}-{y // 5 * 5 + 4}" if y else 'undated'

def pickable(pool, a):
    """How many tracks the picker COULD pick from this pool (one per version, max-per-artist): the ceiling behind a short build."""
    songs, used, n = set(), {}, 0
    for r in pool:
        if song_keys(r) & songs or used.get(r['artist'], 0) >= a.max_per_artist: continue
        songs |= song_keys(r); used[r['artist']] = used.get(r['artist'], 0) + 1; n += 1
    return n

def manifest(rows, pool, drops, picked, a, mixes=None, bad_mixes=0, grid_cache=None):
    """Lines telling the truth about what the picker did. mixes/grid_cache None or empty = no earlier-run data was found."""
    L = [f"built {len(picked)} of {a.n} requested"]
    if len(picked) < a.n:
        why = []
        if len(pool) < a.n: why.append(f"pool too small ({len(pool)} usable tracks after the filters)")
        cap = pickable(pool, a)
        if cap < min(a.n, len(pool)): why.append(f"max-per-artist {a.max_per_artist} and repeat rows of one version leave {cap} pickable")
        L[0] += ": " + ("; ".join(why) or "the arc could not fill the remaining slots")
    L.append("filters: " + ' '.join(f"{k}={v}" for k, v in (
        ('label', ','.join(a.label) or '-'), ('artist', ','.join(a.artist) or '-'), ('year', f"{a.year_from or ''}-{a.year_to or ''}"), ('min_streams', a.min_streams or '-'),
        ('heat', f"{a.heat_min}-{a.heat_max}"), ('preset', a.preset), ('arc', a.arc), ('max_per_artist', a.max_per_artist), ('n', a.n),
        ('credible', bool(getattr(a, 'credible', False))))))
    if a.preset == 'any' and not getattr(a, 'credible', False):
        L.append("preset any ranks by stream percentile across all genres; use --credible --preset cool for a coherent set")
    cov = lambda k: sum(1 for r in rows if r.get(k) not in (None, ''))
    L.append(f"pool: {len(rows)} rows fetched; {len(pool)} after prepare; dropped: " +
             (', '.join(f"{k} {v}" for k, v in sorted(drops.items())) or 'nothing'))
    L.append(f"coverage of the {len(rows)} rows: " + ', '.join(f"{k} {cov(k)}" for k in ('genre', 'bpm', 'musical_key', 'release_date')))
    L.append(f"genre spread, picked: {spread(r['genre'] for r in picked)} ({len({r['genre'] for r in picked})} distinct of {len({r['genre'] for r in pool})} in the pool)")
    L.append(f"genre spread, pool: {spread(r['genre'] for r in pool)}")
    L.append(f"era spread, picked: {spread(era(r) for r in picked)}")
    L.append(f"era spread, pool: {spread(era(r) for r in pool)}")
    L.append("heat curve, picked order: " + (' '.join(str(r['heat']) for r in picked) or '-'))
    rep = [repeats_in(r, mixes) if mixes else [] for r in picked]
    L.append((f"repeats: {sum(bool(x) for x in rep)} of {len(picked)} picks were in an earlier mix (checked against {len(mixes)} earlier mixes"
              + (f"; {bad_mixes} unreadable" if bad_mixes else '') + ")") if mixes else "repeats: not checked (no earlier mix list found)")
    gr = [grid_for(r, grid_cache) if grid_cache else None for r in picked]
    known = sum(g is not None for g in gr)
    L.append(f"grid known for {known} of {len(picked)} picks" + ("" if grid_cache else " (no earlier gate output found)"))
    L.append("picks:")
    for i, r in enumerate(picked):
        g = gr[i]
        gtxt = "grid: unknown (never analysed)" if g is None else f"grid: {g[0]}{f' {g[1]:.1f} ms' if g[1] is not None else ''} (cached) [{g[2]}]"
        L.append(f"  #{i + 1} {r['artist']} - {r['title']}: {gtxt}" + (f"; repeat of: {', '.join(rep[i])}" if rep[i] else ''))
    fails = [f"{r['artist']} - {r['title']}" + (f" ({g[1]:.1f} ms)" if g[1] is not None else '') for r, g in zip(picked, gr) if g and g[0] == 'FAIL']
    L.append(f"summary: built {len(picked)} of {a.n}; {sum(bool(x) for x in rep)} repeats" + ("" if mixes else " (not checked)") +
             f"; grid known {known} of {len(picked)}" + (f"; CACHED GRID FAIL: {'; '.join(fails)}" if fails else ""))
    return L

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['sql', 'build'])
    ap.add_argument('--label', action='append', default=[]); ap.add_argument('--artist', action='append', default=[])
    ap.add_argument('--year-from', type=int); ap.add_argument('--year-to', type=int); ap.add_argument('--min-streams', type=int)
    ap.add_argument('--arc', choices=list(ARCS), default='rise'); ap.add_argument('--preset', choices=['hits', 'cool', 'cult', 'any'], default='any')
    ap.add_argument('--n', type=int, default=12); ap.add_argument('--max-per-artist', type=int, default=1)
    ap.add_argument('--heat-min', type=int, default=0); ap.add_argument('--heat-max', type=int, default=10)
    ap.add_argument('--rows')
    ap.add_argument('--credible', action='store_true', help='refuse --preset any (it ranks by stream percentile across every genre)')
    ap.add_argument('--mixes-dir', action='append', default=[], help='folder of earlier mix projects (<mix>/Audio/*.wav) for the repeat and grid checks; repeatable')
    ap.add_argument('--resolve', action='store_true', help='print matching master WAV paths for build')
    a = ap.parse_args()
    if a.cmd == 'sql':
        if a.resolve: ap.error('--resolve requires build')
        print(SELECT.format(where=where_clause(a))); return
    if a.credible and a.preset == 'any': ap.error('--credible refuses --preset any: it ranks by stream percentile across all genres; use --preset cool, cult or hits')
    resolver = load_resolver() if a.resolve else None
    rows = fetch(a); drops = {}; pool = prepare(rows, a, drops); picked = build(rows, a)
    dirs = mix_dirs(a.mixes_dir + default_mix_roots()); mixes, bad = read_earlier_mixes(dirs)
    lines = manifest(rows, pool, drops, picked, a, mixes, bad, read_grid_cache(dirs))
    if len(picked) < a.n: print('[warn] ' + lines[0], file=sys.stderr)
    print(chr(10).join(lines) + chr(10)); report(picked, a, resolver)

if __name__ == '__main__': main()
