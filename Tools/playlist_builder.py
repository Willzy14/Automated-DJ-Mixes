"""Build a mix playlist with a musical arc (housier -> harder by GENRE, not BPM) from the Neon credits database.

The Neon credits ledger is the ONLY source for what goes in a mix (Sam, 2026-10-06). Filters mirror the website: label, artist,
year, genre family, streams. Output is a draft playlist for Sam's veto, not a build order.

Usage:
  python Tools/playlist_builder.py sql   --label Defected --label DFTD            # print the SELECT (run it yourself)
  python Tools/playlist_builder.py build --label Defected --arc rise --n 12       # live: uses the read-only mix_planner_ro Neon role
  python Tools/playlist_builder.py build --rows rows.json --arc rise --n 12       # offline: rows = JSON list from the SELECT
Selection presets (--preset): hits | cool | cult | any.  Arcs (--arc): rise | wave | flat.
Read-only: only SELECT is ever sent, and the live session is set read-only."""
import argparse, json, math, os, re, sys
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

def title_stem(r): return re.sub(r'[^a-z0-9]+', ' ', re.sub(r'\(.*?\)|\[.*?\]', '', r['title']).lower()).strip()

def song_keys(r):
    """Many credit rows are one song (versions, misspelt artists). Same title stem OR same stream count = same song."""
    return {'t:' + title_stem(r), 's:' + str(r['streams_plays'])}

NOT_A_PLAYLIST_TRACK = re.compile(r'acap|instrumental|instrumenatal|\bdub\b|from stems|radio edit|apella|dj tool', re.I)

def prepare(rows, a):
    out = []
    for r in rows:
        if NOT_A_PLAYLIST_TRACK.search(' '.join(str(r.get(k) or '') for k in ('title', 'mix_name', 'genre'))): continue
        h = HEAT.get(r.get('genre'))
        if h is None: continue                                            # unknown/odd genre: never guess
        if r.get('bpm') and not TEMPO_BAND[0] <= r['bpm'] <= TEMPO_BAND[1]: continue
        if a.year_from and (not year(r) or year(r) < a.year_from): continue
        if a.year_to and (not year(r) or year(r) > a.year_to): continue
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

def report(picked, a):
    print(f"# Playlist draft: arc={a.arc} preset={a.preset} n={len(picked)}  (source: Neon credits ledger)\n")
    print("Genre is a DB DRAFT tag; streams belong to the SONG, not necessarily the version Sam worked on - check version before use.\n")
    for i, r in enumerate(picked, 1):
        ver = r.get('mix_name') or (f"{r['remixer']} remix" if r.get('remixer') else 'original/unspecified')
        print(f"{i:>2}. [{r['heat']}] {r['artist']} - {r['title']}  ({ver}) | {r['label']} {year(r) or '?'} | {r['genre']} | "
              f"{r['streams_plays']:,} streams | bpm {r.get('bpm') or '-'} | key {r.get('musical_key') or '-'} | tier {r.get('display_tier')}")
    print("\nHeat curve:", ' '.join(str(r['heat']) for r in picked))
    print("Next: Tools/resolve_masters.py to find audio + confirm Sam's version; BPM/key are measured by the pipeline.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['sql', 'build'])
    ap.add_argument('--label', action='append', default=[]); ap.add_argument('--artist', action='append', default=[])
    ap.add_argument('--year-from', type=int); ap.add_argument('--year-to', type=int); ap.add_argument('--min-streams', type=int)
    ap.add_argument('--arc', choices=list(ARCS), default='rise'); ap.add_argument('--preset', choices=['hits', 'cool', 'cult', 'any'], default='any')
    ap.add_argument('--n', type=int, default=12); ap.add_argument('--max-per-artist', type=int, default=1)
    ap.add_argument('--rows')
    a = ap.parse_args()
    if a.cmd == 'sql': print(SELECT.format(where=where_clause(a))); return
    report(build(fetch(a), a), a)

if __name__ == '__main__': main()
