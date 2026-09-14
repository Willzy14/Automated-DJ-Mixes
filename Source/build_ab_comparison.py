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

from validate_mix_plan_als import reconcile

# (label, transition-policy, cue-signals). cue-signals is a --cue-signals
# value forwarded verbatim to propose_arrangement.py's CLI (comma-separated
# tokens, e.g. "introloop") or "" for none. Each subprocess ALWAYS installs a
# fresh CueConfig for the tokens it's given (see propose_arrangement.py's
# _apply_cue_signals), so side C's "introloop" cannot leak into A or B even
# though they share this process's own module space via subprocess.run.
#
# "C" = sam_v1's policy geometry + the last-drop incoming_intro_loop cue
# signal (the "SAM_V2" candidate, informal name - not a registered
# TransitionPolicy; the two axes are orthogonal, see transition_policy.py's
# own docstring on why cue signals and policy stay separate). Added 2026-
# 09-10: the flag existed and was directly evidenced (Fresh Mix V2, 5/6 of
# its reworked transitions land on this exact target) but this harness never
# actually enabled it for any side, so no comparison had ever exercised it.
SIDES = [
    ("A", "interim_v1", ""),
    ("B", "sam_v1", ""),
    ("C", "sam_v1", "introloop"),
]


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

    for side, policy, cue_signals in SIDES:
        side_dir = root / side
        side_dir.mkdir(parents=True, exist_ok=True)
        arranged = side_dir / f"Arranged {side}.als"
        report = side_dir / f"Arranged {side}_ARRANGEMENT_REPORT.json"
        mix_plan = side_dir / f"MixPlan {side}.json"
        final = side_dir / f"Mix {side}.als"

        label = f"{policy}+{cue_signals}" if cue_signals else policy
        print(f"\n=== side {side}  policy={label} ===")
        arrange_cmd = [
            sys.executable, str(source / "propose_arrangement.py"),
            str(args.sections_als), str(args.sections_json), str(arranged),
            "--transition-policy", policy,
            "--report", str(report),
            "--mix-plan", str(mix_plan),
        ]
        if cue_signals:
            arrange_cmd += ["--cue-signals", cue_signals]
        code, out = run(arrange_cmd, audit / f"{side}_arrange.log")
        if code != 0:
            tail = "\n".join(out.strip().splitlines()[-6:])
            print(f"  ARRANGE FAILED ({code}):\n{tail}")
            results[side] = {"policy": policy, "cue_signals": cue_signals,
                             "stage": "arrange", "ok": False}
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
            results[side] = {"policy": policy, "cue_signals": cue_signals,
                             "stage": "automation", "ok": False}
            continue
        print(f"  automated -> {final.name}")

        # MixPlan reconciliation is now a real gate for every side (burn
        # list A4, 2026-09-14) - not skipped for non-tempo-arc/experimental
        # builds. It used to hard-fail on every track here (project_bpm None
        # -> NaN, every warp_mode the literal string "inherited") because
        # propose_arrangement.py never recorded what tempo/warp mode a
        # FULLY INHERITED build (no --project-bpm/--warp-mode override -
        # exactly how every side here is built) actually used - a real data
        # gap in the MixPlan, fixed at the source rather than exempted here.
        # In-process, not a subprocess: reconciliation is read-only, so the
        # per-side module-state isolation this file's docstring cares about
        # (the automation ID counter, cached policy) does not apply to it.
        try:
            # Broad except, not just ValueError (render_check.py's own
            # "gate could not run" convention): a missing/malformed
            # MixPlan or ALS is exactly as disqualifying as a genuine
            # mismatch - never let an unrelated IO/parse error crash the
            # whole comparison build uncaught when it should just fail
            # this one side's gate.
            recon = reconcile(mix_plan, report, final)
            print(f"  reconciled -> {len(recon['checks'])} checks PASS")
            results[side] = {
                "policy": policy, "cue_signals": cue_signals,
                "stage": "complete", "ok": True, "als": str(final),
                "report": str(report), "mix_plan": str(mix_plan),
                "reconciliation": recon,
            }
        except Exception as e:
            print(f"  RECONCILE FAILED: {type(e).__name__}: {e}")
            results[side] = {
                "policy": policy, "cue_signals": cue_signals,
                "stage": "reconcile", "ok": False, "als": str(final),
                "report": str(report), "mix_plan": str(mix_plan),
                "reconciliation_error": f"{type(e).__name__}: {e}",
            }

    (audit / "build_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")

    built = [s for s, r in results.items() if r.get("ok")]
    print(f"\nBuilt {len(built)}/{len(SIDES)} sides: {', '.join(built) or 'none'}")
    print(f"Audit logs: {audit}")
    return 0 if len(built) == len(SIDES) else 1


if __name__ == "__main__":
    sys.exit(main())
