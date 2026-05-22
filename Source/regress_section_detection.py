"""Regression test for the section-detection algorithm.

Compares CURRENT section-detection output against blessed "golden" JSONs
from past projects. Lets you tune `phrase_viz.py` for one project without
silently regressing another.

To bless a project (manual, one-off per project):
  1. Confirm with Sam that the mix is final and chops are correct.
  2. Copy <project>/Sections Review/Sections_V<N>.json to
     Documentation/Golden Sections/<ProjectName>__final.json
  3. Add a metadata header (small wrapper around the sections data —
     see _bless() / _unwrap() below) so the regress script knows
     where to find the project audio.

Run after any change to phrase_viz.py:
  python regress_section_detection.py

Output: prints per-project pass/fail, exits non-zero if any regression.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


# Repo root: this file lives in <repo>/Source/, so parent of Source/ = repo
REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "Documentation" / "Golden Sections"

BAR_TOLERANCE = 1.0   # ≤1 bar diff per boundary = match


def _unwrap(golden_obj: dict | list) -> tuple[dict, dict]:
    """Allow either:
      [ sections_data ]  (raw, no metadata) — project_dir must be inferred
                          from the filename stem
      { "project_dir": "...", "sections": { ... } } — explicit
    Returns (sections_data, metadata).
    """
    if isinstance(golden_obj, dict) and "sections" in golden_obj:
        return golden_obj["sections"], {
            "project_dir": golden_obj.get("project_dir"),
            "audio_dir": golden_obj.get("audio_dir"),
            "blessed_at": golden_obj.get("blessed_at"),
        }
    if isinstance(golden_obj, dict):
        return golden_obj, {}
    raise ValueError("Unrecognised golden format")


def _diff_sections(current: dict, golden: dict) -> list[str]:
    """Compare two sections dicts. Returns list of human-readable diffs.
    Empty list means they match within tolerance."""
    diffs = []
    cur_tracks = set(current.keys())
    gold_tracks = set(golden.keys())
    only_gold = gold_tracks - cur_tracks
    only_cur = cur_tracks - gold_tracks
    for t in only_gold:
        diffs.append(f"missing track: {t}")
    for t in only_cur:
        diffs.append(f"new track (not in golden): {t}")

    common = cur_tracks & gold_tracks
    for t in common:
        cur_secs = current[t]
        gold_secs = golden[t]
        if len(cur_secs) != len(gold_secs):
            diffs.append(
                f"{t[:40]}: section count {len(cur_secs)} vs golden {len(gold_secs)}")
            continue
        for cs, gs in zip(cur_secs, gold_secs):
            if cs.get("label") != gs.get("label"):
                diffs.append(
                    f"{t[:40]}: section '{cs.get('name')}' label "
                    f"'{cs.get('label')}' vs golden '{gs.get('label')}'")
            cs_bar = cs.get("source_start_beats", 0) / 4
            gs_bar = gs.get("source_start_beats", 0) / 4
            if abs(cs_bar - gs_bar) > BAR_TOLERANCE:
                diffs.append(
                    f"{t[:40]}: '{cs.get('name')}' start bar "
                    f"{cs_bar:.1f} vs golden {gs_bar:.1f} "
                    f"(Δ {cs_bar - gs_bar:+.1f} bars)")
    return diffs


def regenerate_sections(project_dir: Path, work_dir: Path) -> dict:
    """Run the section-layout orchestrator on a project's Audio folder and
    return the resulting sections dict.

    Uses --skip-desktop-analyze so it doesn't drive MIK/Rekordbox UI.
    Output goes to work_dir/Output, then extract_sections_als.py converts
    the .als to JSON.
    """
    audio_dir = project_dir / "Audio"
    output_dir = work_dir / "Output"
    output_dir.mkdir(parents=True, exist_ok=True)
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(REPO_ROOT / "Source")}

    # Orchestrator — sections-layout mode
    cmd = [
        sys.executable, "-m", "automated_dj_mixes.orchestrator",
        "--input", str(audio_dir),
        "--output", str(output_dir),
        "--sections-layout", "--skip-desktop-analyze",
    ]
    subprocess.run(cmd, check=True, env={**__import__("os").environ, **env})

    # Find the latest Sections V<N>.als under output_dir
    als_candidates = list(output_dir.glob("Sections V*.als")) + \
                     list(output_dir.glob("Sections V* Project/*.als"))
    if not als_candidates:
        raise RuntimeError(f"No Sections .als produced under {output_dir}")
    als_candidates.sort(key=lambda p: p.stat().st_mtime)
    als_path = als_candidates[-1]

    # Extract to JSON via extract_sections_als.py
    extract_script = REPO_ROOT / "Source" / "extract_sections_als.py"
    subprocess.run([sys.executable, str(extract_script), str(als_path)],
                   check=True, env={**__import__("os").environ, **env})
    # extract_sections_als.py writes the JSON next to the .als or into the
    # project's Sections Review dir — easier to read the ALS path stem:
    json_candidates = list(work_dir.rglob("Sections_V*.json"))
    if not json_candidates:
        raise RuntimeError("extract_sections_als did not produce a JSON")
    json_candidates.sort(key=lambda p: p.stat().st_mtime)
    return json.loads(json_candidates[-1].read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR)
    parser.add_argument("--project", type=str, default=None,
                        help="Test only a single project by name")
    args = parser.parse_args()

    if not args.golden_dir.exists():
        print(f"No Golden Sections folder at {args.golden_dir}. "
              f"Nothing to regress.")
        return 0
    goldens = sorted(args.golden_dir.glob("*.json"))
    if args.project:
        goldens = [g for g in goldens if args.project.lower() in g.stem.lower()]
    if not goldens:
        print(f"No golden JSONs in {args.golden_dir}. "
              f"Bless a project before running this script.")
        return 0

    overall_ok = True
    for golden_path in goldens:
        print(f"\n=== {golden_path.name} ===")
        try:
            raw = json.loads(golden_path.read_text(encoding="utf-8"))
            golden_secs, meta = _unwrap(raw)
        except Exception as e:
            print(f"  ERROR loading: {e}")
            overall_ok = False
            continue

        # Resolve project_dir from metadata, else try to find by name
        project_dir = None
        if meta.get("project_dir"):
            project_dir = Path(meta["project_dir"])
        if project_dir is None or not project_dir.exists():
            # Best-effort lookup under Test Project/
            stem = golden_path.stem.replace("__final", "").replace("_", " ")
            candidates = list((REPO_ROOT / "Test Project").glob(f"*{stem}*"))
            project_dir = candidates[0] if candidates else None
        if project_dir is None or not project_dir.exists():
            print(f"  SKIP: cannot find project_dir for {golden_path.stem}. "
                  f"Add `\"project_dir\": \"...\"` to the golden JSON header.")
            continue

        # Run regeneration in a temp workspace so we don't disturb the
        # project's own Output folder.
        with tempfile.TemporaryDirectory() as td:
            work_dir = Path(td)
            # Copy/symlink Audio into work_dir so orchestrator can find it
            (work_dir / "Audio").symlink_to(project_dir / "Audio",
                                            target_is_directory=True) \
                if hasattr(Path, "symlink_to") else None
            try:
                current_secs = regenerate_sections(work_dir, work_dir)
            except Exception as e:
                print(f"  ERROR regenerating: {e}")
                overall_ok = False
                continue

        diffs = _diff_sections(current_secs, golden_secs)
        if diffs:
            overall_ok = False
            print(f"  FAIL: {len(diffs)} regression(s)")
            for d in diffs[:20]:
                print(f"    - {d}")
            if len(diffs) > 20:
                print(f"    ... and {len(diffs) - 20} more")
        else:
            print(f"  PASS: all sections match within {BAR_TOLERANCE} bar(s)")

    return 0 if overall_ok else 2


if __name__ == "__main__":
    sys.exit(main())
