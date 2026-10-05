#!/bin/bash
# Re-lay in Sam's order, then hints gate, phase 2, phase 3, all validators; stops at the first failed gate. RESUME=1 skips the re-lay; DEC=<decisions.json> passes --decisions. See Documentation/Plans and the 2026-10-05 session notes.
# usage: runmix.sh "<project dir name>" "<order csv>" <ntracks>
set -u
cd "/c/Users/Carillon/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes"
P="Test Project/$1"; ORDER="$2"; N="$3"
export PYTHONIOENCODING=utf-8 PYTHONPATH=Source
LOG="$P/Output/runmix.log"; : > "$LOG"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
fail() { say "FAILED at: $*"; exit 1; }

if [ -z "${RESUME:-}" ]; then
say "re-lay in Sam's order"
python -m automated_dj_mixes.orchestrator --input "$P/Audio" --output "$P/Output" --sections-layout --stem-sections --stem-grid --kick-model --skip-desktop-analyze --allow-non-master --order "$ORDER" > "$P/Output/relay.log" 2>&1 || fail "relay (see relay.log)"
grep -q "Custom track order applied ($N tracks)" "$P/Output/relay.log" || fail "order not applied to all $N tracks"
fi
V=$(ls "$P/Output"/Sections\ V[0-9]*.als | sed -E 's/.*Sections V([0-9]+)\.als/\1/' | sort -n | tail -1)
say "relay produced Sections V$V.als"
python Source/validate_als.py "$P/Output/Sections V$V.als" >> "$LOG" 2>&1 || fail "validate V$V"
python Source/extract_sections_als.py "$P/Output/Sections V$V.als" > /dev/null 2>&1 || fail "extract sections"
[ -f "$P/Sections Review/Sections_V$V.json" ] || fail "sections json missing"
if [ -z "${RESUME:-}" ]; then python Source/stem_detector.py "$P" --write-hints > "$P/Output/hints.log" 2>&1 || fail "write hints"; fi
python Source/validate_hints_vs_sections.py "$P" --version $V > "$P/Output/hints_gate.log" 2>&1 || fail "hints gate (see hints_gate.log)"
grep -E "errors:" "$P/Output/hints_gate.log" | tee -a "$LOG"
A=$((V+1)); B=$((V+2))
say "phase 2 -> V$A"
python Source/propose_arrangement.py "$P/Output/Sections V$V.als" "$P/Sections Review/Sections_V$V.json" "$P/Output/Sections V$A.als" --hints "$P/Hints/track_hints.json" --tempo-arc ${DEC:+--decisions "$DEC"} --mix-plan "$P/Output/MIX_PLAN.json" --report "$P/Output/ARRANGEMENT_REPORT.json" > "$P/Output/phase2.log" 2>&1 || fail "phase 2 (see phase2.log)"
python Source/validate_als.py "$P/Output/Sections V$A.als" >> "$LOG" 2>&1 || fail "validate V$A"
say "phase 3 -> V$B"
python Source/apply_automation.py "$P/Output/Sections V$A.als" "$P/Sections Review/Sections_V$V.json" "$P/Output/Sections V$B.als" "$P/Output/ARRANGEMENT_REPORT.json" "$P/Output/MIX_PLAN.json" > "$P/Output/phase3.log" 2>&1 || fail "phase 3 (see phase3.log)"
python Source/validate_als.py "$P/Output/Sections V$B.als" --expected-tracks $N --require-devices --arrangement-report "$P/Output/ARRANGEMENT_REPORT.json" >> "$LOG" 2>&1 || fail "final validate V$B"
python Source/validate_mix_plan_als.py "$P/Output/MIX_PLAN.json" "$P/Output/ARRANGEMENT_REPORT.json" "$P/Output/Sections V$B.als" --output "$P/Output/MIX_PLAN_RECONCILIATION.json" >> "$LOG" 2>&1 || fail "reconciliation"
say "DONE: final = Sections V$B.als"
