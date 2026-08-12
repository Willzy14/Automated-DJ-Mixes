"""Build the two sides of the held-out policy comparison from one set of inputs.

Both sides start from the SAME sections ALS, section map, tracks and running
order, so the only difference is the transition policy. Each side builds into
its own directory: `apply_automation` falls back to a newest-first glob for the
arrangement report when none is passed explicitly, so sharing a directory would
let one side silently consume the other side's swap points.

Each side runs in its own subprocess. That guarantees no module-level state
(the automation ID counter, any cached policy) can carry from the first build
into the second and make the comparison meaningless.

Usage:
    python Source/build_ab_comparison.py "<project path>" "<sections .als>" "<sections .json>"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SIDES = [("A", "interim_v1"), ("B", "sam_v1")]


def run(cmd: list[str], log: Path) -> tuple[int, str]:
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    log.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""),
                   encoding="utf-8")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("sections_als", type=Path)
    parser.add_argument("sections_json", type=Path)
    args = parser.parse_args()

    root = args.project / "Output" / "AB"
    audit = root / "_audit"
    audit.mkdir(parents=True, exist_ok=True)

    source = Path(__file__).parent
    results: dict[str, dict] = {}

    for side, policy in SIDES:
        side_dir = root / side
        side_dir.mkdir(parents=True, exist_ok=True)
        arranged = side_dir / f"Arranged {side}.als"
        report = side_dir / f"Arranged {side}_ARRANGEMENT_REPORT.json"
        mix_plan = side_dir / f"MixPlan {side}.json"
        final = side_dir / f"Mix {side}.als"

        print(f"\n=== side {side}  policy={policy} ===")
        code, out = run([
            sys.executable, str(source / "propose_arrangement.py"),
            str(args.sections_als), str(args.sections_json), str(arranged),
            "--transition-policy", policy,
            "--report", str(report),
            "--mix-plan", str(mix_plan),
        ], audit / f"{side}_arrange.log")
        if code != 0:
            tail = "\n".join(out.strip().splitlines()[-6:])
            print(f"  ARRANGE FAILED ({code}):\n{tail}")
            results[side] = {"policy": policy, "stage": "arrange", "ok": False}
            continue
        print(f"  arranged -> {arranged.name}")

        # Explicit report path: never rely on the newest-report glob.
        code, out = run([
            sys.executable, str(source / "apply_automation.py"),
            str(arranged), str(args.sections_json), str(final), str(report),
        ], audit / f"{side}_automation.log")
        if code != 0:
            tail = "\n".join(out.strip().splitlines()[-6:])
            print(f"  AUTOMATION FAILED ({code}):\n{tail}")
            results[side] = {"policy": policy, "stage": "automation", "ok": False}
            continue
        print(f"  automated -> {final.name}")
        results[side] = {
            "policy": policy, "stage": "complete", "ok": True,
            "als": str(final), "report": str(report), "mix_plan": str(mix_plan),
        }

    (audit / "build_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")

    built = [s for s, r in results.items() if r.get("ok")]
    print(f"\nBuilt {len(built)}/{len(SIDES)} sides: {', '.join(built) or 'none'}")
    print(f"Audit logs: {audit}")
    return 0 if len(built) == len(SIDES) else 1


if __name__ == "__main__":
    sys.exit(main())
