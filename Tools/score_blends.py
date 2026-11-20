"""Score phrase_blend.plan_blend against the six blends in the DFTD answer key.

Usage:  python Tools/score_blends.py [--detail] [--top N]

Four columns.  Same planner and weights each time; only the R6 content source and rule change:
  MEASURED    round 2: per-bar bass/music from the stems, per-bar flat ban (R6 v1), no overrides
  LABELS      old R6: INTRO/OUTRO never carry, DROP/BUILD/BREAK do, per-bar ban (v1), no overrides
  OVERRIDES   old R6 labels plus the hand-set `carries` overrides below (v1)
  BOTTOM END  round 3, Sam's own statement of R6: a bar is flat when NEITHER side gives bottom
              end (kick or bass; after the switch the outgoing's bass is cut so only its kick;
              before the switch the incoming's lows are cut so it gives none).  Music is only
              reported.  Flatness costs by the length of each continuous flat run.
For each blend it prints the planner's answer, Sam's answer, and the flat runs (start+length, in
the outgoing's bar numbers) on SAM'S placement and on the PLANNER'S pick.  A blend that misses
under BOTTOM END gets a term-by-term cost breakdown of Sam's placement against the planner's
pick.  The answer key and the content file are read only, never edited.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
import phrase_blend as pb  # noqa: E402

KEY = ROOT / "Documentation" / "Golden Sections" / "Answer Key 2026-11-20 DFTD.json"
CONTENT = ROOT / "Documentation" / "Golden Sections" / "Content 2026-11-20 DFTD.json"

# Per-section `carries` overrides, applied to a COPY of the key data (the key is not edited).
# Kept only for the OVERRIDES column: with labels alone R6 calls blends 3, 4 and 6 flat.  They
# were INFERRED from Sam accepting those blends, not measured, so that column is partly
# circular.  The MEASURED columns replace them.
OVERRIDES = {
    "Ninetoes - Come Back": {0.0: True, 160.0: True},
    "Adam Ten & Rafael - Call 911": {0.0: True},
}

# column name -> (content source, R6 rule)
MODES = {
    "MEASURED": ("measured", pb.R6_V1),
    "LABELS": ("labels", pb.R6_V1),
    "OVERRIDES": ("overrides", pb.R6_V1),
    "BOTTOM END": ("measured", pb.R6_BE),
}


def apply_overrides(track, name, overrides):
    t = json.loads(json.dumps(track))
    ov = overrides.get(name, {})
    for s in t["sections"]:
        if float(s[0]) in ov:
            del s[2:]
            s.append(ov[float(s[0])])
    return t


def prepare(track, name, mode, content):
    """mode: a column name, or one of 'measured' / 'labels' / 'overrides'."""
    source = MODES[mode][0] if mode in MODES else mode.lower()
    if source == "overrides":
        return apply_overrides(track, name, OVERRIDES)
    t = json.loads(json.dumps(track))
    if source == "measured":
        c = content["tracks"][name]
        t["content"] = {"has_bass": c["has_bass"], "has_music": c["has_music"], "has_kick": c["has_kick"]}
    return t


def sam_numbers(blend, out_t):
    e = blend.get("out_looped_to_bar")
    if e is None:
        e = blend.get("out_cut_at_bar")
    if e is None:
        e = out_t["n_bars"]
    return (blend["in_starts_at_out_bar"], blend["bass_switch_out_bar"], e)


def _runs(flag, lo, hi):
    out, start = [], None
    for x in range(int(lo), int(hi) + 1):
        f = x < hi and flag(x)
        if f and start is None:
            start = x
        if not f and start is not None:
            out.append("%d-%d" % (start, x))
            start = None
    return " ".join(out) or "none"


def _fmt_runs(runs):
    return " ".join("%g+%g" % r for r in runs) or "none"


def _sam_plan(o, n, sam, r6):
    """Sam's placement scored exactly as the planner scores any candidate."""
    return pb._score(o, n, sam[0], sam[1], sam[2], 0.0, 0.0, pb.phrase_points(o), pb.phrase_points(n), r6)


def _breakdown(label, plan):
    t = plan.terms
    parts = " ".join("%s=%.2f" % (k, t[k]) for k in sorted(t))
    return "      %-14s total %7.2f :: %s" % (label, plan.cost, parts or "all terms zero")


def score(mode, top=0, detail=False, out=print):
    key = json.loads(KEY.read_text(encoding="utf-8"))
    content = json.loads(CONTENT.read_text(encoding="utf-8"))
    r6 = MODES[mode][1]
    exact, rows = 0, []
    out("--- %s (content: %s, R6 %s) ---" % (mode, MODES[mode][0], r6))
    for i, b in enumerate(key["blends"], 1):
        o = prepare(key["tracks"][b["out"]], b["out"], mode, content)
        n = prepare(key["tracks"][b["in"]], b["in"], mode, content)
        plan = pb.plan_blend(o, n, r6=r6)
        sam = sam_numbers(b, o)
        mine = plan.key() if plan else (None, None, None)
        ok = plan is not None and all(abs(x - y) < 1e-6 for x, y in zip(mine, sam))
        exact += ok
        sp = _sam_plan(o, n, sam, r6)
        rows.append({"i": i, "ok": ok, "mine": mine, "sam": sam, "plan": plan, "sam_plan": sp})
        out("%d. %s -> %s" % (i, b["out"][:30], b["in"][:30]))
        out("   planner: in %g  switch %g  end %g  cost %.2f  flat runs: %s" % (
            mine + (plan.cost, _fmt_runs(plan.flat_runs))))
        out("   Sam    : in %g  switch %g  end %g  cost %.2f  flat runs: %s   -> %s" % (
            sam + (sp.cost, _fmt_runs(sp.flat_runs), "EXACT" if ok else "MISS")))
        if not ok and r6 == pb.R6_BE:
            out("      cost breakdown, term by term (no weight was changed to flip this):")
            out(_breakdown("planner's pick", plan))
            out(_breakdown("Sam's placement", sp))
            diff = {k: sp.terms.get(k, 0.0) - plan.terms.get(k, 0.0) for k in set(sp.terms) | set(plan.terms)}
            out("      Sam minus planner, by term: " + " ".join(
                "%s=%+.2f" % (k, diff[k]) for k in sorted(diff) if abs(diff[k]) > 1e-9))
        if r6 == pb.R6_BE and (detail or not ok):
            co, ci = content["tracks"][b["out"]], content["tracks"][b["in"]]
            a_, s_, e_ = sam
            rr = lambda tr, key, lo, hi: _runs(lambda x: x < len(tr[key]) and tr[key][x], lo, hi)
            out("      measured on Sam's placement (out bars %g-%g, switch %g; incoming bars 0-%g):" % (a_, e_, s_, e_ - a_))
            out("        outgoing kick %s | bass %s | music %s" % (
                rr(co, "has_kick", a_, e_), rr(co, "has_bass", a_, e_), rr(co, "has_music", a_, e_)))
            out("        incoming kick %s | bass %s | music %s" % (
                rr(ci, "has_kick", 0, e_ - a_), rr(ci, "has_bass", 0, e_ - a_), rr(ci, "has_music", 0, e_ - a_)))
        if detail and sp.flat_bars:
            a = sam[0]
            out("      what each side has in Sam's flat stretches (out bar numbers, incoming bar = out - %g):" % a)
            for st in sp.stretches:
                if st["flat"]:
                    out("        out bars %g-%g (incoming %g-%g): outgoing carries=%s, incoming carries=%s" % (
                        st["start"], st["end"], st["start"] - a, st["end"] - a, st["out"], st["in"]))
        if top:
            for c in pb.candidates(o, n, limit=top, r6=r6):
                out("      cand %-26s cost %6.2f flat %g" % (c.key(), c.cost, c.flat_bars))
    out("TOTAL %s: %d of %d exact" % (mode, exact, len(rows)))
    return exact, rows


def table(out=print):
    res = {m: score(m, out=lambda *_: None)[1] for m in MODES}
    names = list(MODES)
    out("")
    out("blend | " + " | ".join("%-24s" % m for m in names))
    for i in range(6):
        cells = []
        for m in names:
            r = res[m][i]
            cells.append("%-24s" % ("EXACT" if r["ok"] else "MISS %g/%g/%g" % r["mine"]))
        out("%5d | %s" % (i + 1, " | ".join(cells)))
    out("total | " + " | ".join("%-24s" % ("%d of 6" % sum(r["ok"] for r in res[m])) for m in names))
    out("")
    out("flat runs (start+length) on SAM'S placement / on the PLANNER'S pick, per column:")
    for i in range(6):
        for m in names:
            r = res[m][i]
            out("  blend %d  %-12s Sam: %-28s planner: %s" % (
                i + 1, m, _fmt_runs(r["sam_plan"].flat_runs), _fmt_runs(r["plan"].flat_runs)))


if __name__ == "__main__":
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 0
    detail = "--detail" in sys.argv
    for m in MODES:
        score(m, top, detail)
    table()
