import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "Test Project/Test Mix"
OUTPUT = f"{PROJECT}/Output"
LAYOUT = f"{OUTPUT}/Sections V7.als"
SECTIONS = f"{PROJECT}/Sections Review/Sections_V7.json"
PLANNED = f"{PROJECT}/Hints/planned_decisions.json"
PLANNER = "Tools/plan_mix_decisions.py"
ARRANGE = "Source/propose_arrangement.py"
AUTOMATE = "Source/apply_automation.py"

STUB = r'''
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ["PIPELINE_TEST_CALLS"]).open("a", encoding="ascii") as calls:
    calls.write(json.dumps(args) + "\n")

if args[0] == "-m":
    if os.environ.get("PIPELINE_TEST_RELAY_FAIL"):
        sys.exit(1)
    output = Path(args[args.index("--output") + 1])
    stems = output.parent / "_Stem Analysis"
    stems.mkdir()
    cache = {} if args[-1] == "legacy" else {
        "analysis": {"section_detector": "groove-1", "sections_schema": 2},
    }
    (stems / "SECTIONS_STEM_A.json").write_text(json.dumps(cache), encoding="ascii")
    layout = output / "Sections V7.als"
    layout.write_bytes(b"relay layout")
    print("Custom track order applied (3 tracks)")
    relay_log = os.environ.get("PIPELINE_TEST_RELAY_LOG")
    if relay_log == "trailing":
        print(f"Generated: {output / 'Sections V6.als'}")
    if relay_log != "missing":
        print(f"Generated: {layout}", flush=True)
    if relay_log == "trailing":
        print("Relay warning at exit\n", file=sys.stderr)
elif args[0] == "Source/extract_sections_als.py":
    layout = Path(args[1])
    review = layout.parent.parent / "Sections Review"
    review.mkdir(exist_ok=True)
    (review / (layout.stem.replace(" ", "_") + ".json")).write_text("{}", encoding="ascii")
elif args[0] == "Tools/plan_mix_decisions.py":
    if os.environ.get("PIPELINE_TEST_PLANNER_FAIL"):
        sys.exit(1)
    planned = {"transitions": [{"pair_index": 1}], "fallback_pairs": [{"pair_index": 2}]}
    if os.environ.get("PIPELINE_TEST_WHOLE_BAR"):
        planned["whole_bar_alternatives"] = json.loads(os.environ["PIPELINE_TEST_WHOLE_BAR"])
    Path(args[args.index("--out") + 1]).write_text(json.dumps(planned), encoding="ascii")
elif args[0] in {"Source/propose_arrangement.py", "Source/apply_automation.py"}:
    final_from = os.environ.get("PIPELINE_TEST_FINAL_FROM")
    real = final_from and args[0] == "Source/apply_automation.py"
    Path(args[3]).write_bytes(Path(final_from).read_bytes() if real else b"later phase")
elif args[0] == "Tools/check_kick_alignment.py":
    print("T01 A to B: WARN click +7.3 ms main +15.7 ms shared kicks 38 SLOW_ATTACK")
    print("LOOK AT THESE PICTURES: " + args[args.index("--out") + 1])
    if os.environ.get("PIPELINE_TEST_KICK_STDERR"):
        print(os.environ["PIPELINE_TEST_KICK_STDERR"], file=sys.stderr)
    sys.exit(int(os.environ.get("PIPELINE_TEST_KICK_RC", "0")))
elif args[0] == "Tools/check_bounce.py" and os.environ.get("PIPELINE_TEST_REAL_BOUNCE"):
    import subprocess
    root = os.environ["PIPELINE_TEST_ROOT"]
    sys.exit(subprocess.run([sys.executable, root + "/Tools/check_bounce.py", *args[1:]]).returncode)
elif args[0] == "Tools/check_bounce.py":
    rc = int(os.environ.get("PIPELINE_TEST_BOUNCE_RC", "0"))
    out = Path(args[args.index("--out") + 1])
    print("J01 A > B  0:10.0-0:50.0 | loud step +0.1 LU | a long per-join line")
    if rc in (0, 3):
        out.mkdir(parents=True, exist_ok=True)
        (out / "ATTENTION.md").write_text("NEEDS A LOOK: join 1 A > B\n", encoding="ascii")
        print("NEEDS A LOOK: join 1 A > B")
        print("BOUNCE CHECK: 1 of 2 joins need a look (J01): see " + out.as_posix() + "/ATTENTION.md")
    if rc == 2:                       # the real tool writes this itself when it could not run
        out.mkdir(parents=True, exist_ok=True)
        (out / "ATTENTION.md").write_text("bounce check did not run (stub): no measurements\n", encoding="ascii")
    if os.environ.get("PIPELINE_TEST_BOUNCE_STDERR"):
        print(os.environ["PIPELINE_TEST_BOUNCE_STDERR"], file=sys.stderr)
    sys.exit(rc)
elif args[0] == "Tools/fix_grid_phase.py" and os.environ.get("PIPELINE_TEST_REAL_TOOL"):
    import subprocess
    root = os.environ["PIPELINE_TEST_ROOT"]
    sys.exit(subprocess.run([sys.executable, root + "/Tools/fix_grid_phase.py", *args[1:]]).returncode)
elif args[0] == "Tools/fix_grid_phase.py":
    out = Path(args[args.index("--out") + 1])
    print("RESULT PHASED: " + str(out))
    if os.environ.get("PIPELINE_TEST_GRID_STDERR"):
        print(os.environ["PIPELINE_TEST_GRID_STDERR"], file=sys.stderr)
    rc = int(os.environ.get("PIPELINE_TEST_GRID_RC", "0"))
    if rc == 0 or os.environ.get("PIPELINE_TEST_GRID_WRITE_ANYWAY"):
        phased_from = os.environ.get("PIPELINE_TEST_PHASED_FROM")
        out.write_bytes(Path(args[1]).read_bytes() if os.environ.get("PIPELINE_TEST_GRID_SAME")
                        else Path(phased_from).read_bytes() if phased_from else b"phased als")
        if os.environ.get("PIPELINE_TEST_PHASED_BOUNCE"):
            wav = out.with_suffix(".wav")
            wav.write_bytes(b"a bounce of the phased file")
            import time
            os.utime(wav, (time.time() + 3600,) * 2)
        report = Path(args[args.index("--report") + 1])
        same = os.environ.get("PIPELINE_TEST_GRID_SAME")
        if rc == 0:
            report.mkdir(parents=True, exist_ok=True)
            if not os.environ.get("PIPELINE_TEST_GRID_NO_REPORT"):
                body = os.environ.get("PIPELINE_TEST_REPORT_JSON", '{"still_flamming": [], "edge_main_worse": []}')
                (report / "grid_phase_report.json").write_text(body)
        if rc == 0 and not os.environ.get("PIPELINE_TEST_GRID_NO_RECORD") and (not same or os.environ.get("PIPELINE_TEST_RECORD_ANYWAY")):
            record_from = os.environ.get("PIPELINE_TEST_RECORD_FROM")
            (report / "grid_phase.json").write_bytes(Path(record_from).read_bytes() if record_from else b'{"stub": "record"}')
    sys.exit(rc)
elif args[0] == "Source/validate_als.py" and "Phased" in args[1]:
    sys.exit(1 if os.environ.get("PIPELINE_TEST_PHASED_VALIDATE_FAIL") else 0)
elif args[0] == "Source/validate_mix_plan_als.py":
    if os.environ.get("PIPELINE_TEST_REAL_VALIDATOR"):
        import subprocess
        root = os.environ["PIPELINE_TEST_ROOT"]
        done = subprocess.run([sys.executable, root + "/Source/validate_mix_plan_als.py", *args[1:]],
                              env=dict(os.environ, PYTHONPATH=root + "/Source"))
        sys.exit(done.returncode)
    if "--grid-phase" in args:
        rc = int(os.environ.get("PIPELINE_TEST_PHASED_RECON_RC", "0"))
        body = '{"status": "FAIL", "error": "stub"}' if rc else "{}" if os.environ.get("PIPELINE_TEST_PHASED_RECON_NO_SECTION") else '{"grid_phase": {"track_count": 1}}'
        Path(args[args.index("--output") + 1]).write_text(body)
        sys.exit(rc)
    rc = int(os.environ.get("PIPELINE_TEST_RECON_RC", "0"))
    if rc == 0 and "--output" in args:
        Path(args[args.index("--output") + 1]).write_text('{"plain": true}')
    sys.exit(rc)
'''


def bash_executable():
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git:
        for parent in Path(git).parents:
            bash = parent / "bin" / "bash.exe"
            if bash.is_file():
                return str(bash)
    return None


@pytest.fixture
def pipeline(tmp_path):
    bash = bash_executable()
    if not bash:
        pytest.skip("Bash is required (Git Bash on Windows)")
    repo = tmp_path / "repo with spaces"
    for directory in ("Source", "Tools", OUTPUT, f"{PROJECT}/Hints"):
        (repo / directory).mkdir(parents=True)
    script = repo / "Tools" / "run_mix_pipeline.sh"
    shutil.copyfile(ROOT / "Tools" / script.name, script)
    shutil.copyfile(ROOT / "Source" / "section_format.py", repo / "Source" / "section_format.py")
    stub = tmp_path / "fake_pipeline.py"
    stub.write_text(STUB, encoding="ascii")
    bash_env = tmp_path / "bash-env"
    bash_env.write_bytes(b'''python() {
    if [[ "$1" == -c ]]; then
        "$PIPELINE_TEST_PYTHON" "$@"
    else
        "$PIPELINE_TEST_PYTHON" "$PIPELINE_TEST_STUB" "$@"
    fi
}
''')
    calls_path = tmp_path / "calls.jsonl"
    env = dict(os.environ, BASH_ENV=bash_env.as_posix(),
               PIPELINE_TEST_PYTHON=Path(sys.executable).as_posix(),
               PIPELINE_TEST_STUB=stub.as_posix(), PIPELINE_TEST_CALLS=str(calls_path),
               PIPELINE_TEST_ROOT=ROOT.as_posix())
    for name in ("DEC", "PLANNER", "RESUME", "SECTION_MODE"):
        env.pop(name, None)
    if os.name == "nt":
        env["PATH"] = str(Path(bash).parents[1] / "usr" / "bin") + os.pathsep + env["PATH"]

    def run(project="Test Mix", **options):
        calls_path.unlink(missing_ok=True)
        result = subprocess.run([bash, script.as_posix(), project, "A,B,C", "3"],
                                cwd=tmp_path, env={**env, **options}, capture_output=True,
                                text=True, timeout=60)
        calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
        return result, calls

    return repo, run


@pytest.mark.parametrize("options, arranger, decision_args", [
    ({"DEC": "My hand decisions.json", "PLANNER": "off"},
     "hand decisions My hand decisions.json", ["--decisions", "My hand decisions.json"]),
    ({"PLANNER": "off"}, "rule-based (PLANNER=off)", []),
    pytest.param({"PLANNER": "OFF"}, "rule-based (PLANNER=off)", [], id="planner-off-uppercase"),
    pytest.param({"PLANNER": "Off"}, "rule-based (PLANNER=off)", [], id="planner-off-mixed-case"),
    ({}, "planner (1 of 2 pairs planned, fallback: T2)",
     ["--decisions", PLANNED, "--decisions-drop-rejected-loops"]),
    pytest.param({"PLANNER": "office"}, "planner (1 of 2 pairs planned, fallback: T2)",
                 ["--decisions", PLANNED, "--decisions-drop-rejected-loops"], id="planner-non-off"),
    ({"SECTION_MODE": "legacy"}, "rule-based (legacy sections; the planner needs groove)", []),
])
def test_arranger_selection(pipeline, options, arranger, decision_args):
    repo, run = pipeline
    result, calls = run(**options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [line.split("arranger: ")[1] for line in result.stdout.splitlines()
            if "arranger: " in line] == [arranger]
    assert [call for call in calls if call[0] == ARRANGE] == [[
        ARRANGE, LAYOUT, SECTIONS, f"{OUTPUT}/Sections V8.als",
        "--hints", f"{PROJECT}/Hints/track_hints.json", "--tempo-arc", *decision_args,
        "--mix-plan", f"{OUTPUT}/MIX_PLAN.json", "--report", f"{OUTPUT}/ARRANGEMENT_REPORT.json",
    ]]
    planner_calls = [call for call in calls if call[0] == PLANNER]
    if "--decisions-drop-rejected-loops" in decision_args:
        planner_call = [PLANNER, PROJECT, "--sections", SECTIONS, "--out", PLANNED,
                        "--content", f"{OUTPUT}/planner_content.json", "--allow-partial"]
        assert planner_calls == [planner_call]
        hints_gate = next(i for i, call in enumerate(calls) if call[0] == "Source/validate_hints_vs_sections.py")
        assert hints_gate < calls.index(planner_call) < next(i for i, call in enumerate(calls) if call[0] == ARRANGE)
    else:
        assert not planner_calls
    assert (repo / OUTPUT / "Sections V9.als").is_file()


@pytest.mark.parametrize("rows, tail", [
    ('[{"pair_index": 3, "cost_difference": 0.5563}]', ", whole-bar alternative: T3 (+0.56)"),
    ('[{"pair_index": 1, "cost_difference": 0.5}, {"pair_index": 4, "cost_difference": -3}]',
     ", whole-bar alternative: T1 (+0.50),T4 (-3.00)"),
    ("[]", ""),
])
def test_summary_names_a_pair_that_used_a_whole_bar_alternative(pipeline, rows, tail):
    repo, run = pipeline
    result, calls = run(PIPELINE_TEST_WHOLE_BAR=rows)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"arranger: planner (1 of 2 pairs planned, fallback: T2{tail})" in result.stdout


def test_absolute_project_path_outside_test_project(pipeline, tmp_path):
    repo, run = pipeline
    project = tmp_path / "bulk drive" / "Abs Mix"
    (project / "Output").mkdir(parents=True)
    (project / "Hints").mkdir()
    result, calls = run(project.as_posix())
    assert result.returncode == 0, result.stdout + result.stderr
    relay_call = next(call for call in calls if call[:2] == ["-m", "automated_dj_mixes.orchestrator"])
    assert "--no-previews" in relay_call
    assert (project / "Output" / "Sections V9.als").is_file()
    assert not (repo / "Test Project" / project.name).exists()
    assert [call[1] for call in calls if call[0] == PLANNER] == [project.as_posix()]


@pytest.mark.parametrize("options, mode_args", [
    pytest.param({}, ["--section-mode", "groove"], id="fresh"),
    pytest.param({"RESUME": "1"}, [], id="resume-without-override"),
    pytest.param({"RESUME": "1", "SECTION_MODE": "legacy"},
                 ["--section-mode", "legacy"], id="resume-with-override"),
])
def test_phase3_argv(pipeline, options, mode_args):
    _, run = pipeline
    if "RESUME" in options:
        result, _ = run(SECTION_MODE="legacy")
        assert result.returncode == 0, result.stdout + result.stderr
    result, calls = run(**options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call for call in calls if call[0] == AUTOMATE] == [[
        AUTOMATE, f"{OUTPUT}/Sections V8.als", SECTIONS, f"{OUTPUT}/Sections V9.als",
        f"{OUTPUT}/ARRANGEMENT_REPORT.json", f"{OUTPUT}/MIX_PLAN.json", *mode_args,
    ]]


def test_failed_relay_clears_previous_record(pipeline):
    repo, run = pipeline
    result, _ = run()
    assert result.returncode == 0, result.stdout + result.stderr
    record = repo / OUTPUT / "relay-layout.txt"
    assert record.is_file()

    result, _ = run(PIPELINE_TEST_RELAY_FAIL="1")
    assert result.returncode == 1
    assert "FAILED at: relay (see relay.log)" in result.stdout
    assert not record.exists()

    result, calls = run(RESUME="1")
    assert result.returncode == 1
    assert "relay layout record is missing or stale" in result.stdout
    assert not calls


def test_relay_uses_last_generated_line_despite_trailing_output(pipeline):
    repo, run = pipeline
    result, calls = run(PIPELINE_TEST_RELAY_LOG="trailing")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "relay produced Sections V7.als" in result.stdout
    assert [call[1] for call in calls if call[0] == ARRANGE] == [LAYOUT]
    assert (repo / OUTPUT / "Sections V9.als").is_file()


def test_relay_rejects_missing_generated_line(pipeline):
    repo, run = pipeline
    result, calls = run(PIPELINE_TEST_RELAY_LOG="missing")
    assert result.returncode == 1
    assert "FAILED at: relay did not report a layout (see relay.log)" in result.stdout
    assert [call[0] for call in calls] == ["-m"]
    assert not (repo / OUTPUT / "relay-layout.txt").exists()


@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
def test_pipeline_creates_missing_output_directory(pipeline, absolute):
    repo, run = pipeline
    project = repo / PROJECT
    output = project / "Output"
    output.rmdir()

    result, _ = run(project.as_posix() if absolute else project.name)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DONE: final = Sections V9.als" in (output / "runmix.log").read_text()
    assert (output / "Sections V9.als").is_file()


@pytest.mark.parametrize("mode", ["groove", "legacy"])
def test_resume_reuses_relay_layout_after_full_run(pipeline, mode):
    repo, run = pipeline
    result, _ = run(SECTION_MODE=mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all((repo / OUTPUT / f"Sections V{version}.als").is_file() for version in (7, 8, 9))
    result, calls = run(RESUME="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "resuming from relay layout Sections V7.als" in result.stdout
    assert [call[1:3] for call in calls if call[0] == ARRANGE] == [[LAYOUT, SECTIONS]]
    assert [call[1] for call in calls if call[0] == "Source/extract_sections_als.py"] == [LAYOUT]
    assert not any(call[0] in {"-m", "Source/stem_detector.py"} for call in calls)
    assert sum(call[0] == PLANNER for call in calls) == (mode == "groove")
    if mode == "legacy":
        assert "arranger: rule-based (legacy sections; the planner needs groove)" in result.stdout


@pytest.mark.parametrize("problem", ["missing", "malformed", "changed layout"])
def test_resume_rejects_missing_or_stale_record(pipeline, problem):
    repo, run = pipeline
    result, _ = run()
    assert result.returncode == 0, result.stdout + result.stderr
    record = repo / OUTPUT / "relay-layout.txt"
    if problem == "missing":
        record.unlink()
    elif problem == "malformed":
        record.write_text("invalid", encoding="ascii")
    else:
        (repo / LAYOUT).write_bytes(b"changed layout")
    result, calls = run(RESUME="1")
    assert result.returncode == 1
    assert "relay layout record is missing or stale" in result.stdout
    assert not calls


def test_mixed_cache_stops_pipeline(pipeline):
    repo, run = pipeline
    result, _ = run()
    assert result.returncode == 0, result.stdout + result.stderr
    (repo / PROJECT / "_Stem Analysis" / "SECTIONS_STEM_B.json").write_text("{}", encoding="ascii")
    result, calls = run(RESUME="1")
    assert result.returncode == 1
    assert not any(call[0] in {PLANNER, ARRANGE} for call in calls)
    assert "mixed legacy and groove analyses" in (repo / OUTPUT / "runmix.log").read_text()


def test_planner_error_stops_pipeline(pipeline):
    _, run = pipeline
    result, calls = run(PIPELINE_TEST_PLANNER_FAIL="1")
    assert result.returncode == 1
    assert "FAILED at: planner (see planner.log)" in result.stdout
    assert any(call[0] == PLANNER for call in calls)
    assert not any(call[0] == ARRANGE for call in calls)


KICK = "Tools/check_kick_alignment.py"


def test_kick_alignment_runs_once_after_the_final_validate_and_prints_into_the_log(pipeline):
    repo, run = pipeline
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    kick_calls = [call for call in calls if call[0] == KICK]
    assert kick_calls == [[KICK, f"{OUTPUT}/Sections V9.als", "--audio", f"{PROJECT}/Audio",
                           "--out", f"{OUTPUT}/Kick Alignment", "--mode", "warn"]]
    finals = [i for i, call in enumerate(calls) if call[0] == "Source/validate_als.py" and call[1] == FINAL and "--require-devices" in call]
    assert finals and finals[-1] < calls.index(kick_calls[0])
    log = (repo / OUTPUT / "runmix.log").read_text()
    assert "T01 A to B: WARN click +7.3 ms main +15.7 ms shared kicks 38 SLOW_ATTACK" in log
    assert f"LOOK AT THESE PICTURES: {OUTPUT}/Kick Alignment" in log
    assert "LOOK AT THESE PICTURES" in result.stdout
    assert "DONE: final = Sections V9.als" in result.stdout


def test_kick_alignment_that_cannot_run_does_not_break_a_warn_build(pipeline):
    _, run = pipeline
    result, calls = run(PIPELINE_TEST_KICK_RC="2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "kick alignment check did not finish (status 2); warn mode, the build goes on" in result.stdout
    assert "DONE: final = Sections V9.als" in result.stdout
    assert any(call[0] == "Source/validate_mix_plan_als.py" for call in calls)


def test_kick_alignment_in_fail_mode_stops_the_build(pipeline):
    _, run = pipeline
    result, calls = run(KICK_ALIGNMENT_MODE="fail", PIPELINE_TEST_KICK_RC="1")
    assert result.returncode == 1
    assert "FAILED at: kick alignment FAIL" in result.stdout
    assert "could not run" not in result.stdout
    assert [call for call in calls if call[0] == KICK][0][-2:] == ["--mode", "fail"]
    assert not any(call[0] == "Source/validate_mix_plan_als.py" for call in calls)
    assert "DONE" not in result.stdout


def test_kick_alignment_in_fail_mode_lets_a_clean_check_through(pipeline):
    _, run = pipeline
    result, _ = run(KICK_ALIGNMENT_MODE="fail")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DONE: final = Sections V9.als" in result.stdout


def test_kick_alignment_mode_other_than_fail_is_warn(pipeline):
    _, run = pipeline
    result, calls = run(KICK_ALIGNMENT_MODE="banana", PIPELINE_TEST_KICK_RC="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call for call in calls if call[0] == KICK][0][-2:] == ["--mode", "warn"]


def test_kick_alignment_that_could_not_run_is_not_reported_as_a_kick_failure_in_fail_mode(pipeline):
    _, run = pipeline
    result, calls = run(KICK_ALIGNMENT_MODE="fail", PIPELINE_TEST_KICK_RC="2")
    assert result.returncode == 1
    assert "FAILED at: kick alignment could not run (status 2)" in result.stdout
    assert "kick alignment FAIL" not in result.stdout
    assert not any(call[0] == "Source/validate_mix_plan_als.py" for call in calls)


def test_kick_alignment_stderr_reaches_the_log(pipeline):
    repo, run = pipeline
    result, _ = run(PIPELINE_TEST_KICK_STDERR="ParseError: something broke", PIPELINE_TEST_KICK_RC="2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ParseError: something broke" in (repo / OUTPUT / "runmix.log").read_text()


@pytest.mark.parametrize("status, message", [
    (1, "FAILED at: kick alignment FAIL"),
    (2, "FAILED at: kick alignment could not run (status 2)"),
    (3, "FAILED at: kick alignment judged nothing (status 3"),
    (127, "FAILED at: kick alignment could not run (status 127)"),
])
def test_kick_alignment_fail_mode_says_which_status_it_was(pipeline, status, message):
    _, run = pipeline
    result, _ = run(KICK_ALIGNMENT_MODE="fail", PIPELINE_TEST_KICK_RC=str(status))
    assert result.returncode == 1
    assert message in result.stdout
    others = [m for s, m in [(1, "kick alignment FAIL"), (2, "could not run (status 2)"), (3, "judged nothing")] if s != status]
    assert not any(m in result.stdout for m in others)


def test_kick_alignment_status_3_does_not_stop_a_warn_build(pipeline):
    _, run = pipeline
    result, _ = run(PIPELINE_TEST_KICK_RC="3")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "kick alignment check did not finish (status 3); warn mode, the build goes on" in result.stdout


GRID = "Tools/fix_grid_phase.py"
FINAL = f"{OUTPUT}/Sections V9.als"
PHASED = f"{OUTPUT}/Sections V9 Phased.als"
RECONCILE = "Source/validate_mix_plan_als.py"
PHASED_LINE = ("grid phase: phased file written, open it instead of Sections V9.als if the report shows it is tighter; "
               "the MixPlan reconciliation covers the unphased original (the phased markers differ from the original by "
               "the corrections shown in the report: drift up to 12 ms, slide up to 15 ms)")


def _reconciliations(calls):
    return [call for call in calls if call[0] == RECONCILE]


def test_grid_phase_runs_once_after_the_kick_alignment_and_never_changes_the_final(pipeline):
    repo, run = pipeline
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    grid_calls = [call for call in calls if call[0] == GRID]
    assert grid_calls == [[GRID, FINAL, "--audio", f"{PROJECT}/Audio", "--out", PHASED, "--report", f"{OUTPUT}/Grid Phase"]]
    assert calls.index([call for call in calls if call[0] == KICK][0]) < calls.index(grid_calls[0])
    assert f"grid phase fix (suggest): report in {OUTPUT}/Grid Phase" in result.stdout
    assert PHASED_LINE in result.stdout and PHASED_LINE in (repo / OUTPUT / "runmix.log").read_text()
    assert "RESULT PHASED" in (repo / OUTPUT / "runmix.log").read_text()
    assert (repo / PHASED).is_file() and (repo / FINAL).read_bytes() == b"later phase"
    assert "DONE: final = Sections V9.als" in result.stdout
    assert [call[3] for call in _reconciliations(calls)] == [FINAL]
    assert not any("Phased" in " ".join(call) for call in calls if call[0] == RECONCILE)       # the reconciliation never sees it


@pytest.mark.parametrize("value", ["suggest", "banana", "Apply", "APPLY", "applyy", "", "apply "])
def test_any_grid_mode_but_off_and_exactly_apply_is_suggest(pipeline, value):
    _, run = pipeline
    result, calls = run(GRID_PHASE_MODE=value)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "grid phase fix (suggest)" in result.stdout and "DONE: final = Sections V9.als" in result.stdout
    assert [call[3] for call in _reconciliations(calls)] == [FINAL]
    assert "APPLIED" not in result.stdout and "grid phase applied" not in result.stdout
    assert not [call for call in calls if "--grid-phase" in call]


@pytest.mark.parametrize("value", ["off", "OFF", "Off", "oFF"])
def test_grid_mode_off_skips_the_step_with_one_line(pipeline, value):
    repo, run = pipeline
    result, calls = run(GRID_PHASE_MODE=value)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(call[0] == GRID for call in calls) and not (repo / PHASED).exists()
    assert result.stdout.count("grid phase") == 1
    assert "grid phase fix: off (GRID_PHASE_MODE=off), step skipped" in result.stdout
    assert "DONE: final = Sections V9.als" in result.stdout


@pytest.mark.parametrize("status, message", [
    (2, "grid phase fix did not finish (status 2); no phased file, the final is Sections V9.als"),
    (4, "grid phase fix REFUSED its own result (status 4"),
    (127, "grid phase fix did not finish (status 127)"),
])
def test_a_grid_phase_tool_that_fails_never_stops_the_build(pipeline, status, message):
    repo, run = pipeline
    result, calls = run(PIPELINE_TEST_GRID_RC=str(status), PIPELINE_TEST_GRID_STDERR="Traceback: boom")
    assert result.returncode == 0, result.stdout + result.stderr
    assert message in result.stdout and "DONE: final = Sections V9.als" in result.stdout
    assert "Traceback: boom" in (repo / OUTPUT / "runmix.log").read_text()
    assert PHASED_LINE not in result.stdout and not (repo / PHASED).exists()
    assert [call[3] for call in _reconciliations(calls)] == [FINAL]


def test_a_stale_phased_file_from_an_earlier_build_is_removed_when_the_tool_fails(pipeline):
    repo, run = pipeline
    (repo / PHASED).write_bytes(b"old phased file")
    result, _ = run(PIPELINE_TEST_GRID_RC="2")
    assert result.returncode == 0 and not (repo / PHASED).exists()


def test_a_phased_file_identical_to_the_final_is_reported_as_nothing_to_change(pipeline):
    _, run = pipeline
    result, _ = run(PIPELINE_TEST_GRID_SAME="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "grid phase: nothing to change" in result.stdout and PHASED_LINE not in result.stdout


@pytest.mark.parametrize("status", [1, 2, 3])
def test_grid_phase_runs_even_when_the_kick_alignment_fails_the_build_and_the_failure_still_stops_it(pipeline, status):
    repo, run = pipeline
    result, calls = run(KICK_ALIGNMENT_MODE="fail", PIPELINE_TEST_KICK_RC=str(status))
    assert result.returncode == 1
    assert [call[0] for call in calls].count(GRID) == 1 and (repo / PHASED).is_file()
    assert "FAILED at: kick alignment" in result.stdout and "DONE" not in result.stdout
    assert result.stdout.index("grid phase") < result.stdout.index("FAILED at: kick alignment")
    assert not _reconciliations(calls)                 # the build stopped before the reconciliation


def test_stale_refused_and_partial_files_of_an_earlier_build_are_removed(pipeline):
    repo, run = pipeline
    for name in ("Sections V9 Phased.als", "Sections V9 Phased REFUSED.als", "Sections V9 Phased.als.partial"):
        (repo / OUTPUT / name).write_bytes(b"old")
    result, _ = run(PIPELINE_TEST_GRID_RC="4")
    assert result.returncode == 0
    assert [p.name for p in (repo / OUTPUT).glob("Sections V9 Phased*")] == []


def test_a_tool_that_fails_after_writing_the_phased_file_leaves_no_phased_file(pipeline):
    repo, run = pipeline
    result, _ = run(PIPELINE_TEST_GRID_RC="2", PIPELINE_TEST_GRID_WRITE_ANYWAY="1")
    assert result.returncode == 0 and not (repo / PHASED).exists()


def test_the_phased_file_is_validated_and_deleted_loudly_when_it_fails(pipeline):
    repo, run = pipeline
    result, calls = run(PIPELINE_TEST_PHASED_VALIDATE_FAIL="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GRID PHASE FILE FAILED VALIDATION and was deleted" in result.stdout and PHASED_LINE not in result.stdout
    assert not (repo / PHASED).exists() and "DONE: final = Sections V9.als" in result.stdout
    assert [call for call in calls if call[0] == "Source/validate_als.py" and call[1] == PHASED] == [PHASED_VALIDATE]


def test_a_valid_phased_file_is_validated_before_the_line_is_printed(pipeline):
    _, run = pipeline
    result, calls = run()
    assert [call for call in calls if call[0] == "Source/validate_als.py" and call[1] == PHASED] == [PHASED_VALIDATE]
    assert PHASED_LINE in result.stdout


# ---------------------------------------------------------------- apply mode: the phased file becomes the final only when everything passes

RECORD = f"{OUTPUT}/Grid Phase/grid_phase.json"
PHASED_RECON = f"{OUTPUT}/MIX_PLAN_RECONCILIATION_PHASED.json"
PHASED_RECONCILIATION_CALL = [RECONCILE, f"{OUTPUT}/MIX_PLAN.json", f"{OUTPUT}/ARRANGEMENT_REPORT.json", PHASED,
                              "--grid-phase", RECORD, "--grid-phase-original", FINAL, "--output", PHASED_RECON]
PHASED_VALIDATE = ["Source/validate_als.py", PHASED, "--expected-tracks", "3", "--require-devices",
                   "--arrangement-report", f"{OUTPUT}/ARRANGEMENT_REPORT.json"]
RECON = f"{OUTPUT}/MIX_PLAN_RECONCILIATION.json"
RECON_ORIGINAL = f"{OUTPUT}/MIX_PLAN_RECONCILIATION_ORIGINAL.json"
PHASED_FAILED = f"{OUTPUT}/MIX_PLAN_RECONCILIATION_PHASED.FAILED.json"
APPLIED = "DONE: final = Sections V9 Phased.als (grid phase applied)"


def _not_applied(reason):
    return f"DONE: final = Sections V9.als (grid phase NOT applied: {reason})"


def _phased_reconciliations(calls):
    return [call for call in calls if call[0] == RECONCILE and "--grid-phase" in call]


def test_apply_makes_the_phased_file_the_final_when_everything_passes(pipeline):
    repo, run = pipeline
    result, calls = run(GRID_PHASE_MODE="apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert APPLIED in result.stdout and APPLIED in (repo / OUTPUT / "runmix.log").read_text()
    assert "grid phase fix (apply): report in" in result.stdout
    assert _reconciliations(calls)[0][3] == FINAL and "--grid-phase" not in _reconciliations(calls)[0]      # the original still reconciles first
    assert _phased_reconciliations(calls) == [PHASED_RECONCILIATION_CALL] and len(_reconciliations(calls)) == 2
    assert not (repo / PHASED_RECON).exists() and (repo / RECON).is_file() and (repo / RECON_ORIGINAL).is_file()      # adopted: renamed
    assert (repo / PHASED).is_file() and (repo / FINAL).read_bytes() == b"later phase"
    assert "final = Sections V9 Phased.als (grid phase applied)" in result.stdout


def test_apply_runs_the_checks_in_order_validate_then_reconcile_original_then_reconcile_phased(pipeline):
    _, run = pipeline
    _, calls = run(GRID_PHASE_MODE="apply")
    names = [(call[0], call[1]) for call in calls]
    validate = names.index(("Source/validate_als.py", PHASED))
    plain = [i for i, call in enumerate(calls) if call[0] == RECONCILE and "--grid-phase" not in call][0]
    phased = [i for i, call in enumerate(calls) if call[0] == RECONCILE and "--grid-phase" in call][0]
    assert [call[0] for call in calls].index(GRID) < validate < plain < phased


@pytest.mark.parametrize("rc", [1, 2, 127])
def test_apply_keeps_the_original_as_final_when_the_phased_reconciliation_fails(pipeline, rc):
    repo, run = pipeline
    result, calls = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_PHASED_RECON_RC=str(rc))
    assert result.returncode == 0, result.stdout + result.stderr
    reason = "the MixPlan reconciliation of the phased file failed (details in runmix.log, kept as MIX_PLAN_RECONCILIATION_PHASED.FAILED.json)"
    assert _not_applied(reason) in result.stdout
    assert APPLIED not in result.stdout and "grid phase applied" not in result.stdout
    assert not (repo / PHASED_RECON).exists()
    assert json.loads((repo / PHASED_FAILED).read_text())["status"] == "FAIL"       # the evidence is kept
    assert json.loads((repo / RECON).read_text()) == {"plain": True} and not (repo / RECON_ORIGINAL).exists()    # nothing renamed
    assert len(_phased_reconciliations(calls)) == 1


def test_apply_does_not_trust_a_phased_reconciliation_that_has_no_grid_phase_section(pipeline):
    repo, run = pipeline
    result, _ = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_PHASED_RECON_NO_SECTION="1")
    assert result.returncode == 0
    assert _not_applied("the phased reconciliation passed without a grid_phase section (not trusted)") in result.stdout
    assert not (repo / PHASED_RECON).exists() and (repo / PHASED_FAILED).is_file()
    assert json.loads((repo / RECON).read_text()) == {"plain": True} and not (repo / RECON_ORIGINAL).exists()


@pytest.mark.parametrize("options, reason", [
    ({"PIPELINE_TEST_GRID_RC": "2"}, "the grid phase tool did not finish (status 2)"),
    ({"PIPELINE_TEST_GRID_RC": "4"}, "the grid phase tool refused its own result (status 4)"),
    ({"PIPELINE_TEST_GRID_SAME": "1"}, "nothing to change (the phased file is a copy of Sections V9.als)"),
    ({"PIPELINE_TEST_PHASED_VALIDATE_FAIL": "1"}, "the phased file failed validate_als.py"),
    ({"PIPELINE_TEST_GRID_NO_RECORD": "1"}, "the grid phase tool wrote no record (Output/Grid Phase/grid_phase.json)"),
])
def test_apply_keeps_the_original_when_an_earlier_step_did_not_give_a_valid_phased_file_and_record(pipeline, options, reason):
    repo, run = pipeline
    result, calls = run(GRID_PHASE_MODE="apply", **options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _not_applied(reason) in result.stdout
    assert not _phased_reconciliations(calls) and not (repo / PHASED_RECON).exists()
    assert [call[3] for call in _reconciliations(calls)] == [FINAL]
    assert (repo / FINAL).read_bytes() == b"later phase"


def test_apply_without_a_tool_run_leaves_no_phased_file_or_record_behind(pipeline):
    repo, run = pipeline
    for path in (PHASED, RECORD, PHASED_RECON):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(b"left by an earlier build")
    result, _ = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_GRID_RC="2")
    assert result.returncode == 0 and not (repo / PHASED).exists() and not (repo / RECORD).exists() and not (repo / PHASED_RECON).exists()


@pytest.mark.parametrize("mode", ["suggest", "apply"])
def test_a_stale_record_and_phased_reconciliation_never_survive_a_new_build(pipeline, mode):
    repo, run = pipeline
    for path in (RECORD, PHASED_RECON):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(b"left by an earlier build")
    result, _ = run(GRID_PHASE_MODE=mode, PIPELINE_TEST_GRID_SAME="1")
    assert result.returncode == 0 and not (repo / RECORD).exists() and not (repo / PHASED_RECON).exists()


def test_apply_never_runs_when_the_original_reconciliation_fails_and_the_build_fails(pipeline):
    repo, run = pipeline
    result, calls = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_RECON_RC="1")
    assert result.returncode == 1 and "FAILED at: reconciliation" in result.stdout and "DONE" not in result.stdout
    assert not _phased_reconciliations(calls)


def test_apply_does_not_decide_when_the_kick_alignment_stops_the_build(pipeline):
    result, calls = pipeline[1](GRID_PHASE_MODE="apply", KICK_ALIGNMENT_MODE="fail", PIPELINE_TEST_KICK_RC="1")
    assert result.returncode == 1 and "FAILED at: kick alignment" in result.stdout and "DONE" not in result.stdout
    assert not _reconciliations(calls)


@pytest.mark.parametrize("value", ["off", "OFF", "suggest"])
def test_off_and_suggest_never_ask_for_a_phased_reconciliation(pipeline, value):
    _, run = pipeline
    result, calls = run(GRID_PHASE_MODE=value)
    assert not _phased_reconciliations(calls) and "grid phase applied" not in result.stdout
    assert "DONE: final = Sections V9.als\n" in result.stdout


# the real validator behind the hook: real files, real exit codes

@pytest.fixture
def real_case(pipeline, tmp_path, monkeypatch):
    from test_grid_phase_apply import make_case
    (tmp_path / "case").mkdir()
    case = make_case(tmp_path / "case", monkeypatch)
    repo, run = pipeline
    (repo / OUTPUT / "MIX_PLAN.json").write_bytes(case.plan.read_bytes())
    (repo / OUTPUT / "ARRANGEMENT_REPORT.json").write_bytes(case.report.read_bytes())
    options = dict(PIPELINE_TEST_REAL_VALIDATOR="1", PIPELINE_TEST_FINAL_FROM=str(case.original),
                   PIPELINE_TEST_PHASED_FROM=str(case.phased), PIPELINE_TEST_RECORD_FROM=str(case.record),
                   GRID_PHASE_MODE="apply")
    return repo, run, case, options


def test_the_real_validator_applies_a_phased_file_with_its_record(real_case):
    repo, run, case, options = real_case
    result, calls = run(**options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert APPLIED in result.stdout
    recon = json.loads((repo / RECON).read_text())
    assert recon["status"] == "PASS" and recon["grid_phase"]["track_count"] == 1
    assert "grid_phase" not in json.loads((repo / RECON_ORIGINAL).read_text())          # the original's own reconciliation, kept


def test_the_real_validator_keeps_the_original_when_the_record_is_tampered(real_case, tmp_path):
    repo, run, case, options = real_case
    record = json.loads(case.record.read_text())
    record["tracks"][0]["original_markers"][1][0] += 1e-6            # one microsecond: no longer the pinned grid
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(record))
    result, _ = run(**dict(options, PIPELINE_TEST_RECORD_FROM=str(forged)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "(grid phase NOT applied: the MixPlan reconciliation of the phased file failed" in result.stdout
    assert "grid phase applied" not in result.stdout and not (repo / PHASED_RECON).exists()
    log = (repo / OUTPUT / "runmix.log").read_text()
    assert "grid phase record does not hold the pinned original warp grid" in log


def test_the_real_validator_keeps_the_original_when_the_record_is_for_another_phased_file(real_case, tmp_path, monkeypatch):
    repo, run, case, options = real_case
    from test_grid_phase_apply import make_case
    (tmp_path / "other").mkdir()
    other = make_case(tmp_path / "other", monkeypatch, shifts={0: 6.0}).phased       # a different phasing of the same original
    result, _ = run(**dict(options, PIPELINE_TEST_PHASED_FROM=str(other)))
    assert result.returncode == 0 and "grid phase NOT applied" in result.stdout
    assert "belongs to another ALS" in (repo / OUTPUT / "runmix.log").read_text()


def test_the_real_tool_behind_the_hook_writes_the_record_where_the_hook_looks_for_it(pipeline, tmp_path):
    import hashlib
    from test_grid_phase_fix import make_mix
    repo, run = pipeline
    (tmp_path / "set").mkdir()
    als, audio = make_mix(tmp_path / "set", "tiny", {"A": 0.0, "B": 8.0}, kick_beats={"B": range(0, 30)})
    shutil.copytree(audio, repo / PROJECT / "Audio")
    result, calls = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_REAL_TOOL="1", PIPELINE_TEST_FINAL_FROM=str(als))
    assert result.returncode == 0, result.stdout + result.stderr
    assert APPLIED in result.stdout and _phased_reconciliations(calls) == [PHASED_RECONCILIATION_CALL]
    record = json.loads((repo / RECORD).read_text())
    assert record["schema"] == "grid-phase/1" and record["tracks"]
    assert record["phased_als_sha256"] == hashlib.sha256((repo / PHASED).read_bytes()).hexdigest()
    assert record["original_als_sha256"] == hashlib.sha256((repo / FINAL).read_bytes()).hexdigest()


# ---------------------------------------------------------------- fix round 1

STALE = (PHASED, f"{OUTPUT}/Sections V9 Phased REFUSED.als", f"{OUTPUT}/Sections V9 Phased.als.partial", RECORD,
         PHASED_RECON, PHASED_FAILED, RECON_ORIGINAL)


@pytest.mark.parametrize("mode", ["off", "OFF", "suggest", "apply"])
def test_every_mode_clears_what_an_earlier_build_left(pipeline, mode):
    repo, run = pipeline
    for path in STALE:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(b"left by an earlier build")
    result, _ = run(GRID_PHASE_MODE=mode, PIPELINE_TEST_GRID_RC="2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [path for path in STALE if (repo / path).exists()] == []


def test_a_record_the_tool_left_beside_an_unchanged_file_is_removed(pipeline):
    # a tool that writes a record next to a byte copy of the final: the record must not survive (the stale-file sweep
    # before the tool runs cannot help, the tool wrote it after)
    repo, run = pipeline
    result, _ = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_GRID_SAME="1", PIPELINE_TEST_RECORD_ANYWAY="1")
    assert result.returncode == 0 and "nothing to change" in result.stdout
    assert not (repo / RECORD).exists()


@pytest.mark.parametrize("mode", ["suggest", "apply"])
def test_the_phased_file_is_validated_with_the_flags_of_the_original(pipeline, mode):
    _, run = pipeline
    _, calls = run(GRID_PHASE_MODE=mode)
    assert [call for call in calls if call[0] == "Source/validate_als.py" and call[1] == PHASED] == [PHASED_VALIDATE]
    original = [call for call in calls if call[0] == "Source/validate_als.py" and call[1] == FINAL and "--require-devices" in call]
    assert original and original[0][2:] == PHASED_VALIDATE[2:]


def test_adopting_the_phased_file_makes_the_canonical_reconciliation_describe_the_final(pipeline):
    import hashlib
    repo, run = pipeline
    result, _ = run(GRID_PHASE_MODE="apply")
    assert result.returncode == 0 and APPLIED in result.stdout
    assert json.loads((repo / RECON).read_text()) == {"grid_phase": {"track_count": 1}}       # the phased one is canonical
    assert json.loads((repo / RECON_ORIGINAL).read_text()) == {"plain": True}
    assert not (repo / PHASED_RECON).exists() and not (repo / PHASED_FAILED).exists()
    digest = hashlib.sha256((repo / PHASED).read_bytes()).hexdigest()
    line = [l for l in result.stdout.splitlines() if "final ALS:" in l][0]
    assert line.endswith(f"Sections V9 Phased.als sha256 {digest}") and os.path.isabs(line.split("final ALS: ")[1].split(" sha256")[0])


def test_when_the_phased_file_is_not_adopted_the_final_line_names_the_original_and_nothing_is_renamed(pipeline):
    import hashlib
    repo, run = pipeline
    result, _ = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_PHASED_RECON_RC="1")
    line = [l for l in result.stdout.splitlines() if "final ALS:" in l][0]
    assert line.endswith(f"Sections V9.als sha256 {hashlib.sha256((repo / FINAL).read_bytes()).hexdigest()}")
    assert json.loads((repo / RECON).read_text()) == {"plain": True} and not (repo / RECON_ORIGINAL).exists()


def test_suggest_and_off_say_nothing_new_and_rename_nothing(pipeline):
    repo, run = pipeline
    for mode in ("suggest", "off"):
        result, _ = run(GRID_PHASE_MODE=mode)
        assert "final ALS:" not in result.stdout and not (repo / RECON_ORIGINAL).exists()
        assert json.loads((repo / RECON).read_text()) == {"plain": True}


@pytest.mark.parametrize("report, extra", [
    ('{"still_flamming": [{"index": 7, "half": "first_half", "predicted": 9.0}], "edge_main_worse": []}',
     "; still flamming: T07"),
    ('{"still_flamming": [{"index": 7}, {"index": 3}, {"index": 7}], "edge_main_worse": []}', "; still flamming: T03,T07"),
    ('{"still_flamming": [], "edge_main_worse": [{"label": "T02 No Panties to Taste", "estimator": "edge", "before": 0.5, "after": 4.7}]}',
     "; edge or main hit worse: T02 edge"),
    ('{"still_flamming": [{"index": 7}], "edge_main_worse": [{"label": "T02 A to B", "estimator": "edge"}, {"label": "T05 C to D", "estimator": "main"}]}',
     "; still flamming: T07; edge or main hit worse: T02 edge, T05 main"),
])
def test_the_final_line_names_every_blend_the_tool_could_not_make_clean(pipeline, report, extra):
    _, run = pipeline
    result, _ = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_REPORT_JSON=report)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"DONE: final = Sections V9 Phased.als (grid phase applied{extra})" in result.stdout


@pytest.mark.parametrize("options", [
    {"PIPELINE_TEST_GRID_NO_REPORT": "1"},
    {"PIPELINE_TEST_REPORT_JSON": "{not json"},
    {"PIPELINE_TEST_REPORT_JSON": '{"edge_main_worse": []}'},
])
def test_an_unreadable_tool_report_keeps_the_original(pipeline, options):
    repo, run = pipeline
    result, _ = run(GRID_PHASE_MODE="apply", **options)
    assert result.returncode == 0
    assert "grid phase NOT applied: Output/Grid Phase/grid_phase_report.json is missing or unreadable" in result.stdout
    assert json.loads((repo / RECON).read_text()) == {"plain": True} and not (repo / RECON_ORIGINAL).exists()
    assert (repo / PHASED_FAILED).is_file()


def test_a_project_path_with_an_ampersand_and_a_space(pipeline):
    repo, run = pipeline
    name = "Fish & Chips Mix"
    for directory in ("Output", "Hints"):
        (repo / "Test Project" / name / directory).mkdir(parents=True)
    result, calls = run(project=name, GRID_PHASE_MODE="apply")
    assert result.returncode == 0, result.stdout + result.stderr
    base = f"Test Project/{name}/Output"
    assert f"DONE: final = Sections V9 Phased.als (grid phase applied)" in result.stdout
    assert [call for call in calls if call[0] == RECONCILE and "--grid-phase" in call] == [
        [RECONCILE, f"{base}/MIX_PLAN.json", f"{base}/ARRANGEMENT_REPORT.json", f"{base}/Sections V9 Phased.als",
         "--grid-phase", f"{base}/Grid Phase/grid_phase.json", "--grid-phase-original", f"{base}/Sections V9.als",
         "--output", f"{base}/MIX_PLAN_RECONCILIATION_PHASED.json"]]
    assert (repo / "Test Project" / name / "Output" / "MIX_PLAN_RECONCILIATION_ORIGINAL.json").is_file()


# the bounce check hook: report only, found by the same-basename rule beside the final ALS, never stops the build

BOUNCE = "Tools/check_bounce.py"
BOUNCE_DIR = f"{OUTPUT}/Bounce Check"
BOUNCE_ATTENTION = f"{BOUNCE_DIR}/ATTENTION.md"
BOUNCE_SUMMARY = f"BOUNCE CHECK: 1 of 2 joins need a look (J01): see {BOUNCE_ATTENTION}"
BOUNCE_COMMAND = f'python Tools/check_bounce.py --project "{PROJECT}"'
NO_FRESH = "bounce check: no fresh bounce beside the final yet (render the final ALS to a WAV of the same name, then run):"
STALE_BOUNCE = "bounce check: the bounce beside the final is older than the ALS (stale), ignored: render the new final, then run Tools/check_bounce.py:"


def touch(path, age=3600):
    """Set the mtime `age` seconds from now: a positive age is newer than the ALS the build is about to write, a negative one older."""
    stamp = time.time() + age
    os.utime(path, (stamp, stamp))
    return path


def render(repo, name="Sections V9.wav", age=3600):
    path = repo / OUTPUT / name
    path.write_bytes(b"bounce")
    return touch(path, age)


def test_a_bounce_beside_the_final_is_checked_once_after_the_grid_step_and_its_summary_reaches_the_log(pipeline):
    repo, run = pipeline
    render(repo)
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    bounce_calls = [call for call in calls if call[0] == BOUNCE]
    assert bounce_calls == [[BOUNCE, f"{OUTPUT}/Sections V9.wav", "--als", FINAL, "--out", BOUNCE_DIR]]
    assert calls.index([call for call in calls if call[0] == GRID][0]) < calls.index(bounce_calls[0])
    assert calls.index([call for call in calls if call[0] == KICK][0]) < calls.index(bounce_calls[0])
    log = (repo / OUTPUT / "runmix.log").read_text()
    assert BOUNCE_SUMMARY in result.stdout and BOUNCE_SUMMARY in log
    assert "NEEDS A LOOK: join 1 A > B" in log and "a long per-join line" in log and "a long per-join line" not in result.stdout
    assert (repo / BOUNCE_ATTENTION).is_file()
    assert result.stdout.index(BOUNCE_SUMMARY) < result.stdout.index("DONE: final = Sections V9.als")


def test_the_bounce_is_taken_beside_the_phased_final_when_the_grid_phase_is_applied(pipeline):
    repo, run = pipeline
    (repo / OUTPUT / "Sections V9.wav").write_bytes(b"the unphased bounce")
    result, calls = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_PHASED_BOUNCE="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert APPLIED in result.stdout
    assert [call for call in calls if call[0] == BOUNCE] == [[BOUNCE, f"{OUTPUT}/Sections V9 Phased.wav", "--als", PHASED, "--out", BOUNCE_DIR]]


@pytest.mark.parametrize("mode", ["suggest", "apply", "off"])
def test_a_rendered_bounce_survives_a_rebuild_but_the_phased_leftovers_do_not(pipeline, mode):
    repo, run = pipeline
    planted = {name: name.encode("ascii") for name in ("Sections V9 Phased.wav", "Sections V9.wav", "Sections V9 Phased.aif", "Sections V9 Phased.flac",
                                                          "Sections V9 Phased Mix.wav", "Sections V9 Phased.mp3")}
    for name, body in planted.items():
        (repo / OUTPUT / name).write_bytes(body)
        touch(repo / OUTPUT / name, -3600)
    for name in ("Sections V9 Phased REFUSED.als", "Sections V9 Phased.als.partial", "Sections V9 Phased.als"):
        (repo / OUTPUT / name).write_bytes(b"old build")
    result, calls = run(GRID_PHASE_MODE=mode)
    assert result.returncode == 0, result.stdout + result.stderr
    for name, body in planted.items():
        assert (repo / OUTPUT / name).read_bytes() == body, name                    # the audio is Sam's, a rebuild never touches it
    assert not (repo / OUTPUT / "Sections V9 Phased REFUSED.als").exists() and not (repo / OUTPUT / "Sections V9 Phased.als.partial").exists()
    assert not any(call[0] == BOUNCE for call in calls)                             # and it was rendered before the new ALS: stale


def test_a_bounce_of_another_name_is_not_taken_for_the_final(pipeline):
    repo, run = pipeline
    for name in ("Sections V9 Phased.wav", "Sections V8.wav", "Test Mix Mix V9.wav"):
        (repo / OUTPUT / name).write_bytes(b"not the final's bounce")
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(call[0] == BOUNCE for call in calls)
    assert NO_FRESH in result.stdout


def test_without_a_bounce_the_hook_says_so_and_the_build_is_untouched(pipeline):
    repo, run = pipeline
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert NO_FRESH in result.stdout
    assert not any(call[0] == BOUNCE for call in calls) and not (repo / BOUNCE_ATTENTION).exists()
    lines = [l.split("] ", 1)[1] for l in result.stdout.splitlines() if l.startswith("[")]
    assert lines[-3:] == [NO_FRESH, "  " + BOUNCE_COMMAND, "DONE: final = Sections V9.als"], lines[-4:]      # the command is the last hint line


@pytest.mark.parametrize("value", ["off", "OFF", "Off", "oFF"])
def test_bounce_check_mode_off_skips_the_step_even_with_a_bounce_there(pipeline, value):
    repo, run = pipeline
    render(repo)
    result, calls = run(BOUNCE_CHECK_MODE=value)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(call[0] == BOUNCE for call in calls)
    assert "bounce check: off (BOUNCE_CHECK_MODE=off), step skipped" in result.stdout and "BOUNCE CHECK:" not in result.stdout
    assert "DONE: final = Sections V9.als" in result.stdout


@pytest.mark.parametrize("value", ["on", "banana", "offf", ""])
def test_any_bounce_check_mode_but_off_runs_the_step(pipeline, value):
    repo, run = pipeline
    render(repo)
    result, calls = run(BOUNCE_CHECK_MODE=value)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sum(call[0] == BOUNCE for call in calls) == 1 and BOUNCE_SUMMARY in result.stdout


@pytest.mark.parametrize("status", [1, 2, 127])
def test_a_bounce_check_that_crashes_never_stops_the_build_and_leaves_a_file_saying_it_did_not_run(pipeline, status):
    repo, run = pipeline
    render(repo)
    result, calls = run(PIPELINE_TEST_BOUNCE_RC=str(status), PIPELINE_TEST_BOUNCE_STDERR="Traceback: boom")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"bounce check did not finish (status {status}): no measurements, {BOUNCE_ATTENTION} says so; the build goes on" in result.stdout
    assert "DONE: final = Sections V9.als" in result.stdout and "BOUNCE CHECK:" not in result.stdout
    assert "Traceback: boom" in (repo / OUTPUT / "runmix.log").read_text()
    text = (repo / BOUNCE_ATTENTION).read_text()
    assert text.startswith("bounce check did not run (") and "no measurements" in text and "NEEDS A LOOK" not in text, text


def test_a_bounce_that_does_not_fit_the_final_is_named_and_the_build_goes_on(pipeline):
    repo, run = pipeline
    render(repo)
    result, _ = run(PIPELINE_TEST_BOUNCE_RC="3")
    assert result.returncode == 0, result.stdout + result.stderr
    assert BOUNCE_SUMMARY in result.stdout and "the bounce does not fit the final ALS (status 3)" in result.stdout
    assert "did not finish" not in result.stdout and "DONE: final = Sections V9.als" in result.stdout


@pytest.mark.parametrize("options, bounce", [
    pytest.param({}, False, id="no-bounce"),
    pytest.param({}, True, id="bounce"),
    pytest.param({"BOUNCE_CHECK_MODE": "off"}, True, id="off"),
    pytest.param({"PIPELINE_TEST_BOUNCE_RC": "2"}, True, id="crash"),
])
def test_an_attention_file_of_an_earlier_build_never_survives_a_new_one(pipeline, options, bounce):
    repo, run = pipeline
    (repo / BOUNCE_DIR).mkdir()
    (repo / BOUNCE_ATTENTION).write_text("no join needs a look (8 joins checked)\n")
    if bounce:
        render(repo)
    result, _ = run(**options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / BOUNCE_ATTENTION).exists() or "no join needs a look (8 joins" not in (repo / BOUNCE_ATTENTION).read_text()
    assert (repo / BOUNCE_ATTENTION).exists() == (bounce and not options.get("BOUNCE_CHECK_MODE"))     # the tool ran (or crashed and said so)


def test_an_attention_file_of_an_earlier_build_is_removed_even_when_the_build_fails_early(pipeline):
    repo, run = pipeline
    (repo / BOUNCE_DIR).mkdir()
    (repo / BOUNCE_ATTENTION).write_text("no join needs a look (8 joins checked)\n")
    result, _ = run(PIPELINE_TEST_RELAY_FAIL="1")
    assert result.returncode == 1 and "FAILED at: relay" in result.stdout
    assert not (repo / BOUNCE_ATTENTION).exists()


# the REAL Tools/check_bounce.py behind the hook, on a tiny synthetic bounce and ALS: the hook, ATTENTION.md and the summary line end to end

@pytest.fixture
def real_bounce(pipeline, tmp_path):
    import test_bounce_check as tb
    repo, run = pipeline
    als = tmp_path / "Real.als"
    tb._write_als(als, [("Alpha - One", (0.0, 160.0)), ("Beta - Two", (100.0, 220.0))])        # one join, 50 s to 80 s at 120 bpm
    options = dict(PIPELINE_TEST_REAL_BOUNCE="1", PIPELINE_TEST_FINAL_FROM=als.as_posix())
    return repo, run, tb, options


def test_the_real_check_runs_through_the_hook_and_flags_a_loud_blend(real_bounce):
    repo, run, tb, options = real_bounce
    touch(tb.build(repo / OUTPUT / "Sections V9.wav", (tb.sine(1000, 40, -20), None), (tb.sine(1000, 30, -10), None), (tb.sine(1000, 30, -20), None)))
    result, calls = run(**options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call for call in calls if call[0] == BOUNCE] == [[BOUNCE, f"{OUTPUT}/Sections V9.wav", "--als", FINAL, "--out", BOUNCE_DIR]]
    summary = [l.split("] ", 1)[1] for l in result.stdout.splitlines() if "BOUNCE CHECK:" in l]
    assert len(summary) == 1 and summary[0].startswith("BOUNCE CHECK: 1 of 1 joins need a look (J01)") and "ATTENTION.md" in summary[0], summary
    text = (repo / BOUNCE_ATTENTION).read_text()
    assert "NEEDS A LOOK: join 1 One > Two (0:50.0-1:20.0): blend peak vs sides +10.0 LU, outside Sam's accepted range" in text, text
    assert "picture: " in text and (repo / BOUNCE_DIR / "join_01.png").is_file() and (repo / BOUNCE_DIR / "bounce_check.json").is_file()
    log = (repo / OUTPUT / "runmix.log").read_text()
    assert summary[0] in log and "J01 One > Two" in log and "DONE: final = Sections V9.als" in result.stdout


def test_the_real_check_that_cannot_read_the_bounce_says_so_in_the_file_and_never_stops_the_build(real_bounce):
    repo, run, tb, options = real_bounce
    (repo / OUTPUT / "Sections V9.wav").write_bytes(b"this is not a wav file")
    touch(repo / OUTPUT / "Sections V9.wav")
    (repo / BOUNCE_DIR).mkdir()
    (repo / BOUNCE_ATTENTION).write_text("no join needs a look (8 joins checked)\n")
    result, _ = run(**options)
    assert result.returncode == 0, result.stdout + result.stderr
    text = (repo / BOUNCE_ATTENTION).read_text()
    assert text.startswith("bounce check did not run (") and text.rstrip().endswith("): no measurements") and "8 joins" not in text, text
    assert f"bounce check did not finish (status 2): no measurements, {BOUNCE_ATTENTION} says so; the build goes on" in result.stdout
    assert "BOUNCE CHECK: did not run: no measurements: see " in result.stdout and "DONE: final = Sections V9.als" in result.stdout


def test_the_real_check_on_a_bounce_that_does_not_fit_the_final_says_so_in_the_file(pipeline, tmp_path):
    import test_bounce_check as tb
    repo, run = pipeline
    als = tmp_path / "Long.als"
    tb._write_als(als, [("Alpha - One", (0.0, 160.0)), ("Beta - Two", (100.0, 400.0))])        # arrangement is 200 s, the bounce 110 s
    touch(tb.build(repo / OUTPUT / "Sections V9.wav", (tb.sine(1000, 40, -20), None), (tb.sine(1000, 30, -20), None), (tb.sine(1000, 30, -20), None)))
    result, _ = run(PIPELINE_TEST_REAL_BOUNCE="1", PIPELINE_TEST_FINAL_FROM=als.as_posix())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "bounce does not fit the arrangement" in (repo / BOUNCE_ATTENTION).read_text()
    assert "the bounce does not fit the final ALS (status 3)" in result.stdout and "WARNINGS (MAP" in result.stdout


def test_a_bounce_older_than_the_final_is_ignored_and_the_command_to_run_is_the_last_hint(pipeline):
    repo, run = pipeline
    wav = render(repo, age=-3600)
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(call[0] == BOUNCE for call in calls) and wav.read_bytes() == b"bounce"
    lines = [l.split("] ", 1)[1] for l in result.stdout.splitlines() if l.startswith("[")]
    assert lines[-3:] == [STALE_BOUNCE, "  " + BOUNCE_COMMAND, "DONE: final = Sections V9.als"], lines[-4:]
    assert "BOUNCE CHECK:" not in result.stdout and not (repo / BOUNCE_ATTENTION).exists()


def test_a_bounce_newer_than_the_final_is_checked_and_prints_no_render_hint(pipeline):
    repo, run = pipeline
    render(repo, age=3600)
    result, calls = run()
    assert sum(call[0] == BOUNCE for call in calls) == 1 and BOUNCE_SUMMARY in result.stdout
    assert NO_FRESH not in result.stdout and STALE_BOUNCE not in result.stdout and BOUNCE_COMMAND not in result.stdout


def test_off_prints_no_render_hint(pipeline):
    result, _ = pipeline[1](BOUNCE_CHECK_MODE="off")
    assert BOUNCE_COMMAND not in result.stdout and NO_FRESH not in result.stdout and STALE_BOUNCE not in result.stdout


def test_a_resumed_build_that_rewrites_the_final_ignores_the_bounce_of_the_old_arrangement(pipeline):
    repo, run = pipeline
    result, _ = run()
    assert result.returncode == 0, result.stdout + result.stderr
    final = repo / FINAL
    wav = render(repo, age=0)
    stamp = final.stat().st_mtime - 1                      # rendered from the first arrangement, before this rewrite
    os.utime(wav, (stamp, stamp))
    result, calls = run(RESUME="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert final.stat().st_mtime > wav.stat().st_mtime
    assert not any(call[0] == BOUNCE for call in calls) and STALE_BOUNCE in result.stdout
    assert "BOUNCE CHECK:" not in result.stdout
