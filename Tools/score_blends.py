"""Score phrase_blend.plan_blend against the six blends in the DFTD answer key.

Usage:  python Tools/score_blends.py [--no-overrides] [--top N]

Prints, per blend, planner vs Sam for in_start / bass_switch / out_end and a total
"N of 6 exact".  Run twice by default: with the `carries` overrides below, and without.
The answer key file is read only, never edited.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
import phrase_blend as pb  # noqa: E402

KEY = ROOT / "Documentation" / "Golden Sections" / "Answer Key 2026-11-20 DFTD.json"

# Per-section `carries` overrides, applied to a COPY of the key data (the key is not edited).
# Why each exists: R6 derives "carries bass or music" from labels only (INTRO and OUTRO do not
# carry).  Read that way it calls blends 3, 4 and 6 flat, yet Sam confirmed them.  His words:
#   - Ninetoes OUTRO carries vocal chops (brief note on blend 4)
#   - an incoming with full kick and bass from bar one is fine (R7), and blend 3 was
#     "exactly how I do that blend" with Ninetoes' INTRO (bars 0-32) under it
#   - blend 6 "bang on" puts Call 911's INTRO (bars 0-32) under an outgoing with no bass left
# These are INFERRED from Sam's acceptance, not measured.  [OPEN] ask Sam to confirm each.
OVERRIDES = {
    "Ninetoes - Come Back": {0.0: True, 160.0: True},
    "Adam Ten & Rafael - Call 911": {0.0: True},
}


def apply_overrides(track, name, overrides):
    t = json.loads(json.dumps(track))
    ov = overrides.get(name, {})
    for s in t["sections"]:
        if float(s[0]) in ov:
            del s[2:]
            s.append(ov[float(s[0])])
    return t


def sam_numbers(blend, out_t):
    e = blend.get("out_looped_to_bar")
    if e is None:
        e = blend.get("out_cut_at_bar")
    if e is None:
        e = out_t["n_bars"]
    return (blend["in_starts_at_out_bar"], blend["bass_switch_out_bar"], e)


def score(use_overrides=True, top=0, out=print):
    key = json.loads(KEY.read_text(encoding="utf-8"))
    ov = OVERRIDES if use_overrides else {}
    exact, rows = 0, []
    out("--- carries overrides: %s ---" % ("ON" if use_overrides else "OFF"))
    for i, b in enumerate(key["blends"], 1):
        o = apply_overrides(key["tracks"][b["out"]], b["out"], ov)
        n = apply_overrides(key["tracks"][b["in"]], b["in"], ov)
        plan = pb.plan_blend(o, n)
        sam = sam_numbers(b, o)
        mine = plan.key() if plan else (None, None, None)
        ok = plan is not None and all(abs(x - y) < 1e-6 for x, y in zip(mine, sam))
        exact += ok
        rows.append((i, b, plan, sam, ok))
        out("%d. %s -> %s" % (i, b["out"][:34], b["in"][:34]))
        if plan:
            out("   planner: in %g  switch %g  end %g   (loop %g, in-loop %g, flat %g, cost %.2f)" % (
                mine[0], mine[1], mine[2], plan.out_looped_bars, plan.in_intro_looped_bars,
                plan.flat_bars, plan.cost))
        else:
            out("   planner: NO PLAN")
        out("   Sam    : in %g  switch %g  end %g   -> %s" % (sam + ("EXACT" if ok else "MISS",)))
        if top or not ok:
            allc = pb.candidates(o, n)
            for c in allc[:max(top, 4)]:
                out("      cand %-26s cost %6.2f flat %g loop %g/%g" % (
                    c.key(), c.cost, c.flat_bars, c.out_looped_bars, c.in_intro_looped_bars))
            sc = [c for c in allc if all(abs(x - y) < 1e-6 for x, y in zip(c.key(), sam))]
            if sc:
                out("      Sam's plan in candidates: rank %d cost %.2f flat %g :: %s" % (
                    allc.index(sc[0]) + 1, sc[0].cost, sc[0].flat_bars, "; ".join(sc[0].why)))
            else:
                out("      Sam's plan is NOT in the candidate set")
    out("TOTAL: %d of %d exact (overrides %s)" % (exact, len(rows), "ON" if use_overrides else "OFF"))
    return exact, rows


if __name__ == "__main__":
    top = 0
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
    if "--no-overrides" in sys.argv:
        score(False, top)
    else:
        score(True, top)
        score(False, top)
