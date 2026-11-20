"""Score phrase_blend.plan_blend against the six blends in the DFTD answer key.

Usage:  python Tools/score_blends.py [--detail] [--top N]

Three columns, same planner and weights each time, only the R6 content source changes:
  MEASURED  per-bar bass/music measured from the stems (Content 2026-11-20 DFTD.json), no overrides
  LABELS    old R6: INTRO/OUTRO never carry, DROP/BUILD/BREAK do, no overrides
  OVERRIDES old R6 labels plus the hand-set `carries` overrides below
For each blend it prints the planner's answer, Sam's answer, and how many flat bars the planner
sees on SAM'S placement (the direct test of whether R6 agrees with him).  The answer key and
the content file are read only, never edited.
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
# circular.  The MEASURED column replaces them.
OVERRIDES = {
    "Ninetoes - Come Back": {0.0: True, 160.0: True},
    "Adam Ten & Rafael - Call 911": {0.0: True},
}

MODES = ("MEASURED", "LABELS", "OVERRIDES")


def apply_overrides(track, name, overrides):
    t = json.loads(json.dumps(track))
    ov = overrides.get(name, {})
    for s in t["sections"]:
        if float(s[0]) in ov:
            del s[2:]
            s.append(ov[float(s[0])])
    return t


def prepare(track, name, mode, content):
    if mode == "OVERRIDES":
        return apply_overrides(track, name, OVERRIDES)
    t = json.loads(json.dumps(track))
    if mode == "MEASURED":
        t["content"] = {"has_bass": content["tracks"][name]["has_bass"],
                        "has_music": content["tracks"][name]["has_music"]}
    return t


def sam_numbers(blend, out_t):
    e = blend.get("out_looped_to_bar")
    if e is None:
        e = blend.get("out_cut_at_bar")
    if e is None:
        e = out_t["n_bars"]
    return (blend["in_starts_at_out_bar"], blend["bass_switch_out_bar"], e)


def _runs(flags, lo, hi):
    out, start = [], None
    for x in range(int(lo), int(hi) + 1):
        f = x < hi and flags(x)
        if f and start is None:
            start = x
        if not f and start is not None:
            out.append("%d-%d" % (start, x))
            start = None
    return " ".join(out) or "none"


def score(mode, top=0, detail=False, out=print):
    key = json.loads(KEY.read_text(encoding="utf-8"))
    content = json.loads(CONTENT.read_text(encoding="utf-8"))
    exact, rows = 0, []
    out("--- R6 content source: %s ---" % mode)
    for i, b in enumerate(key["blends"], 1):
        o = prepare(key["tracks"][b["out"]], b["out"], mode, content)
        n = prepare(key["tracks"][b["in"]], b["in"], mode, content)
        plan = pb.plan_blend(o, n)
        sam = sam_numbers(b, o)
        mine = plan.key() if plan else (None, None, None)
        ok = plan is not None and all(abs(x - y) < 1e-6 for x, y in zip(mine, sam))
        exact += ok
        _, sam_flat = pb.content_check(o, n, *sam)
        rows.append({"i": i, "ok": ok, "mine": mine, "sam": sam, "sam_flat": sam_flat, "plan": plan})
        out("%d. %s -> %s" % (i, b["out"][:30], b["in"][:30]))
        if plan:
            out("   planner: in %g  switch %g  end %g   (loop %g, in-loop %g, flat %g, cost %.2f)" % (
                mine[0], mine[1], mine[2], plan.out_looped_bars, plan.in_intro_looped_bars,
                plan.flat_bars, plan.cost))
        else:
            out("   planner: NO PLAN")
        out("   Sam    : in %g  switch %g  end %g   -> %s   flat bars on Sam's placement: %g" % (
            sam + ("EXACT" if ok else "MISS", sam_flat)))
        if detail and sam_flat:
            a, s_, e = sam
            nb = n["n_bars"]
            out("      flat on Sam's placement (out bar numbers); what each side has there:")
            for st in pb.content_check(o, n, *sam)[0]:
                if st["flat"]:
                    out("        out bars %g-%g: outgoing carries=%s, incoming carries=%s (incoming bars %g-%g)" % (
                        st["start"], st["end"], st["out"], st["in"], st["start"] - a, st["end"] - a))
            if mode == "MEASURED":
                co, ci = content["tracks"][b["out"]], content["tracks"][b["in"]]
                out("        outgoing %s in %g-%g: bass %s | music %s" % (
                    b["out"][:20], a, e,
                    _runs(lambda x: x < len(co["has_bass"]) and co["has_bass"][x], a, e),
                    _runs(lambda x: x < len(co["has_music"]) and co["has_music"][x], a, e)))
                out("        incoming %s in %g-%g: bass %s | music %s" % (
                    b["in"][:20], 0, e - a,
                    _runs(lambda j: j < len(ci["has_bass"]) and ci["has_bass"][j], 0, e - a),
                    _runs(lambda j: j < len(ci["has_music"]) and ci["has_music"][j], 0, e - a)))
        if top or not ok:
            allc = pb.candidates(o, n)
            for c in allc[:max(top, 3)]:
                out("      cand %-26s cost %6.2f flat %g loop %g/%g" % (
                    c.key(), c.cost, c.flat_bars, c.out_looped_bars, c.in_intro_looped_bars))
            sc = [c for c in allc if all(abs(x - y) < 1e-6 for x, y in zip(c.key(), sam))]
            if sc:
                out("      Sam's plan is candidate rank %d, cost %.2f, flat %g" % (
                    allc.index(sc[0]) + 1, sc[0].cost, sc[0].flat_bars))
            else:
                out("      Sam's plan is NOT in the candidate set")
    out("TOTAL %s: %d of %d exact" % (mode, exact, len(rows)))
    return exact, rows


def table(out=print):
    res = {m: score(m, out=lambda *_: None)[1] for m in MODES}
    out("")
    out("blend | " + " | ".join("%-27s" % m for m in MODES))
    out("      | " + " | ".join("%-27s" % "result (flat bars on Sam)" for _ in MODES))
    for i in range(6):
        cells = []
        for m in MODES:
            r = res[m][i]
            cells.append("%-27s" % ("%s (%g)" % ("EXACT" if r["ok"] else "MISS %g/%g/%g" % r["mine"], r["sam_flat"])))
        out("%5d | %s" % (i + 1, " | ".join(cells)))
    out("total | " + " | ".join("%-27s" % ("%d of 6" % sum(r["ok"] for r in res[m])) for m in MODES))


if __name__ == "__main__":
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 0
    detail = "--detail" in sys.argv
    for m in MODES:
        score(m, top, detail)
    table()
