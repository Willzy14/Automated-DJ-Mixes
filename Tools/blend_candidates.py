"""Show the top blend candidates for one pair of tracks, with every cost term, and say whether the
candidates DISAGREE on where the bass switch goes.

Usage:
  python Tools/blend_candidates.py --out "Ninetoes" --in "Ashibah"
         [--key FILE] [--content FILE] [--top 5] [--r6 be|v1]

--key     JSON with "tracks": {name: {bpm, n_bars, sections, fills}}  (default: the DFTD answer key)
--content JSON from Tools/measure_section_content.py (default: the DFTD content file, used for any
          track named in it; other tracks fall back to labels)
--out / --in   a track name or any unique part of one

Three switch rules the six answer-key blends cannot tell apart (in all six the switch is exactly
entry + 16 bars):
  ENTRY+16   the switch is 16 bars after the entry
  30 SECONDS the switch is within one bar of 30 seconds before the ORIGINAL outgoing end
  BOTH       the switch is a phrase point of BOTH tracks
For each rule it prints the best-ranked candidate that satisfies it.  When those best candidates have
different switch bars the pair DISCRIMINATES the rules: a good seventh or eighth pair to show the owner.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
import phrase_blend as pb  # noqa: E402

KEY = ROOT / "Documentation" / "Golden Sections" / "Answer Key 2026-11-20 DFTD.json"
CONTENT = ROOT / "Documentation" / "Golden Sections" / "Content 2026-11-20 DFTD.json"


def find(tracks, part):
    hits = [n for n in tracks if part.lower() in n.lower()]
    if len(hits) != 1:
        raise SystemExit("%r matches %d tracks: %s" % (part, len(hits), hits))
    return hits[0]


def rules_for(plan, out_t, in_t):
    """Which of the three switch rules a candidate satisfies."""
    out_pts, in_pts = pb.phrase_points(out_t), pb.phrase_points(in_t)
    a, s = plan.in_starts_at_out_bar, plan.bass_switch_out_bar
    k = s - a - plan.in_intro_looped_bars
    e0 = min(plan.out_end_bar, float(out_t["n_bars"]))
    half_minute_bars = (30.0 / (240.0 / float(out_t["bpm"])))
    return {
        "ENTRY+16": abs((s - a) - 16.0) < 1e-9,
        "30 SECONDS": abs((e0 - s) - half_minute_bars) <= 1.0,
        "BOTH": any(abs(s - p) < 1e-9 for p in out_pts) and any(abs(k - p) < 1e-9 for p in in_pts),
    }


def report(out_t, in_t, out_name, in_name, top=5, r6=pb.R6_BE, out=print):
    cs = pb.candidates(out_t, in_t, r6=r6)
    out("%s  ->  %s   (R6 %s, %d candidates)" % (out_name, in_name, r6, len(cs)))
    if not cs:
        out("  no candidate fits the mix zones")
        return {"candidates": 0, "disagree": False}
    for i, c in enumerate(cs[:top], 1):
        r = rules_for(c, out_t, in_t)
        terms = " ".join("%s=%.2f" % (k, v) for k, v in sorted(c.terms.items())) or "all terms zero"
        out("  #%d in %g  switch %g  end %g  (loop %g, in-loop %g, cut %g)  cost %.2f%s" % (
            i, c.in_starts_at_out_bar, c.bass_switch_out_bar, c.out_end_bar, c.out_looped_bars,
            c.in_intro_looped_bars, c.out_cut_bars, c.cost, "  [minute mark]" if c.minute_mark else ""))
        out("      terms: %s" % terms)
        out("      switch rules met: %s" % (", ".join(k for k, v in r.items() if v) or "none"))
        if c.flat_runs:
            out("      flat runs: %s" % " ".join("%g+%g" % x for x in c.flat_runs))
    best = {}
    for rule in ("ENTRY+16", "30 SECONDS", "BOTH"):
        for i, c in enumerate(cs, 1):
            if rules_for(c, out_t, in_t)[rule]:
                best[rule] = (i, c)
                break
    out("  best candidate meeting each switch rule:")
    for rule in ("ENTRY+16", "30 SECONDS", "BOTH"):
        if rule in best:
            i, c = best[rule]
            out("    %-10s rank %-3d switch %g  (entry %g, end %g, cost %.2f)" % (
                rule, i, c.bass_switch_out_bar, c.in_starts_at_out_bar, c.out_end_bar, c.cost))
        else:
            out("    %-10s no candidate meets it" % rule)
    switches = {c.bass_switch_out_bar for c in cs[:top]}
    switch_bars = {best[r][1].bass_switch_out_bar for r in best}
    disagree = len(switch_bars) > 1
    if disagree:
        out("  >>> DISAGREE: the best candidate for each rule uses different switches %s (top-%d "
            "switches: %s). This pair can discriminate." % (sorted(switch_bars), top, sorted(switches)))
    else:
        out("  >>> agree: the best candidate for every rule has the same switch %s; this pair cannot "
            "discriminate (other top-%d switches %s meet no rule)." % (sorted(switch_bars), top,
                                                                      sorted(switches - switch_bars)))
    return {"candidates": len(cs), "disagree": disagree}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", default=str(KEY))
    ap.add_argument("--content", default=str(CONTENT))
    ap.add_argument("--out", required=True, dest="out_name")
    ap.add_argument("--in", required=True, dest="in_name")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--r6", default=pb.R6_BE, choices=[pb.R6_BE, pb.R6_V1])
    a = ap.parse_args(argv)
    tracks = json.loads(Path(a.key).read_text(encoding="utf-8"))["tracks"]
    content = {}
    if Path(a.content).exists():
        content = json.loads(Path(a.content).read_text(encoding="utf-8")).get("tracks", {})
    names = (find(tracks, a.out_name), find(tracks, a.in_name))
    prepared = []
    for n in names:
        t = json.loads(json.dumps(tracks[n]))
        if n in content:
            c = content[n]
            t["content"] = {k: c[k] for k in ("has_bass", "has_music", "has_kick") if k in c}
        prepared.append(t)
    report(prepared[0], prepared[1], names[0], names[1], a.top, a.r6)
    return 0


if __name__ == "__main__":
    sys.exit(main())
