"""Side-by-side comparison: MIK 11 auto-cue points vs our V8 cue candidates.

Reads MIK cues from file tags + DB, reads our candidates from the CSV reports,
and shows where they agree / disagree per track.

Usage:
    python Source/compare_mik_vs_candidates.py
"""

from __future__ import annotations

import csv
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from pathlib import Path
from automated_dj_mixes.mik_reader import enrich_from_mik

AUDIO_DIR = Path(r"C:\Users\Carillon\Wired Masters Dropbox\Sam Wills\0.1---GIT HUB---\Automated DJ Mixes\Test Project\May 2026 Mix\Audio")
REPORTS_DIR = Path(r"C:\Users\Carillon\Wired Masters Dropbox\Sam Wills\0.1---GIT HUB---\Automated DJ Mixes\Test Project\May 2026 Mix\Output\Reports")
MATCH_TOLERANCE_SEC = 4.0  # cues within this window count as "aligned"


def _fmt_time(sec: float) -> str:
    m = int(sec // 60)
    s = sec % 60
    return f"{m}:{s:05.2f}"


def _load_candidates_from_csv(csv_path: Path) -> list[dict]:
    """Parse our V8 CSV report, extract intervals that have candidates."""
    candidates = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cand_str = row.get("candidates", "").strip()
            if not cand_str:
                continue
            bpm_approx = 128.0
            pssi_start = int(row.get("pssi_start", 0))
            start_beats = float(row.get("source_start_beats", 0))
            start_sec = (pssi_start - 1) * (60.0 / bpm_approx)

            for part in cand_str.split("; "):
                match = re.match(r"(\w+)\(([\d.]+)\)", part.strip())
                if match:
                    cue_type = match.group(1)
                    confidence = float(match.group(2))
                    candidates.append({
                        "cue_type": cue_type,
                        "confidence": confidence,
                        "start_beats": start_beats,
                        "pssi_start": pssi_start,
                        "interval": int(row.get("interval", 0)),
                        "rb_label": row.get("rb_label", ""),
                    })
    return candidates


def _match_csv_to_audio(csv_path: Path, audio_files: list[Path]) -> Path | None:
    """Match a CSV report filename back to its audio file."""
    csv_stem = csv_path.stem.replace("Analysis - ", "")
    for audio in audio_files:
        sanitized = re.sub(r'[^\w\-]', '_', audio.stem)[:80]
        if sanitized.startswith(csv_stem[:30]):
            return audio
        if csv_stem[:25] in sanitized:
            return audio
    return None


def _find_audio_for_csv(csv_path: Path, audio_files: list[Path]) -> Path | None:
    """More robust matching: extract key components from CSV name and match."""
    csv_name = csv_path.stem.replace("Analysis - ", "")
    parts = csv_name.split("_-_")
    if len(parts) >= 3:
        search_key = parts[2].replace("_", " ")[:20].lower()
        for audio in audio_files:
            if search_key in audio.name.lower():
                return audio
    for audio in audio_files:
        csv_tokens = set(csv_name.lower().replace("_", " ").split())
        audio_tokens = set(audio.stem.lower().replace("-", " ").replace("_", " ").split())
        overlap = csv_tokens & audio_tokens
        if len(overlap) >= 3:
            return audio
    return None


def main():
    audio_files = sorted(AUDIO_DIR.glob("*.wav"))
    csv_files = sorted(REPORTS_DIR.glob("Analysis - *.csv"))

    print("=" * 100)
    print("MIK 11 AUTO-CUE POINTS vs OUR V8 CUE CANDIDATES — SIDE-BY-SIDE COMPARISON")
    print("=" * 100)
    print(f"Match tolerance: ±{MATCH_TOLERANCE_SEC}s")
    print()

    total_mik_cues = 0
    total_our_cues = 0
    total_aligned = 0

    for audio_path in audio_files:
        mik = enrich_from_mik(audio_path)
        if not mik.cues:
            continue

        csv_match = None
        for csv_f in csv_files:
            audio_match = _find_audio_for_csv(csv_f, audio_files)
            if audio_match and audio_match == audio_path:
                csv_match = csv_f
                break

        our_candidates = _load_candidates_from_csv(csv_match) if csv_match else []

        bpm = mik.bpm or 128.0
        sec_per_beat = 60.0 / bpm

        print(f"{'─' * 100}")
        print(f"  {mik.artist} — {mik.title}")
        print(f"  Key: {mik.key} (conf: {mik.key_confidence:.0%})  BPM: {mik.bpm}  Energy: {mik.energy}  LUFS: {mik.lufs}")
        if mik.beat_grid:
            print(f"  MIK Beat Grid: {len(mik.beat_grid.beats_ms)} markers, tempo {mik.beat_grid.tempo:.4f}")
        if mik.energy_segments:
            seg_str = " → ".join(f"E{s.energy}({_fmt_time(s.start_sec)}-{_fmt_time(s.end_sec)})" for s in mik.energy_segments)
            print(f"  MIK Energy: {seg_str}")
        print()

        # Convert our candidates to approximate seconds
        our_cues_sec = []
        for c in our_candidates:
            approx_sec = (c["pssi_start"] - 1) * sec_per_beat
            our_cues_sec.append({
                **c,
                "sec": approx_sec,
            })

        # Print side by side
        print(f"  {'MIK 11 Cue Points':<45} {'Our V8 Candidates':<55}")
        print(f"  {'─' * 44} {'─' * 54}")

        # Build alignment map
        aligned_pairs = []
        used_ours = set()
        used_mik = set()

        for mi, mc in enumerate(mik.cues):
            best_dist = float("inf")
            best_oi = None
            for oi, oc in enumerate(our_cues_sec):
                if oi in used_ours:
                    continue
                dist = abs(mc.time_sec - oc["sec"])
                if dist < MATCH_TOLERANCE_SEC and dist < best_dist:
                    best_dist = dist
                    best_oi = oi
            if best_oi is not None:
                aligned_pairs.append((mi, best_oi, best_dist))
                used_ours.add(best_oi)
                used_mik.add(mi)

        mik_idx = 0
        our_idx = 0
        aligned_count = 0

        all_events = []
        for mi, mc in enumerate(mik.cues):
            all_events.append(("mik", mi, mc.time_sec))
        for oi, oc in enumerate(our_cues_sec):
            all_events.append(("ours", oi, oc["sec"]))
        all_events.sort(key=lambda x: x[2])

        printed_mik = set()
        printed_ours = set()

        for pair in aligned_pairs:
            mi, oi, dist = pair
            mc = mik.cues[mi]
            oc = our_cues_sec[oi]
            mik_str = f"Cue {mc.index + 1}: {_fmt_time(mc.time_sec)}"
            our_str = f"{oc['cue_type']}({oc['confidence']:.2f}) @ {_fmt_time(oc['sec'])} [{oc['rb_label']}]"
            delta = mc.time_sec - oc["sec"]
            sign = "+" if delta >= 0 else ""
            align_str = f"  ✓ ALIGNED ({sign}{delta:.1f}s)"
            print(f"  {mik_str:<45} {our_str:<40}{align_str}")
            printed_mik.add(mi)
            printed_ours.add(oi)
            aligned_count += 1

        # Print unmatched MIK cues
        for mi, mc in enumerate(mik.cues):
            if mi not in printed_mik:
                mik_str = f"Cue {mc.index + 1}: {_fmt_time(mc.time_sec)}"
                print(f"  {mik_str:<45} {'— no match —':<40}  ✗ MIK ONLY")

        # Print unmatched our candidates
        for oi, oc in enumerate(our_cues_sec):
            if oi not in printed_ours:
                our_str = f"{oc['cue_type']}({oc['confidence']:.2f}) @ {_fmt_time(oc['sec'])} [{oc['rb_label']}]"
                print(f"  {'— no match —':<45} {our_str:<40}  ✗ OURS ONLY")

        mik_count = len(mik.cues)
        our_count = len(our_cues_sec)
        total_mik_cues += mik_count
        total_our_cues += our_count
        total_aligned += aligned_count

        print()
        print(f"  Summary: {mik_count} MIK cues, {our_count} our candidates, {aligned_count} aligned (±{MATCH_TOLERANCE_SEC}s)")
        print()

    print("=" * 100)
    print(f"TOTALS: {total_mik_cues} MIK cues, {total_our_cues} our candidates, {total_aligned} aligned")
    if total_mik_cues > 0:
        print(f"MIK coverage: {total_aligned}/{total_mik_cues} = {total_aligned / total_mik_cues:.0%} of MIK cues matched an existing candidate")
    if total_our_cues > 0:
        print(f"Our coverage:  {total_aligned}/{total_our_cues} = {total_aligned / total_our_cues:.0%} of our candidates matched a MIK cue")
    print("=" * 100)


if __name__ == "__main__":
    main()
