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


def point_name(letter, track, bar):
    """The owner's name for a phrase point: fills F1.. in order (A-F7 end), section lines numbered from 1
    after the track start (A11, B1), the track start and end."""
    names = []
    fills = sorted(track.get("fills", []), key=lambda f: f[0])
    for i, f in enumerate(fills, 1):
        if abs(bar - float(f[1])) < 1e-9:
            names.append("%s-F%d end" % (letter, i))
        elif abs(bar - float(f[0])) < 1e-9:
            names.append("%s-F%d start" % (letter, i))
    lines = [x for x in pb._sections(track) if x[0] > 0]
    for j, x in enumerate(lines, 1):
        if abs(bar - x[0]) < 1e-9:
            names.append("%s%d" % (letter, j))
    if abs(bar) < 1e-9:
        names.append("%s start" % letter)
    if abs(bar - float(track["n_bars"])) < 1e-9:
        names.append("%s end" % letter)
    if not names:
        return "%s bar %g (no phrase point)" % (letter, bar)
    return "%s (bar %g)" % (" = ".join(names), bar)


def pairing_name(plan, out_t, in_t):
    """e.g. 'A-F7 end (bar 184) with B1 (bar 16) [B kick-in]' for a candidate's swap."""
    k = plan.bass_switch_out_bar - plan.in_starts_at_out_bar - plan.in_intro_looped_bars
    a = point_name("A", out_t, plan.bass_switch_out_bar)
    b = point_name("B", in_t, k)
    role = " [B %s]" % plan.in_point_kind if plan.in_point_kind else ""
    kind = "PAIRING" if plan.paired else "one-sided"
    if plan.sane:
        kind = "sane PAIRING"
    return "%s with %s%s  <%s>" % (a, b, role, kind)


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


PLACEMENT_RULES = ("SWAP-ANCHORED", "FILL-END", "MINUTE-MARK")


def placement_rules_for(plan, out_t):
    """Which placement rules a candidate satisfies (round 5):
    SWAP-ANCHORED  the swap is the incoming's bass-in about 30 s before the original end (entry derived)
    FILL-END       entry and swap are both the END of an outgoing fill
    MINUTE-MARK    the entry is an outgoing phrase point about a minute before the outgoing end"""
    fill_ends = {float(f[1]) for f in out_t.get("fills", [])}
    return {
        "SWAP-ANCHORED": bool(plan.swap_anchored),
        "FILL-END": plan.in_starts_at_out_bar in fill_ends and plan.bass_switch_out_bar in fill_ends,
        "MINUTE-MARK": bool(plan.minute_mark),
    }


def disagreement(out_t, in_t, r6=pb.R6_BE):
    """Best-ranked candidate per placement rule, and a disagreement score: the number of distinct
    placements among them times 100, plus the spread of their swap bars.  0 when no rule is satisfiable."""
    cs = pb.candidates(out_t, in_t, r6=r6)
    best = {}
    for rule in PLACEMENT_RULES:
        for i, c in enumerate(cs, 1):
            if placement_rules_for(c, out_t)[rule]:
                best[rule] = (i, c)
                break
    keys = {c.key() for _, c in best.values()}
    swaps = [c.bass_switch_out_bar for _, c in best.values()]
    score = (len(keys) * 100 + (max(swaps) - min(swaps))) if len(keys) > 1 else 0
    return best, score


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
        out("      swap: %s" % pairing_name(c, out_t, in_t))
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
    pbest, pscore = disagreement(out_t, in_t, r6)
    out("  best candidate meeting each PLACEMENT rule (swap-anchored, fill-end, one-minute mark):")
    for rule in PLACEMENT_RULES:
        if rule in pbest:
            i, c = pbest[rule]
            out("    %-14s rank %-3d entry %g  swap %g  end %g  cost %.2f" % (
                rule, i, c.in_starts_at_out_bar, c.bass_switch_out_bar, c.out_end_bar, c.cost))
        else:
            out("    %-14s no candidate meets it" % rule)
    out("  placement disagreement score %d (distinct placements x 100 + swap spread)" % pscore)
    return {"candidates": len(cs), "disagree": disagree, "placement_score": pscore}


def _prepared(tracks, content, name):
    t = json.loads(json.dumps(tracks[name]))
    if name in content:
        c = content[name]
        t["content"] = {k: c[k] for k in ("has_bass", "has_music", "has_kick") if k in c}
    return t


def scan(keydata, content, top=5, out=print):
    """Rank every ordered pair of key tracks the owner has NOT already placed by how much the three
    placement rules disagree: the pairs most worth showing him next."""
    tracks = keydata["tracks"]
    placed = {(b["out"], b["in"]) for b in keydata.get("blends", [])}
    rows = []
    for a in tracks:
        for b in tracks:
            if a == b or (a, b) in placed:
                continue
            best, score = disagreement(_prepared(tracks, content, a), _prepared(tracks, content, b))
            rows.append((score, a, b, best))
    rows.sort(key=lambda r: -r[0])
    out("pairs ranked by placement disagreement (already placed by the owner are skipped):")
    for score, a, b, best in rows[:top]:
        out("  %4d  %s -> %s" % (score, a, b))
        for rule in PLACEMENT_RULES:
            if rule in best:
                i, c = best[rule]
                out("        %-14s entry %g swap %g end %g (rank %d, cost %.2f)" % (
                    rule, c.in_starts_at_out_bar, c.bass_switch_out_bar, c.out_end_bar, i, c.cost))
            else:
                out("        %-14s no candidate" % rule)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", default=str(KEY))
    ap.add_argument("--content", default=str(CONTENT))
    ap.add_argument("--out", dest="out_name")
    ap.add_argument("--in", dest="in_name")
    ap.add_argument("--scan", action="store_true",
                    help="rank every ordered pair of tracks in the key by placement disagreement")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--r6", default=pb.R6_BE, choices=[pb.R6_BE, pb.R6_V1])
    a = ap.parse_args(argv)
    keydata = json.loads(Path(a.key).read_text(encoding="utf-8"))
    tracks = keydata["tracks"]
    content = {}
    if Path(a.content).exists():
        content = json.loads(Path(a.content).read_text(encoding="utf-8")).get("tracks", {})
    if a.scan:
        scan(keydata, content, a.top)
        return 0
    if not (a.out_name and a.in_name):
        raise SystemExit("--out and --in are required unless --scan is given")
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
