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


def matches(plan, blend, sam):
    """None, "primary", or "acceptable #i": does the plan equal the owner's answer, or one of his
    `also_acceptable` placements (entry and switch; the end too when the entry gives one)?"""
    if plan is None or plan.in_intro_looped_bars != 0:
        return None
    if all(abs(x - y) < 1e-6 for x, y in zip(plan.key(), sam)):
        return "primary"
    for i, alt in enumerate(blend.get("also_acceptable", []), 1):
        if (abs(plan.in_starts_at_out_bar - alt["in_starts_at_out_bar"]) < 1e-6
                and abs(plan.bass_switch_out_bar - alt["bass_switch_out_bar"]) < 1e-6
                and ("out_end_bar" not in alt or abs(plan.out_end_bar - alt["out_end_bar"]) < 1e-6)):
            return "acceptable #%d" % i
    return None


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
        # RIGHT = the top plan is the owner's answer or one of his also_acceptable placements; also
        # no incoming loop (his blends have none: the same key can play other bars)
        which = matches(plan, b, sam)
        ok = which is not None
        exact += ok
        top3 = pb.candidates(o, n, limit=3, r6=r6)
        primary_top3 = any(matches(c, b, sam) == "primary" for c in top3)
        sp = _sam_plan(o, n, sam, r6)
        swap_only = bool(plan) and any(
            abs(plan.bass_switch_out_bar - v) < 1e-6
            for v in [sam[1]] + [a["bass_switch_out_bar"] for a in b.get("also_acceptable", [])])
        rows.append({"i": i, "ok": ok, "which": which, "primary_top3": primary_top3, "mine": mine,
                     "primary": which == "primary", "swap_only": swap_only,
                     "sam": sam, "plan": plan, "sam_plan": sp})
        out("%d. %s -> %s" % (i, b["out"][:30], b["in"][:30]))
        if plan is None:
            out("   planner: NO PLAN")
            continue
        out("   planner: in %g  switch %g  end %g  cost %.2f  flat runs: %s" % (
            mine + (plan.cost, _fmt_runs(plan.flat_runs))))
        out("   Sam    : in %g  switch %g  end %g  cost %.2f  flat runs: %s   -> %s" % (
            sam + (sp.cost, _fmt_runs(sp.flat_runs), ("RIGHT (%s)" % which) if ok else "MISS")))
        out("      owner's primary answer in the top 3 candidates: %s" % ("yes" if primary_top3 else "no"))
        pool = [plan] + [a.plan for a in plan.alternatives]
        for j, ac in enumerate(b.get("also_acceptable", []), 1):
            hit = [q for q in pool if abs(q.in_starts_at_out_bar - ac["in_starts_at_out_bar"]) < 1e-6
                   and abs(q.bass_switch_out_bar - ac["bass_switch_out_bar"]) < 1e-6]
            out("      owner's also_acceptable #%d (entry %g, swap %g): %s" % (
                j, ac["in_starts_at_out_bar"], ac["bass_switch_out_bar"],
                "offered (top plan or an alternative)" if hit else "NOT offered"))
        for alt in plan.alternatives:
            out("      alternative swap %g (cost %.2f, entry %g, end %g): %s | feel: %s" % (
                alt.swap_out_bar, alt.plan.cost, alt.plan.in_starts_at_out_bar, alt.plan.out_end_bar,
                alt.reason, alt.feel))
        if not ok and r6 == pb.R6_BE and plan is not None:
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
    out("TOTAL %s: top pick = primary %d of %d | primary or also_acceptable %d of %d | primary in top 3 %d of %d"
        " | (info) swap bar equals one of his %d of %d" % (
            mode, sum(r["primary"] for r in rows), len(rows), exact, len(rows),
            sum(r["primary_top3"] for r in rows if r["plan"]), len(rows),
            sum(r["swap_only"] for r in rows), len(rows)))
    return exact, rows


def headline(out=print):
    """The two facts that limit what the six blends prove, computed live, not asserted."""
    key = json.loads(KEY.read_text(encoding="utf-8"))
    content = json.loads(CONTENT.read_text(encoding="utf-8"))
    gaps = [b["bass_switch_out_bar"] - b["in_starts_at_out_bar"] for b in key["blends"]]
    base = _count(key, content, "BOTTOM END")
    saved = pb.W_FLAT_RUN
    pb.W_FLAT_RUN = 0.0
    try:
        off = _count(key, content, "BOTTOM END")
    finally:
        pb.W_FLAT_RUN = saved
    out("HEADLINE (computed now):")
    nb = len(gaps)
    first = gaps[:6]
    out("  switch minus entry per owner blend: %s -> the first six %s; blends 7+ %s" % (
        gaps, "ALL EXACTLY %g bars (they say almost nothing about the switch rule)" % first[0]
        if len(set(first)) == 1 else "are not all equal", "differ" if len(set(gaps)) > 1 else "agree"))
    out("  BOTTOM END score %d of %d; with W_FLAT_RUN = 0 (R6 cost removed) %d of %d -> R6 and the "
        "measured content are %s on these blends; the score comes from geometry" % (
            base, nb, off, nb, "INERT" if base == off else "NOT inert"))
    out("")


def _count(key, content, mode):
    r6 = MODES[mode][1]
    n_ok = 0
    for b in key["blends"]:
        o = prepare(key["tracks"][b["out"]], b["out"], mode, content)
        n = prepare(key["tracks"][b["in"]], b["in"], mode, content)
        p = pb.plan_blend(o, n, r6=r6)
        sam = sam_numbers(b, o)
        n_ok += matches(p, b, sam) is not None
    return n_ok


def table(out=print):
    res = {m: score(m, out=lambda *_: None)[1] for m in MODES}
    names = list(MODES)
    out("")
    out("blend | " + " | ".join("%-24s" % m for m in names))
    nb = len(res[names[0]])
    for i in range(nb):
        cells = []
        for m in names:
            r = res[m][i]
            cells.append("%-24s" % ((r["which"].upper() if r["which"] != "primary" else "RIGHT") if r["ok"] else
                                    ("NO PLAN" if r["plan"] is None else "MISS %g/%g/%g" % r["mine"])))
        out("%5d | %s" % (i + 1, " | ".join(cells)))
    out("primary           | " + " | ".join("%-24s" % ("%d of %d" % (sum(r["primary"] for r in res[m]), nb)) for m in names))
    out("primary or accept.| " + " | ".join("%-24s" % ("%d of %d" % (sum(r["ok"] for r in res[m]), nb)) for m in names))
    out("primary in top 3  | " + " | ".join("%-24s" % ("%d of %d" % (sum(r["primary_top3"] for r in res[m] if r["plan"]), nb)) for m in names))
    out("(info) swap bar equals his | " + " | ".join("%-24s" % ("%d of %d" % (sum(r["swap_only"] for r in res[m]), nb)) for m in names))
    out("")
    out("flat runs (start+length) on SAM'S placement / on the PLANNER'S pick, per column:")
    for i in range(nb):
        for m in names:
            r = res[m][i]
            out("  blend %d  %-12s Sam: %-28s planner: %s" % (
                i + 1, m, _fmt_runs(r["sam_plan"].flat_runs), _fmt_runs(r["plan"].flat_runs) if r["plan"] else "no plan"))


if __name__ == "__main__":
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 0
    detail = "--detail" in sys.argv
    headline()
    for m in MODES:
        score(m, top, detail)
    table()
