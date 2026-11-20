"""Sensitivity of each scoring pass to plus or minus 25 percent on every constant in phrase_blend.

Usage:  python Tools/blend_sensitivity.py [--pct 25]

For each column of Tools/score_blends.py (MEASURED, LABELS, OVERRIDES, BOTTOM END) and each
constant, set the constant to 75 and 125 percent of its value, recompute every owner blend in the key and print
the exact count, plus the blends that miss when it differs from the baseline.  A constant whose
change flips a blend is one the six cases are balanced on; a constant that never matters is one
they do not test.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
sys.path.insert(0, str(ROOT / "Tools"))
import phrase_blend as pb  # noqa: E402
import score_blends as sb  # noqa: E402

CONSTANTS = ["W_FLAT", "W_CUT", "W_LOOP", "W_ENTRY_SECTION", "W_ENTRY_OFF", "W_SWITCH_NONE", "W_LEN",
             "W_SWITCH_TIME", "W_MINUTE_OFF", "FREE_FLAT_BARS", "W_FLAT_RUN", "MIX_ZONE_SECONDS",
             "MINUTE_TOL_BARS", "END_SLACK_BARS", "MIN_SIDE_BARS", "MAX_TAIL_LOOP", "MIN_IN_LOOP",
             "MIN_LOOP_SOURCE", "MAX_LOOP_SOURCE", "LOOP_SOURCE_KICK", "W_SWAP_KICK_ONLY", "W_LATE_SWAP",
             "LATE_SWAP_SECONDS", "W_TRIM", "MAX_TRIM_BARS", "ENTRY_MAX_SECONDS", "ANCHOR_TOL_BARS",
             "BASS_IN_FRACTION", "ALT_MARGIN"]


def vector(mode, key, content):
    r6 = sb.MODES[mode][1]
    out = []
    for b in key["blends"]:
        o = sb.prepare(key["tracks"][b["out"]], b["out"], mode, content)
        n = sb.prepare(key["tracks"][b["in"]], b["in"], mode, content)
        p = pb.plan_blend(o, n, r6=r6)
        sam = sb.sam_numbers(b, o)
        out.append(sb.matches(p, b, sam) is not None)
    return out


def run(pct=25, out=print):
    key = json.loads(sb.KEY.read_text(encoding="utf-8"))
    content = json.loads(sb.CONTENT.read_text(encoding="utf-8"))
    base = {m: vector(m, key, content) for m in sb.MODES}
    out("baseline exact counts: " + "  ".join("%s %d" % (m, sum(v)) for m, v in base.items()))
    flips = []
    for name in CONSTANTS:
        orig = getattr(pb, name)
        for f in (1 - pct / 100.0, 1 + pct / 100.0):
            setattr(pb, name, orig * f)
            try:
                for m in sb.MODES:
                    v = vector(m, key, content)
                    if v != base[m]:
                        miss = [i + 1 for i, x in enumerate(v) if not x]
                        flips.append((name, f, m, sum(v), miss))
            finally:
                setattr(pb, name, orig)
    out("")
    if not flips:
        out("no constant changes any column at +-%d%%" % pct)
    for name, f, m, n, miss in flips:
        out("%-18s x%.2f  %-11s -> %d of %d (misses: blends %s)" % (name, f, m, n, len(key["blends"]), miss))
    never = [c for c in CONSTANTS if not any(x[0] == c for x in flips)]
    out("")
    out("no effect on any column at +-%d%%: %s" % (pct, ", ".join(never)))
    return flips


if __name__ == "__main__":
    pct = int(sys.argv[sys.argv.index("--pct") + 1]) if "--pct" in sys.argv else 25
    run(pct)
