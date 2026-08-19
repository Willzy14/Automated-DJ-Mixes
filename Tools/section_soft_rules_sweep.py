"""Flag-ON/OFF sweep across the cached 14.08.26 corpus.

Read-only: always make_viz=False, write_json=False. Compares per-track sections
under soft_intro_outro=False (OFF, byte-identical to main @ 88f15c4) and
soft_intro_outro=True (ON, applies R2/R3/R4 soft rules).

Usage:
    PYTHONPATH=Source python Tools/section_soft_rules_sweep.py --off
    PYTHONPATH=Source python Tools/section_soft_rules_sweep.py --on
    PYTHONPATH=Source python Tools/section_soft_rules_sweep.py --diff
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import stem_detector

CORPUS = Path("Test Project/14.08.26")
STEM_DIR = CORPUS / "_Stem Analysis"


def sweep_all(soft_intro_outro: bool) -> dict:
    out = {}
    for jp in sorted(STEM_DIR.glob("SECTIONS_STEM_*.json")):
        track = jp.stem[len("SECTIONS_STEM_"):]
        cached = json.loads(jp.read_text(encoding="utf-8"))
        bpm = cached["bpm"]
        downbeat = cached["sections"][0]["start_sec"] - cached["sections"][0]["start_bar"] * (4 * 60.0 / bpm)
        wav = CORPUS / "Audio" / f"{track}.wav"
        res = stem_detector.detect(
            wav, CORPUS, bpm=bpm, downbeat=downbeat,
            make_viz=False, write_json=False,
            soft_intro_outro=soft_intro_outro,
        )
        if res is None:
            continue
        secs = res["sections"]
        out[track] = {
            "n_bars": res["n_bars"],
            "sections": [(s["start_bar"], s["end_bar"], s["label"]) for s in secs],
            "soft_hints": res["signals"].get("soft_intro_outro_hints"),
        }
    return out


def fmt(secs):
    return " ".join(f"{lab}{end - start}" for start, end, lab in secs)


def print_table(data: dict, label: str):
    print(f"=== {label} ({len(data)} tracks) ===")
    for track, d in data.items():
        summary = fmt(d["sections"])
        print(f"{track[:60]:60} | n={d['n_bars']:4d} | {summary}")


def diff_sections(a: dict, b: dict) -> list:
    changed = []
    for track in sorted(set(a.keys()) | set(b.keys())):
        a_secs = a.get(track, {}).get("sections", [])
        b_secs = b.get(track, {}).get("sections", [])
        if a_secs != b_secs:
            changed.append(track)
    return changed


def print_diff(a: dict, b: dict, a_label="OFF", b_label="ON"):
    print(f"=== DIFF ({a_label} vs {b_label}) ===")
    changed = diff_sections(a, b)
    if not changed:
        print("NO CHANGES.")
        return
    for track in changed:
        a_secs = a.get(track, {}).get("sections", [])
        b_secs = b.get(track, {}).get("sections", [])
        print(f"\n--- {track} ---")
        print(f"  {a_label}: {fmt(a_secs)}")
        print(f"  {b_label}: {fmt(b_secs)}")
        for i, (a_s, b_s) in enumerate(zip(a_secs, b_secs)):
            if a_s != b_s:
                print(f"    [{i}] {a_label}={a_s} {b_label}={b_s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--off", action="store_true",
                    help="Sweep with soft_intro_outro=False (default behaviour).")
    ap.add_argument("--on", action="store_true",
                    help="Sweep with soft_intro_outro=True (R2/R3/R4 active).")
    ap.add_argument("--diff", action="store_true",
                    help="Print OFF vs ON diff.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Write sweep JSON to this file.")
    args = ap.parse_args()

    off = sweep_all(False)
    on = sweep_all(True)

    if args.off:
        print_table(off, "OFF (soft_intro_outro=False, byte-identical to main @ 88f15c4)")
        if args.out:
            args.out.write_text(json.dumps(off, indent=1, default=str))
    if args.on:
        print_table(on, "ON (soft_intro_outro=True, R2/R3/R4 active)")
        if args.out:
            args.out.write_text(json.dumps(on, indent=1, default=str))
    if args.diff:
        print_diff(off, on)


if __name__ == "__main__":
    main()
