#!/bin/bash
# Re-lay in Sam's order, gate the hints, arrange, automate, and validate.
# usage: [RESUME=1] [DEC=path] [PLANNER=off] [SECTION_MODE=legacy] run_mix_pipeline.sh "<project dir name, or an absolute project path>" "<order csv>" <ntracks>
# An inherited PYTHONPATH is kept after Source (e.g. the Audio Analysis Toolkit src on a machine without it installed).
set -u
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1
case "$1" in
    /*|[A-Za-z]:[/\\]*) P="$1" ;;
    *) P="Test Project/$1" ;;
esac
ORDER="$2"; N="$3"
section_mode_override="${SECTION_MODE:-}"
SECTION_MODE="${SECTION_MODE:-groove}"
phase3_mode=()
if [ -z "${RESUME:-}" ] || [ -n "$section_mode_override" ]; then
    phase3_mode=(--section-mode "$SECTION_MODE")
fi
export PYTHONIOENCODING=utf-8 PYTHONPATH="Source${PYTHONPATH:+;$PYTHONPATH}"
(cd -- "$P" && mkdir -p -- Output) || exit 1
LOG="$P/Output/runmix.log"; : > "$LOG" || exit 1
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
fail() { say "FAILED at: $*"; exit 1; }
# The bounce check report of an earlier build must not pass for this build's: no ATTENTION.md means the check did not run for this build.
rm -f -- "$P/Output/Bounce Check/ATTENTION.md"

# Memory preflight before the first python step. The guard runs on Sam's Home
# PC only (CARILLONAC-1-PC, where Phase 1a froze the machine on 2026-11-21);
# on every other machine it is a no-op that prints nothing and exits 0, so the
# work PC (STUDIO-2) runs this script exactly as it ran before. The file check
# keeps the pipeline tests (which run a stripped copy of the repo) unchanged.
#
# The floor here is the LIGHT one (2.0 GB): this script re-lays cached
# analysis and separates nothing itself. The HEAVY floor (7.0 GB) belongs to
# a fresh Demucs separation, and the orchestrator's own guard applies it -
# it knows whether the re-lay will separate a track (it does when a track has
# no finished stem analysis in the _Stem Analysis folder beside it).
if [ -f Source/memory_guard.py ]; then
    python Source/memory_guard.py --preflight-only --min-start-gb 2.0 || fail "memory preflight (close Chrome and Dropbox, then retry)"
fi

record="$P/Output/relay-layout.txt"
if [ -z "${RESUME:-}" ]; then
    rm -f -- "$record" || fail "clear previous relay layout record"
    say "re-lay in Sam's order"
    python -m automated_dj_mixes.orchestrator --input "$P/Audio" --output "$P/Output" --sections-layout --stem-sections --stem-grid --kick-model --skip-desktop-analyze --allow-non-master --no-previews --order "$ORDER" --section-mode "$SECTION_MODE" > "$P/Output/relay.log" 2>&1 || fail "relay (see relay.log)"
    grep -q "Custom track order applied ($N tracks)" "$P/Output/relay.log" || fail "order not applied to all $N tracks"
    generated=$(grep -a '^Generated: ' "$P/Output/relay.log" | tail -n 1)
    generated=${generated%$'\r'}
    [[ "$generated" == "Generated: "* ]] || fail "relay did not report a layout (see relay.log)"
    generated=${generated#Generated: }
    generated=${generated//\\//}
    version_pattern='^Sections V([1-9][0-9]*)\.als$'
    [[ "${generated##*/}" =~ $version_pattern ]] || fail "unexpected relay layout (see relay.log)"
    V=${BASH_REMATCH[1]}
    [[ "$generated" -ef "$P/Output/Sections V$V.als" ]] || fail "relay layout missing from Output (see relay.log)"
    say "relay produced Sections V$V.als"
else
    record_error="relay layout record is missing or stale ($record); run without RESUME"
    [[ -f "$record" ]] || fail "$record_error"
    saved=$(cat "$record") || fail "$record_error"
    record_pattern='^([1-9][0-9]*) ([0-9]+ [0-9]+)$'
    [[ "$saved" =~ $record_pattern ]] || fail "$record_error"
    V=${BASH_REMATCH[1]}
    expected=${BASH_REMATCH[2]}
    layout="$P/Output/Sections V$V.als"
    [[ -f "$layout" ]] || fail "$record_error"
    actual=$(cksum < "$layout") || fail "$record_error"
    [[ "$actual" == "$expected" ]] || fail "$record_error"
    say "resuming from relay layout Sections V$V.als"
fi
layout="$P/Output/Sections V$V.als"
sections="$P/Sections Review/Sections_V$V.json"
python Source/validate_als.py "$layout" >> "$LOG" 2>&1 || fail "validate V$V"
if [ -z "${RESUME:-}" ]; then
    checksum=$(cksum < "$layout") || fail "checksum relay layout"
    pending_record=$(mktemp "$record.XXXXXX") || fail "create relay layout record"
    if ! printf '%s %s\n' "$V" "$checksum" > "$pending_record" || ! mv -f -- "$pending_record" "$record"; then
        rm -f -- "$pending_record"
        fail "write relay layout record"
    fi
fi
python Source/extract_sections_als.py "$layout" > /dev/null 2>> "$LOG" || fail "extract sections (see runmix.log)"
[ -f "$sections" ] || fail "sections json missing"
if [ -z "${RESUME:-}" ]; then python Source/stem_detector.py "$P" --write-hints --section-mode "$SECTION_MODE" > "$P/Output/hints.log" 2>&1 || fail "write hints"; fi
python Source/validate_hints_vs_sections.py "$P" --version $V > "$P/Output/hints_gate.log" 2>&1 || fail "hints gate (see hints_gate.log)"
grep -E "errors:" "$P/Output/hints_gate.log" | tee -a "$LOG"

decision_args=()
if [ -n "${DEC:-}" ]; then
    say "arranger: hand decisions $DEC"
    decision_args=(--decisions "$DEC")
elif [[ "${PLANNER:-}" == [oO][fF][fF] ]]; then
    say "arranger: rule-based (PLANNER=off)"
else
    analysis_mode=$(python -c '
import sys
from pathlib import Path
from section_format import SectionFormatError, cached_section_mode

try:
    mode = cached_section_mode(Path(sys.argv[1]), "run_mix_pipeline")
except SectionFormatError as exc:
    sys.exit(str(exc))
if mode is None:
    sys.exit("No readable section analysis in " + sys.argv[1])
print(mode)
' "$P/_Stem Analysis" 2>> "$LOG") || fail "detect section format (see runmix.log)"
    if [ "$analysis_mode" = legacy ]; then
        say "arranger: rule-based (legacy sections; the planner needs groove)"
    elif [ "$analysis_mode" = groove ]; then
        planned="$P/Hints/planned_decisions.json"
        python Tools/plan_mix_decisions.py "$P" --sections "$sections" --out "$planned" --content "$P/Output/planner_content.json" --allow-partial > "$P/Output/planner.log" 2>&1 || fail "planner (see planner.log)"
        summary=$(python -c '
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
planned = len(data["transitions"])
fallback = ["T{}".format(pair["pair_index"]) for pair in data["fallback_pairs"]]
whole_bar = ["T{} ({:+.2f})".format(row["pair_index"], row["cost_difference"]) for row in data.get("whole_bar_alternatives", [])]
summary = f"{planned} of {planned + len(fallback)} pairs planned"
if fallback:
    summary += ", fallback: " + ",".join(fallback)
if whole_bar:
    summary += ", whole-bar alternative: " + ",".join(whole_bar)
print(summary)
' "$planned" 2>> "$P/Output/planner.log") || fail "planner (see planner.log)"
        say "arranger: planner ($summary)"
        decision_args=(--decisions "$planned" --decisions-drop-rejected-loops)
    else
        fail "unsupported section format: $analysis_mode"
    fi
fi

A=$((V+1)); B=$((V+2))
say "phase 2 -> V$A"
python Source/propose_arrangement.py "$layout" "$sections" "$P/Output/Sections V$A.als" --hints "$P/Hints/track_hints.json" --tempo-arc "${decision_args[@]}" --mix-plan "$P/Output/MIX_PLAN.json" --report "$P/Output/ARRANGEMENT_REPORT.json" > "$P/Output/phase2.log" 2>&1 || fail "phase 2 (see phase2.log)"
python Source/validate_als.py "$P/Output/Sections V$A.als" >> "$LOG" 2>&1 || fail "validate V$A"
say "phase 3 -> V$B"
python Source/apply_automation.py "$P/Output/Sections V$A.als" "$sections" "$P/Output/Sections V$B.als" "$P/Output/ARRANGEMENT_REPORT.json" "$P/Output/MIX_PLAN.json" "${phase3_mode[@]}" > "$P/Output/phase3.log" 2>&1 || fail "phase 3 (see phase3.log)"
python Source/validate_als.py "$P/Output/Sections V$B.als" --expected-tracks $N --require-devices --arrangement-report "$P/Output/ARRANGEMENT_REPORT.json" >> "$LOG" 2>&1 || fail "final validate V$B"
# Kick alignment: look at each blend (pictures beside the Output) and say which ones are off. Warn only until
# Sam flips KICK_ALIGNMENT_MODE=fail; in warn mode a check that cannot run never stops the build.
case "${KICK_ALIGNMENT_MODE:-warn}" in fail) kick_mode=fail ;; *) kick_mode=warn ;; esac
say "kick alignment check ($kick_mode)"
{ python Tools/check_kick_alignment.py "$P/Output/Sections V$B.als" --audio "$P/Audio" --out "$P/Output/Kick Alignment" --mode "$kick_mode"; } 2>> "$LOG" | tee -a "$LOG"
kick_status=${PIPESTATUS[0]}
kick_fail=""
if [ "$kick_status" -ne 0 ]; then
    if [ "$kick_mode" = fail ]; then
        # the failure stops the build, but only after the grid phase step below has had its say about the same blends
        if [ "$kick_status" -eq 1 ]; then
            kick_fail="kick alignment FAIL (pictures in Output/Kick Alignment, details in runmix.log)"
        elif [ "$kick_status" -eq 3 ]; then
            kick_fail="kick alignment judged nothing (status 3; every blend was CANNOT_JUDGE, see runmix.log)"
        else
            kick_fail="kick alignment could not run (status $kick_status)"
        fi
    else
        say "kick alignment check did not finish (status $kick_status); warn mode, the build goes on"
    fi
fi
# Grid phase: a drift fit and a slide per track, measured by the kick check, into a NEW file. It never stops the build.
# GRID_PHASE_MODE=off (any case) skips it; apply (exactly that spelling) lets the phased file become the final when it passes
# validate_als.py and the MixPlan reconciliation against grid_phase.json (checked at the end, see below); anything else
# (default) is suggest: the phased file is only offered, the final stays Sections V$B.als.
case "${GRID_PHASE_MODE:-suggest}" in [oO][fF][fF]) grid_mode=off ;; apply) grid_mode=apply ;; *) grid_mode=suggest ;; esac
phased="$P/Output/Sections V$B Phased.als"
grid_record="$P/Output/Grid Phase/grid_phase.json"
grid_report="$P/Output/Grid Phase/grid_phase_report.json"
recon="$P/Output/MIX_PLAN_RECONCILIATION.json"
recon_original="$P/Output/MIX_PLAN_RECONCILIATION_ORIGINAL.json"
phased_recon="$P/Output/MIX_PLAN_RECONCILIATION_PHASED.json"
phased_failed="$P/Output/MIX_PLAN_RECONCILIATION_PHASED.FAILED.json"
apply_blocker=""
# Nothing of an earlier build may pass for this build's result, in every mode (off included): the phased file, its REFUSED
# copy, any .partial, its record, its reconciliations and the saved reconciliation of an earlier adopted original.
rm -f -- "$phased" "$P/Output/Sections V$B Phased REFUSED.als" "$P/Output/Sections V$B Phased"*.partial "$phased_recon" "$phased_failed" "$recon_original" "$grid_record"
if [ "$grid_mode" = off ]; then
    say "grid phase fix: off (GRID_PHASE_MODE=off), step skipped"
else
    say "grid phase fix ($grid_mode): report in $P/Output/Grid Phase"
    { python Tools/fix_grid_phase.py "$P/Output/Sections V$B.als" --audio "$P/Audio" --out "$phased" --report "$P/Output/Grid Phase"; } 2>> "$LOG" | tee -a "$LOG"
    grid_status=${PIPESTATUS[0]}
    if [ "$grid_status" -ne 0 ]; then
        rm -f -- "$P/Output/Sections V$B Phased"*.partial "$phased" "$grid_record"      # a tool that failed leaves no phased file and no record behind
        if [ "$grid_status" -eq 4 ]; then
            apply_blocker="the grid phase tool refused its own result (status 4)"
            say "grid phase fix REFUSED its own result (status 4, details in Output/Grid Phase); no phased file, the final is Sections V$B.als"
        else
            apply_blocker="the grid phase tool did not finish (status $grid_status)"
            say "grid phase fix did not finish (status $grid_status); no phased file, the final is Sections V$B.als"
        fi
    elif [ ! -f "$phased" ]; then
        apply_blocker="the grid phase tool wrote no phased file"
        say "grid phase fix wrote no phased file; the final is Sections V$B.als"
    elif cmp -s -- "$P/Output/Sections V$B.als" "$phased"; then
        apply_blocker="nothing to change (the phased file is a copy of Sections V$B.als)"
        rm -f -- "$grid_record"
        say "grid phase: nothing to change (the phased file is a copy of Sections V$B.als)"
    elif ! python Source/validate_als.py "$phased" --expected-tracks $N --require-devices --arrangement-report "$P/Output/ARRANGEMENT_REPORT.json" >> "$LOG" 2>&1; then
        apply_blocker="the phased file failed validate_als.py"
        rm -f -- "$phased" "$grid_record"
        say "GRID PHASE FILE FAILED VALIDATION and was deleted (details in runmix.log); the final is Sections V$B.als"
    elif [ "$grid_mode" = apply ] && [ ! -f "$grid_record" ]; then
        apply_blocker="the grid phase tool wrote no record (Output/Grid Phase/grid_phase.json)"
        say "grid phase: the phased file has no record (grid_phase.json); the final is Sections V$B.als"
    elif [ "$grid_mode" = apply ]; then
        say "grid phase: phased file written and valid; its MixPlan reconciliation against grid_phase.json decides the final (below)"
    else
        say "grid phase: phased file written, open it instead of Sections V$B.als if the report shows it is tighter; the MixPlan reconciliation covers the unphased original (the phased markers differ from the original by the corrections shown in the report: drift up to 12 ms, slide up to 15 ms)"
    fi
fi
[ -z "$kick_fail" ] || fail "$kick_fail"
python Source/validate_mix_plan_als.py "$P/Output/MIX_PLAN.json" "$P/Output/ARRANGEMENT_REPORT.json" "$P/Output/Sections V$B.als" --output "$P/Output/MIX_PLAN_RECONCILIATION.json" >> "$LOG" 2>&1 || fail "reconciliation"
# Apply mode: the original above still had to reconcile. The phased file is the final only if the tool wrote it (PHASED, which
# the tool only says when no judged blend's worst click lag got worse; its edge or main hit may), validate_als.py passed it with the same flags as the original (above), the
# reconciliation of it passes against the record AND the original ALS, and the tool's report can be read (a blend it could not
# fix, or whose edge or main hit got worse, is named in the final line, never hidden). The first thing that fails keeps the original as the final, loudly, and never
# fails the build. When the phased file is adopted the saved reconciliation of the original becomes MIX_PLAN_RECONCILIATION_
# ORIGINAL.json and the phased one becomes the canonical MIX_PLAN_RECONCILIATION.json, so the canonical file describes the
# final ALS; when it is not adopted nothing is renamed, and a failed phased reconciliation is kept as ...PHASED.FAILED.json.
final_text="Sections V$B.als"
final_file="$P/Output/Sections V$B.als"
if [ "$grid_mode" = apply ]; then
    caveats=""
    if [ -z "$apply_blocker" ]; then
        if ! python Source/validate_mix_plan_als.py "$P/Output/MIX_PLAN.json" "$P/Output/ARRANGEMENT_REPORT.json" "$phased" --grid-phase "$grid_record" --grid-phase-original "$P/Output/Sections V$B.als" --output "$phased_recon" >> "$LOG" 2>&1; then
            apply_blocker="the MixPlan reconciliation of the phased file failed (details in runmix.log, kept as MIX_PLAN_RECONCILIATION_PHASED.FAILED.json)"
        elif ! grep -q '"grid_phase"' "$phased_recon" 2>/dev/null; then
            apply_blocker="the phased reconciliation passed without a grid_phase section (not trusted)"
        fi
    fi
    if [ -z "$apply_blocker" ]; then
        caveats=$(python -c '
import json
import sys

data = json.load(open(sys.argv[1], encoding="ascii"))
parts = []
flamming = sorted({"T%02d" % item["index"] for item in data["still_flamming"]})
if flamming:
    parts.append("still flamming: " + ",".join(flamming))
worse = sorted({item["label"].split()[0] + " " + item["estimator"] for item in data["edge_main_worse"]})
if worse:
    parts.append("edge or main hit worse: " + ", ".join(worse))
print("; ".join(parts))
' "$grid_report" 2>> "$LOG") || apply_blocker="Output/Grid Phase/grid_phase_report.json is missing or unreadable (cannot tell which blends are still flamming or worse at the edge)"
    fi
    if [ -z "$apply_blocker" ]; then
        if mv -f -- "$recon" "$recon_original" && mv -f -- "$phased_recon" "$recon"; then
            final_file="$phased"
            final_text="Sections V$B Phased.als (grid phase applied${caveats:+; $caveats})"
        else
            mv -f -- "$recon_original" "$recon" 2>/dev/null
            apply_blocker="could not save the reconciliations (MIX_PLAN_RECONCILIATION_ORIGINAL.json)"
        fi
    fi
    if [ -n "$apply_blocker" ]; then
        [ ! -f "$phased_recon" ] || mv -f -- "$phased_recon" "$phased_failed"
        final_text="Sections V$B.als (grid phase NOT applied: $apply_blocker)"
    fi
    final_info=$(python -c '
import hashlib
import os
import sys

path = os.path.abspath(sys.argv[1])
print(path + " sha256 " + hashlib.sha256(open(path, "rb").read()).hexdigest())
' "$final_file" 2>> "$LOG") && say "final ALS: $final_info" || say "final ALS: could not read $final_file to hash it"
fi
# Bounce check: measure every join on the RENDERED bounce, report only (the build never stops here). The bounce is found by the
# same-basename rule only: <final basename>.wav beside the final ALS in Output (e.g. Sections V9 Phased.wav beside Sections V9 Phased.als),
# and only when it is not older than that ALS: a WAV rendered before the ALS was last written belongs to an earlier arrangement and is
# ignored. A build writes a brand new final ALS, so normally nothing has been rendered from it yet: render it, then run the command printed
# below (it finds the final ALS and its fresh bounce itself). BOUNCE_CHECK_MODE=off (any case) skips the step. The tool writes
# Output/Bounce Check/ATTENTION.md (NEEDS A LOOK lines, or "no join needs a look"); whoever builds the mix opens the pictures it names.
bounce_wav="${final_file%.als}.wav"
bounce_dir="$P/Output/Bounce Check"
bounce_cmd="python Tools/check_bounce.py --project \"$P\""
if [[ "${BOUNCE_CHECK_MODE:-}" == [oO][fF][fF] ]]; then
    say "bounce check: off (BOUNCE_CHECK_MODE=off), step skipped"
elif [ ! -f "$bounce_wav" ]; then
    say "bounce check: no fresh bounce beside the final yet (render the final ALS to a WAV of the same name, then run):"
    say "  $bounce_cmd"
elif [ "$bounce_wav" -ot "$final_file" ]; then
    say "bounce check: the bounce beside the final is older than the ALS (stale), ignored: render the new final, then run Tools/check_bounce.py:"
    say "  $bounce_cmd"
else
    say "bounce check: $bounce_wav (report only: read $bounce_dir/ATTENTION.md and open the pictures it names)"
    bounce_out=$(python Tools/check_bounce.py "$bounce_wav" --als "$final_file" --out "$bounce_dir" 2>> "$LOG")
    bounce_status=$?
    printf '%s\n' "$bounce_out" >> "$LOG"
    bounce_summary=$(printf '%s\n' "$bounce_out" | grep -a '^BOUNCE CHECK: ' | tail -n 1)
    bounce_summary=${bounce_summary%$'\r'}
    [ -z "$bounce_summary" ] || say "$bounce_summary"
    if [ "$bounce_status" -eq 3 ]; then
        say "bounce check: the bounce does not fit the final ALS (status 3); its numbers cannot be trusted, details in $bounce_dir/ATTENTION.md"
    elif [ "$bounce_status" -ne 0 ]; then
        # the tool writes its own "did not run" file on status 2; for any other status (a hard crash, command not found) the hook does,
        # so the file exists after every invocation and no old or missing report can read as a clean one
        if [ "$bounce_status" -ne 2 ] || [ ! -f "$bounce_dir/ATTENTION.md" ]; then
            mkdir -p -- "$bounce_dir" && printf 'bounce check did not run (status %s): no measurements\n' "$bounce_status" > "$bounce_dir/ATTENTION.md"
        fi
        say "bounce check did not finish (status $bounce_status): no measurements, $bounce_dir/ATTENTION.md says so; the build goes on"
    fi
fi
say "DONE: final = $final_text"
