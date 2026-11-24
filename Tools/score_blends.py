"""Score phrase_blend.plan_blend against the seven blends in the DFTD answer key.

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


def find_data_file(root, rel, pattern):
    """Locate a data file relative to the repo root (round 8, fix 9): the canonical path first,
    then a recursive glob under Documentation/ - the scorer must find the key wherever the repo
    layout puts it, and never read paths relative to the caller's working directory."""
    p = root / rel
    if p.exists():
        return p
    hits = sorted((root / "Documentation").rglob(pattern)) if (root / "Documentation").exists() else []
    return hits[0] if hits else p


KEY = find_data_file(ROOT, Path("Documentation") / "Golden Sections" / "Answer Key 2026-11-20 DFTD.json",
                     "Answer Key*.json")
CONTENT = find_data_file(ROOT, Path("Documentation") / "Golden Sections" / "Content 2026-11-20 DFTD.json",
                         "Content*.json")

VOCAL_KEY = find_data_file(ROOT, Path("Documentation") / "Golden Sections" / "Vocal Key 2026-11-24 DFTD.json",
                           "Vocal Key*.json")

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
    for s in t.get("sections") or []:
        start = pb._num(s[0]) if isinstance(s, list) and len(s) >= 2 else None
        if start in ov:
            del s[2:]
            s.append(ov[start])
    return t


def prepare(track, name, mode, content):
    """mode: a column name, or one of 'measured' / 'labels' / 'overrides'."""
    source = MODES[mode][0] if mode in MODES else mode.lower()
    if source == "overrides":
        return apply_overrides(track, name, OVERRIDES)
    t = json.loads(json.dumps(track))
    if source == "measured":
        c = (content.get("tracks") or {}).get(name)   # a missing track falls back to labels
        if c:
            t["content"] = {k: c[k] for k in ("has_bass", "has_music", "has_kick") if k in c}
            _add_vocal_key(t, name)
    return t


def _add_vocal_key(track, name):
    """Optional per-bar vocal flags and a Camelot key for a measured track (Tools/measure_vocal_key.py).
    A missing file or a track with no entry changes nothing.  A file or entry that cannot be used (bad JSON,
    not an object, flags that are not a list, a key that is not text) is left out and a note goes on
    track["input_notes"], which score() prints: it never crashes.  Text that is a key or a flag list but not
    a usable one reaches the planner, which reports "key unknown" or an unusable list on the plan."""
    notes = track.setdefault("input_notes", [])
    try:
        tracks = json.loads(VOCAL_KEY.read_text(encoding="utf-8")).get("tracks")
    except FileNotFoundError:
        del track["input_notes"]
        return
    except (OSError, ValueError, AttributeError) as exc:
        notes.append("vocal and key file %s is unusable (%s): no vocal or key data" % (VOCAL_KEY.name, exc))
        return
    if not isinstance(tracks, dict):
        notes.append("vocal and key file %s has no tracks object: no vocal or key data" % VOCAL_KEY.name)
        return
    data = tracks.get(name)
    if data is None:
        del track["input_notes"]
        return
    if not isinstance(data, dict):
        notes.append("vocal and key entry for %s is not an object: ignored" % name)
        return
    flags, key = data.get("has_vocal"), data.get("key")
    if flags:
        if isinstance(flags, list):
            track["content"]["has_vocal"] = flags
        else:
            notes.append("vocal flags for %s are not a list: ignored" % name)
    if key is not None:
        if isinstance(key, str):
            track["key"] = key
        else:
            notes.append("key for %s is not text (%r): ignored" % (name, key))
    if not notes:
        del track["input_notes"]


def sam_numbers(blend, out_t):
    e = blend.get("out_looped_to_bar")
    if e is None:
        e = blend.get("out_cut_at_bar")
    if e is None:
        e = pb.natural_end_bar(out_t)
    return (blend["in_starts_at_out_bar"], blend["bass_switch_out_bar"], e)


def matches(plan, blend, sam):
    """None, "primary", or "acceptable #i": does the plan equal the owner's answer, or one of his
    `also_acceptable` placements? Compare the timeline bars, the incoming loop length and the
    incoming track's source bar at the swap. An alternative may leave its exit unspecified."""
    if plan is None:
        return None
    if plan.in_intro_looped_bars != 0 and plan.in_loop_phrase_end <= 0:
        return None                            # an unbounded incoming repeat is a different plan
    if _matches_placement(plan, blend, *sam):
        return "primary"
    for i, alt in enumerate(blend.get("also_acceptable", []), 1):
        if _matches_placement(plan, alt, alt["in_starts_at_out_bar"],
                              alt["bass_switch_out_bar"], alt.get("out_end_bar")):
            return "acceptable #%d" % i
    return None


def _matches_placement(plan, placement, entry, swap, end):
    loop = placement.get("in_intro_looped_bars", 0.0)
    cut = placement.get("in_intro_cut_bars", 0.0)
    source_swap = placement.get("swap_incoming_bar", swap - entry - loop + cut)
    return (abs(plan.in_starts_at_out_bar - entry) < 1e-6
            and abs(plan.bass_switch_out_bar - swap) < 1e-6
            and (end is None or abs(plan.out_end_bar - end) < 1e-6)
            and abs(plan.in_intro_looped_bars - loop) < 1e-6
            and abs(plan.swap_incoming_bar - source_swap) < 1e-6)


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
    if not (pb._valid(o) and pb._valid(n)):
        return None
    return pb._score(o, n, sam[0], sam[1], sam[2], 0.0, 0.0, pb.phrase_points(o), pb.phrase_points(n), r6)


def _breakdown(label, plan):
    t = plan.terms
    parts = " ".join("%s=%.2f" % (k, t[k]) for k in sorted(t))
    return "      %-14s total %7.2f :: %s" % (label, plan.cost, parts or "all terms zero")


def _read_content():
    try:
        return json.loads(CONTENT.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def score(mode, top=0, detail=False, out=print):
    key = json.loads(KEY.read_text(encoding="utf-8"))
    content = _read_content()
    r6 = MODES[mode][1]
    exact, rows = 0, []
    out("--- %s (content: %s, R6 %s) ---" % (mode, MODES[mode][0], r6))
    for i, b in enumerate(key["blends"], 1):
        o = prepare(key["tracks"][b["out"]], b["out"], mode, content)
        n = prepare(key["tracks"][b["in"]], b["in"], mode, content)
        why = []
        plan = pb.plan_blend(o, n, r6=r6, why=why)
        sam = sam_numbers(b, o)
        mine = plan.key() if plan else (None, None, None)
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
        for note in o.get("input_notes", []) + n.get("input_notes", []):
            out("   input note: %s" % note)
        if plan is None:
            out("   planner: NO PLAN" + (" - " + ". ".join(why) if why else ""))
            continue
        out("   planner: in %g  switch %g  end %g  cost %.2f  flat runs: %s" % (
            mine + (plan.cost, _fmt_runs(plan.flat_runs))))
        out("   Sam    : in %g  switch %g  end %g  cost %.2f  flat runs: %s   -> %s" % (
            sam + (sp.cost, _fmt_runs(sp.flat_runs), ("RIGHT (%s)" % which) if ok else "MISS")))
        out("      owner's primary answer in the top 3 candidates: %s" % ("yes" if primary_top3 else "no"))
        allc = pb.candidates(o, n, r6=r6)
        sane_c = [c for c in allc if c.sane]
        own_rank = next((j for j, c in enumerate(sane_c, 1) if matches(c, b, sam) == "primary"), None)
        out("      owner's primary among the %d sane pairings: %s; types: A bar %g = %s, B bar %g = %s" % (
            len(sane_c), ("rank %d" % own_rank) if own_rank else "not a sane pairing",
            sam[1], pb.point_type(o, sam[1]) or "NONE", sam[1] - sam[0],
            pb.point_type(n, max(sam[1] - sam[0], 0.0)) or "NONE"))
        if r6 == pb.R6_BE and which != "primary":
            diff = {k_: sp.terms.get(k_, 0.0) - plan.terms.get(k_, 0.0) for k_ in set(sp.terms) | set(plan.terms)}
            out("      terms separating the owner's primary from the top pick (owner minus top): " + " ".join(
                "%s=%+.2f" % (k_, diff[k_]) for k_ in sorted(diff) if abs(diff[k_]) > 1e-9))
        pool = [plan] + [a.plan for a in plan.alternatives]
        for j, ac in enumerate(b.get("also_acceptable", []), 1):
            hit = any(_matches_placement(q, ac, ac["in_starts_at_out_bar"],
                                         ac["bass_switch_out_bar"], ac.get("out_end_bar")) for q in pool)
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
            a_, s_, e_ = sam
            rr = lambda tr, key, lo, hi: _runs(lambda x: x < len(tr[key]) and tr[key][x], lo, hi)
            out("      measured on Sam's placement (out bars %g-%g, switch %g; incoming bars 0-%g):" % (a_, e_, s_, e_ - a_))
            for side, track, lo, hi in (("outgoing", o, a_, e_), ("incoming", n, 0, e_ - a_)):
                if pb._measured(track, 0, pb.R6_BE) is None:
                    out("        %s measurements unavailable. Using labels." % side)
                    continue
                runs = [rr(track["content"], key, lo, hi) for key in ("has_kick", "has_bass", "has_music")]
                out("        %s kick %s | bass %s | music %s" % (side, *runs))
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
    """Compute both ablations on the answer key."""
    key = json.loads(KEY.read_text(encoding="utf-8"))
    content = _read_content()
    gaps = [b["bass_switch_out_bar"] - b["in_starts_at_out_bar"] for b in key["blends"]]
    base = _picks(key, content)
    saved = pb.W_FLAT_RUN
    pb.W_FLAT_RUN = 0.0
    try:
        flat_off = _picks(key, content)
    finally:
        pb.W_FLAT_RUN = saved
    out("HEADLINE (computed now):")
    nb = len(gaps)
    first = gaps[:6]
    out("  switch minus entry per owner blend: %s -> the first six %s; blends 7+ %s" % (
        gaps, "ALL EXACTLY %g bars (they say almost nothing about the switch rule)" % first[0]
        if len(set(first)) == 1 else "are not all equal", "differ" if len(set(gaps)) > 1 else "agree"))
    no_content = _picks(key, {})
    hits = lambda picks: sum(match is not None for _, match in picks)
    misses = ", ".join(str(i) for i, (_, match) in enumerate(no_content, 1) if match is None)
    out("  BOTTOM END score %d of %d. With W_FLAT_RUN = 0: %d of %d. The bottom-end cost %s the top picks." % (
        hits(base), nb, hits(flat_off), nb, "is INERT for" if base == flat_off else "changes"))
    out("  BOTTOM END with measured content off: %d of %d (misses: %s)." %
        (hits(no_content), nb, misses or "none"))
    out("")


def _picks(key, content):
    picks = []
    for b in key["blends"]:
        o = prepare(key["tracks"][b["out"]], b["out"], "BOTTOM END", content)
        n = prepare(key["tracks"][b["in"]], b["in"], "BOTTOM END", content)
        plan = pb.plan_blend(o, n, r6=pb.R6_BE)
        picks.append((plan.full_key() if plan else None, matches(plan, b, sam_numbers(b, o))))
    return picks


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
                i + 1, m, _fmt_runs(r["sam_plan"].flat_runs) if r["sam_plan"] else "unusable track",
                _fmt_runs(r["plan"].flat_runs) if r["plan"] else "no plan"))


if __name__ == "__main__":
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 0
    detail = "--detail" in sys.argv
    headline()
    for m in MODES:
        score(m, top, detail)
    table()
