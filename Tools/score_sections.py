"""Score stem-detector section outputs against the owner's section answer key.

Usage:
    python Tools/score_sections.py --results "<dir with SECTIONS_STEM_*.json>" \
        [--key "Documentation/Golden Sections/Answer Key 2026-11-20 DFTD.json"] \
        [--out score.json] [--detail] [--allow-partial]

What is scored, per key track (and summed):
  lines   the key's section starts after bar 0 against the detector's section
          starts after its first section. One-to-one, order-preserving, the
          most matches possible (then the smallest total distance).
          found1 = within 1 bar, found0 = within one beat (0.25 bar),
          missed = key lines with no detector line within 1 bar,
          extra  = detector lines with no key line within 1 bar.
  labels  for every line matched within 1 bar, does the detector's label equal
          the key's? (The owner confirmed positions; the key's labels are
          Claude's, so this is reported, not a pass mark.)
  fills   ONLY the detector's own fill output (signals.fill_sections, else
          signals.fills) counts. One-to-one by overlap: a detector fill can find
          one key fill. extra = detector fills that found none. fills_exact =
          found fills whose both edges are within one beat of the key's.
          fills_gap_only = key fills overlapped only by a kick gap
          (kick_dropout -> kick_return), reported for information, not credit.
  bar1    the detector's bar 0 minus the key's first beat, in bars, from
          analysis.downbeat_sec (else the first section's start); plus the
          detector's own bar_one_suspect offset when it reports one.

Track identity is EXACT: a result matches a key track only when its name is
the key name, or the key name followed by a version tag ("24 Bit MASTER",
"16 Bit MASTER AMENDED V2", "SW V1", ...). A remix or another version is a
different track and never matches. Two candidates for one key track is an
error. Missing results exit 3 unless --allow-partial.

Everything is compared in SECONDS converted to the key's bars with the key's
bpm and lead-in, so a grid that starts on the wrong beat shows on every line.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_KEY = (Path(__file__).resolve().parent.parent / "Documentation" / "Golden Sections"
               / "Answer Key 2026-11-20 DFTD.json")
LINE_TOL_BARS = 1.0       # the owner's tolerance: a line within one bar is the same line
EXACT_TOL_BARS = 0.25     # "on the beat" = within one beat
_EPS = 1e-6
_MASTER = r"(\d+ ?[Bb]it )?MASTER( AMENDED( V\d+)?)?"
VERSION_TAG = re.compile(rf"^({_MASTER}|SW V\d+( {_MASTER})?)$")   # nothing after the tag: a
                                                                   # "(X Remix)" suffix is another track


def match_lines(key_bars, det_bars, tol):
    """Order-preserving one-to-one matching: most pairs, then least distance.
    Returns (pairs, missed, extra)."""
    k = sorted(float(x) for x in key_bars)
    d = sorted(float(x) for x in det_bars)
    n, m = len(k), len(d)
    best = [[(0, 0.0)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cand = [best[i - 1][j], best[i][j - 1]]
            dist = abs(k[i - 1] - d[j - 1])
            if dist <= tol + _EPS:
                c, s = best[i - 1][j - 1]
                cand.append((c + 1, s - dist))
            best[i][j] = max(cand)
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        dist = abs(k[i - 1] - d[j - 1])
        c, s = best[i - 1][j - 1]
        if dist <= tol + _EPS and best[i][j] == (c + 1, s - dist):
            pairs.append((k[i - 1], d[j - 1]))
            i, j = i - 1, j - 1
        elif best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    used_k = [p[0] for p in pairs]
    used_d = [p[1] for p in pairs]
    missed = [x for x in k if not _take(used_k, x)]
    extra = [x for x in d if not _take(used_d, x)]
    return pairs, missed, extra


def _take(pool, x):
    for i, y in enumerate(pool):
        if y == x:
            del pool[i]
            return True
    return False


def _overlap(a, b):
    return min(a[1], b[1]) - max(a[0], b[0])


def match_fills(key_fills, det_fills):
    """One-to-one by overlap, largest overlaps first. Returns list of (key, det)."""
    cands = sorted(((_overlap(kf, df), i, j) for i, kf in enumerate(key_fills)
                    for j, df in enumerate(det_fills) if _overlap(kf, df) > _EPS), reverse=True)
    used_k, used_d, pairs = set(), set(), []
    for _, i, j in cands:
        if i in used_k or j in used_d:
            continue
        used_k.add(i)
        used_d.add(j)
        pairs.append((key_fills[i], det_fills[j]))
    return pairs


def _kick_gaps(cues):
    gaps, open_start = [], None
    for c in sorted(cues, key=lambda c: c.get("start_sec", 0.0)):
        if c.get("type") == "kick_dropout":
            open_start = c["start_sec"]
        elif c.get("type") == "kick_return" and open_start is not None:
            gaps.append((open_start, c["start_sec"]))
            open_start = None
    return gaps


def detector_downbeat_sec(res) -> float:
    analysis = res.get("analysis") or {}
    if isinstance(analysis.get("downbeat_sec"), (int, float)):
        return float(analysis["downbeat_sec"])
    secs = res.get("sections") or []
    return float(secs[0]["start_sec"]) - float(secs[0]["start_bar"]) * 240.0 / float(res["bpm"]) if secs else 0.0


def detector_fills_sec(res):
    sig = res.get("signals") or {}
    if isinstance(sig.get("fill_sections"), list):
        return [(float(f["start_sec"]), float(f["end_sec"])) for f in sig["fill_sections"]]
    return [(float(a), float(b)) for a, b in sig.get("fills", [])]


def score_track(key_track: dict, res: dict) -> dict:
    spb = 240.0 / float(key_track["bpm"])
    lead = float(key_track.get("lead_in_bars", 0.0)) * spb
    to_bar = lambda sec: (float(sec) - lead) / spb

    key_lines = [float(b) for b, _ in key_track["sections"] if float(b) > 0]
    key_label = {float(b): str(lab).upper() for b, lab in key_track["sections"]}
    det_secs = [(round(to_bar(s["start_sec"]), 3), str(s.get("label", "")).upper()) for s in res["sections"]]
    det_lines = [b for b, _ in det_secs[1:]]
    det_label = dict(det_secs)
    pairs1, missed, extra = match_lines(key_lines, det_lines, LINE_TOL_BARS)
    pairs0, _, _ = match_lines(key_lines, det_lines, EXACT_TOL_BARS)
    label_pairs = [(k, d) for k, d in pairs1]
    label_agree = sum(1 for k, d in label_pairs if key_label.get(k) == det_label.get(d))
    label_disagree = [[k, key_label.get(k), det_label.get(d)] for k, d in label_pairs
                      if key_label.get(k) != det_label.get(d)]

    det_fills = [(round(to_bar(a), 3), round(to_bar(b), 3)) for a, b in detector_fills_sec(res)]
    key_fills = [(float(a), float(b)) for a, b in key_track.get("fills", [])]
    fpairs = match_fills(key_fills, det_fills)
    found_k = [kf for kf, _ in fpairs]
    found_d_idx = set()
    for _, df in fpairs:                  # by position, so duplicate intervals cannot hide
        found_d_idx.add(next(i for i, d in enumerate(det_fills) if d == df and i not in found_d_idx))
    exact = sum(1 for kf, df in fpairs
                if abs(kf[0] - df[0]) <= EXACT_TOL_BARS + _EPS and abs(kf[1] - df[1]) <= EXACT_TOL_BARS + _EPS)
    gaps = [(round(to_bar(a), 3), round(to_bar(b), 3))
            for a, b in _kick_gaps((res.get("signals") or {}).get("kick_cues", []))]
    missed_fills = [kf for kf in key_fills if kf not in found_k]
    gap_only = [kf for kf in missed_fills if any(_overlap(kf, g) > _EPS for g in gaps)]
    extra_fills = [df for i, df in enumerate(det_fills) if i not in found_d_idx]

    sus = (res.get("analysis") or {}).get("bar_one_suspect") or {}
    return {
        "lines_key": len(key_lines),
        "lines_det": len(det_lines),
        "found1": len(pairs1),
        "found0": len(pairs0),
        "missed": len(missed),
        "extra": len(extra),
        "labels_agree": label_agree,
        "labels_checked": len(label_pairs),
        "fills_key": len(key_fills),
        "fills_found": len(fpairs),
        "fills_exact": exact,
        "fills_missed": len(missed_fills),
        "fills_extra": len(extra_fills),
        "fills_gap_only": len(gap_only),
        "bar1_offset": round(to_bar(detector_downbeat_sec(res)), 3),
        "bar1_suspect_offset": sus.get("offset_bars") if sus.get("suspect") else None,
        "replay_adjust_beats": (res.get("analysis") or {}).get("replay_downbeat_adjust_beats"),
        "detail": {
            "key_lines": key_lines,
            "det_lines": det_lines,
            "matched_within_1": [list(p) for p in pairs1],
            "missed_lines": missed,
            "extra_lines": extra,
            "label_disagree": label_disagree,
            "missed_fills": [list(f) for f in missed_fills],
            "gap_only_fills": [list(f) for f in gap_only],
            "extra_fills": [list(f) for f in extra_fills],
            "det_labels": [[b, lab] for b, lab in det_secs],
        },
    }


def track_matches(stem: str, name: str) -> bool:
    if stem == name:
        return True
    return stem.startswith(name + " ") and bool(VERSION_TAG.match(stem[len(name) + 1:]))


def find_result(results_dir: Path, track_name: str):
    hits = sorted(p for p in results_dir.glob("SECTIONS_STEM_*.json")
                  if track_matches(p.stem[len("SECTIONS_STEM_"):], track_name))
    if len(hits) > 1:
        raise ValueError(f"{len(hits)} results match key track {track_name!r}: "
                         f"{[h.name for h in hits]}")
    return hits[0] if hits else None


SUM_KEYS = ("lines_key", "lines_det", "found1", "found0", "missed", "extra", "labels_agree",
            "labels_checked", "fills_key", "fills_found", "fills_exact", "fills_missed",
            "fills_extra", "fills_gap_only")


def score_all(key: dict, results_dir: Path) -> dict:
    tracks, missing = {}, []
    for name, kt in key["tracks"].items():
        path = find_result(results_dir, name)
        if path is None:
            missing.append(name)
            continue
        res = json.loads(path.read_text(encoding="utf-8"))
        if "track" in res and not track_matches(str(res["track"]), name):
            raise ValueError(f"{path.name} says it is track {res['track']!r}, not {name!r}")
        tracks[name] = score_track(kt, res)
    total = {k: sum(t[k] for t in tracks.values()) for k in SUM_KEYS}
    total["tracks"] = len(tracks)
    return {"tracks": tracks, "total": total, "missing": missing}


def format_table(scored: dict) -> str:
    head = (f"{'track':34} {'lines':>5} {'f<=1':>5} {'f=0':>4} {'miss':>5} {'xtra':>5} {'lab':>5} | "
            f"{'fills':>5} {'found':>5} {'exact':>5} {'miss':>5} {'xtra':>5} {'gap':>4} | {'bar1':>6} {'sus':>5}")
    rows = [head, "-" * len(head)]

    def row(name, t, extra_cols=True):
        sus = "" if t.get("bar1_suspect_offset") is None else f"{t['bar1_suspect_offset']:.2f}"
        b1 = f"{t['bar1_offset']:6.2f}" if extra_cols else " " * 6
        return (f"{name[:34]:34} {t['lines_key']:5d} {t['found1']:5d} {t['found0']:4d} {t['missed']:5d} "
                f"{t['extra']:5d} {str(t['labels_agree']) + '/' + str(t['labels_checked']):>5} | "
                f"{t['fills_key']:5d} {t['fills_found']:5d} {t['fills_exact']:5d} {t['fills_missed']:5d} "
                f"{t['fills_extra']:5d} {t['fills_gap_only']:4d} | {b1} {sus:>5}")
    for name, t in scored["tracks"].items():
        rows.append(row(name, t))
    rows.append("-" * len(head))
    t = scored["total"]
    rows.append(row(f"TOTAL ({t['tracks']} tracks)", dict(t, bar1_offset=0.0), extra_cols=False))
    for m in scored["missing"]:
        rows.append(f"[missing result] {m}")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--key", type=Path, default=DEFAULT_KEY)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--detail", action="store_true", help="print missed/extra per track")
    ap.add_argument("--allow-partial", action="store_true")
    args = ap.parse_args(argv)
    key = json.loads(args.key.read_text(encoding="utf-8"))
    scored = score_all(key, args.results)
    print(format_table(scored))
    if args.detail:
        for name, t in scored["tracks"].items():
            d = t["detail"]
            print(f"\n{name}\n  missed lines {d['missed_lines']}  extra lines {d['extra_lines']}"
                  f"\n  label disagreements {d['label_disagree']}"
                  f"\n  missed fills {d['missed_fills']} (gap-only {d['gap_only_fills']})  extra fills {d['extra_fills']}"
                  f"\n  detector {d['det_labels']}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(scored, indent=1), encoding="utf-8")
    if scored["missing"] and not args.allow_partial:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
