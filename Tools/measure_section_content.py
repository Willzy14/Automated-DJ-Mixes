"""Measure what is actually playing in every bar of the answer-key tracks.

Reads each track's cached stem envelopes (`<track>__stemenv.npz`: drums, bass, vocals, other,
mix, tiera_band_low at hop_t seconds) and writes, per track and per BAR (0-based from the
first beat; bar b spans seconds [(b + lead_in_bars) * bar, (b + 1 + lead_in_bars) * bar)):
has_kick, has_bass, has_music (vocals or other).  Used by phrase_blend rule R6.

Usage:  python Tools/measure_section_content.py [--env-dir DIR] [--out FILE]

How bass is decided (the Demucs bass stem is not enough on its own: on PAX the bass lives in
the kick or sub and the stem is nearly empty):
  1. Every channel is divided by its own track's 99.5th percentile, so thresholds are
     relative to each track's own level, never absolute.
  2. Per bar the 90th percentile of an envelope is used, not the mean or median: the bass is
     sidechain-pumped, so a bar-wide median flickers between "on" and "off" mid-bassline.
  3. has_bass = bass-stem P90 >= BASS_STEM_MIN
        OR sustained sub: low-band P90 >= SUSTAIN_LOW_MIN and the low band's 20th percentile
        stays >= SUSTAIN_FLOOR_MIN of its 90th.  A bare kick decays between hits (its floor
        ratio measured 0.00-0.14 on Ashibah's intro and outro, Ninetoes' outro, Back In The
        Dance's intro); a held sub keeps the floor up (PAX drops 0.4-0.6).
Limits, stated plainly: the sustained-sub route cannot tell a long kick tail from a bass held
under the kick (PAX's outro floor, 0.34-0.39, is close to its drop at 16-30, 0.37-0.43), and
there is no audio ground truth beyond Sam's own words for five tracks.
"""
import glob
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
KEY = ROOT / "Documentation" / "Golden Sections" / "Answer Key 2026-11-20 DFTD.json"
OUT = ROOT / "Documentation" / "Golden Sections" / "Content 2026-11-20 DFTD.json"
ENV_DIR = "D:/Claude Workspace/Automated DJ Mixes/d20-replay/proj/_Stem Analysis"

# one distinctive word per track, to pick its envelope file
MATCH = {
    "Darius Syrossian & George Smeddles - Back In The Dance": "Back In The Dance",
    "PAX - Revelry": "PAX - Revelry",
    "Murphy's Law & Guy Mac - Passion (V.I.P Mix)": "Passion",
    "Ninetoes - Come Back": "Ninetoes",
    "Ashibah - Devotion (Original Mix)": "Ashibah",
    "Eli Brown - Better When": "Eli Brown",
    "Adam Ten & Rafael - Call 911": "Call 911",
}

NORM_PCT = 99.5        # each channel relative to its own loud level (same as draw_pair.py)
BAR_PCT = 90           # per-bar level: sidechain pumping makes a bar median flicker
BASS_STEM_MIN = 0.25   # a bass-stem bar at a quarter of the track's own peak is real bass
SUSTAIN_LOW_MIN = 0.6  # sub route only counts when the low band is loud at all
SUSTAIN_FLOOR_MIN = 0.45  # ... and holds nearly half its peak between kicks (bare kicks: <=0.14)
MUSIC_MIN = 0.25       # vocals or other at a quarter of the track's own peak is music
KICK_LOW_MIN = 0.25    # kick = low band and drums stem both clearly up
KICK_DRUMS_MIN = 0.25


def bar_content(env, hop, bpm, n_bars, lead_in_bars=0.0):
    """env: dict of arrays (drums, bass, vocals, other, tiera_band_low).  Returns
    (kick, bass, music) lists of 0/1 per bar, bars past the end of the envelope are 0."""
    norm = {k: np.asarray(env[k], float) / max(np.percentile(env[k], NORM_PCT), 1e-12)
            for k in ("drums", "bass", "vocals", "other", "tiera_band_low")}
    bar = 240.0 / bpm
    n = len(norm["drums"])
    kick, bass, music = [], [], []
    for b in range(int(np.ceil(n_bars))):
        i0 = int(round((b + lead_in_bars) * bar / hop))
        i1 = int(round((b + 1 + lead_in_bars) * bar / hop))
        i0 = max(i0, 0)
        if i1 > n:
            i1 = n
        if i1 - i0 < 2:
            kick.append(0), bass.append(0), music.append(0)
            continue
        p = lambda k, q: float(np.percentile(norm[k][i0:i1], q))
        low90, low20 = p("tiera_band_low", BAR_PCT), p("tiera_band_low", 20)
        floor = low20 / low90 if low90 > 1e-9 else 0.0
        stem = p("bass", BAR_PCT) >= BASS_STEM_MIN
        sub = low90 >= SUSTAIN_LOW_MIN and floor >= SUSTAIN_FLOOR_MIN
        bass.append(int(stem or sub))
        kick.append(int(low90 >= KICK_LOW_MIN and p("drums", BAR_PCT) >= KICK_DRUMS_MIN))
        music.append(int(max(p("vocals", BAR_PCT), p("other", BAR_PCT)) >= MUSIC_MIN))
    return kick, bass, music


def ranges(flags):
    """[start, end) bar runs where flags is 1."""
    out, start = [], None
    for i, f in enumerate(list(flags) + [0]):
        if f and start is None:
            start = i
        if not f and start is not None:
            out.append([start, i])
            start = None
    return out


def load_env(env_dir, word):
    hits = [f for f in glob.glob(env_dir + "/*__stemenv.npz") if word in f]
    if len(hits) != 1:
        raise SystemExit("expected one envelope file for %r, found %d" % (word, len(hits)))
    z = np.load(hits[0], allow_pickle=True)
    return {k: z[k] for k in z.files}, float(z["hop_t"]), hits[0]


def measure(env_dir=ENV_DIR, out=OUT, quiet=False):
    key = json.loads(KEY.read_text(encoding="utf-8"))
    result = {"what": "Measured per-bar content of the DFTD answer-key tracks (has_kick, has_bass, "
                      "has_music). Bars 0-based from the first beat; lead_in_bars honoured. "
                      "Made by Tools/measure_section_content.py.",
              "thresholds": {"NORM_PCT": NORM_PCT, "BAR_PCT": BAR_PCT, "BASS_STEM_MIN": BASS_STEM_MIN,
                             "SUSTAIN_LOW_MIN": SUSTAIN_LOW_MIN, "SUSTAIN_FLOOR_MIN": SUSTAIN_FLOOR_MIN,
                             "MUSIC_MIN": MUSIC_MIN, "KICK_LOW_MIN": KICK_LOW_MIN,
                             "KICK_DRUMS_MIN": KICK_DRUMS_MIN},
              "tracks": {}}
    for name, t in key["tracks"].items():
        if name not in MATCH:
            continue
        env, hop, path = load_env(env_dir, MATCH[name])
        lead = float(t.get("lead_in_bars", 0.0))
        kick, bass, music = bar_content(env, hop, t["bpm"], t["n_bars"], lead)
        result["tracks"][name] = {
            "bpm": t["bpm"], "n_bars": t["n_bars"], "lead_in_bars": lead,
            "has_kick": kick, "has_bass": bass, "has_music": music,
            "ranges": {"kick": ranges(kick), "bass": ranges(bass), "music": ranges(music)}}
        if not quiet:
            print(name)
            for lab, fl in (("kick", kick), ("bass", bass), ("music", music)):
                print("   %-6s %s" % (lab, " ".join("%d-%d" % (a, b) for a, b in ranges(fl))))
    out = Path(out)
    out.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    if not quiet:
        print("wrote", out)
    return result


if __name__ == "__main__":
    a = sys.argv[1:]
    measure(a[a.index("--env-dir") + 1] if "--env-dir" in a else ENV_DIR,
            a[a.index("--out") + 1] if "--out" in a else OUT)
