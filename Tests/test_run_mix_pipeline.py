import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

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
    Path(args[args.index("--out") + 1]).write_text(
        '{"transitions": [{"pair_index": 1}], "fallback_pairs": [{"pair_index": 2}]}',
        encoding="ascii")
elif args[0] in {"Source/propose_arrangement.py", "Source/apply_automation.py"}:
    Path(args[3]).write_bytes(b"later phase")
elif args[0] == "Tools/check_kick_alignment.py":
    print("T01 A to B: WARN click +7.3 ms main +15.7 ms shared kicks 38 SLOW_ATTACK")
    print("LOOK AT THESE PICTURES: " + args[args.index("--out") + 1])
    if os.environ.get("PIPELINE_TEST_KICK_STDERR"):
        print(os.environ["PIPELINE_TEST_KICK_STDERR"], file=sys.stderr)
    sys.exit(int(os.environ.get("PIPELINE_TEST_KICK_RC", "0")))
elif args[0] == "Tools/fix_grid_phase.py":
    out = Path(args[args.index("--out") + 1])
    print("RESULT PHASED: " + str(out))
    if os.environ.get("PIPELINE_TEST_GRID_STDERR"):
        print(os.environ["PIPELINE_TEST_GRID_STDERR"], file=sys.stderr)
    rc = int(os.environ.get("PIPELINE_TEST_GRID_RC", "0"))
    if rc == 0:
        out.write_bytes(Path(args[1]).read_bytes() if os.environ.get("PIPELINE_TEST_GRID_SAME") else b"phased als")
    sys.exit(rc)
elif args[0] == "Source/validate_als.py" and "Phased" in args[1]:
    sys.exit(1 if os.environ.get("PIPELINE_TEST_PHASED_VALIDATE_FAIL") else 0)
elif args[0] == "Source/validate_mix_plan_als.py" and "Phased" in args[3]:
    sys.exit(1 if os.environ.get("PIPELINE_TEST_PHASED_RECON_FAIL") else 0)
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
               PIPELINE_TEST_STUB=stub.as_posix(), PIPELINE_TEST_CALLS=str(calls_path))
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
    finals = [i for i, call in enumerate(calls) if call[0] == "Source/validate_als.py" and "--require-devices" in call]
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


def _last_reconciliation(calls):
    return [call for call in calls if call[0] == RECONCILE][-1]


def test_grid_phase_runs_once_after_the_kick_alignment_in_suggest_mode_by_default(pipeline):
    repo, run = pipeline
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    grid_calls = [call for call in calls if call[0] == GRID]
    assert grid_calls == [[GRID, FINAL, "--audio", f"{PROJECT}/Audio", "--out", PHASED, "--report", f"{OUTPUT}/Grid Phase"]]
    assert calls.index([call for call in calls if call[0] == KICK][0]) < calls.index(grid_calls[0])
    assert f"grid phase fix (suggest): report in {OUTPUT}/Grid Phase" in result.stdout
    assert "RESULT PHASED" in (repo / OUTPUT / "runmix.log").read_text()
    assert (repo / PHASED).is_file()


def test_suggest_mode_leaves_the_final_untouched_and_does_not_validate_the_phased_file(pipeline):
    repo, run = pipeline
    result, calls = run(GRID_PHASE_MODE="suggest")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DONE: final = Sections V9.als" in result.stdout
    assert (repo / FINAL).read_bytes() == b"later phase"
    assert _last_reconciliation(calls)[3] == FINAL
    assert not any("Phased" in " ".join(call) for call in calls if call[0] in ("Source/validate_als.py", RECONCILE))


def test_a_grid_mode_other_than_apply_is_suggest(pipeline):
    _, run = pipeline
    result, calls = run(GRID_PHASE_MODE="banana")
    assert "grid phase fix (suggest)" in result.stdout and "DONE: final = Sections V9.als" in result.stdout
    assert _last_reconciliation(calls)[3] == FINAL


@pytest.mark.parametrize("status, message", [
    (2, "grid phase fix did not finish (status 2); the final stays Sections V9.als"),
    (4, "grid phase fix REFUSED its own result (status 4"),
    (127, "grid phase fix did not finish (status 127)"),
])
@pytest.mark.parametrize("mode", ["suggest", "apply"])
def test_a_grid_phase_tool_that_fails_never_stops_the_build(pipeline, mode, status, message):
    repo, run = pipeline
    result, calls = run(GRID_PHASE_MODE=mode, PIPELINE_TEST_GRID_RC=str(status), PIPELINE_TEST_GRID_STDERR="Traceback: boom")
    assert result.returncode == 0, result.stdout + result.stderr
    assert message in result.stdout and "DONE: final = Sections V9.als" in result.stdout
    assert "Traceback: boom" in (repo / OUTPUT / "runmix.log").read_text()
    assert _last_reconciliation(calls)[3] == FINAL
    assert not (repo / PHASED).exists()


def test_a_stale_phased_file_from_an_earlier_build_is_removed_when_the_tool_fails(pipeline):
    repo, run = pipeline
    (repo / PHASED).write_bytes(b"old phased file")
    result, _ = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_GRID_RC="2")
    assert result.returncode == 0 and not (repo / PHASED).exists()


def test_apply_mode_swaps_in_the_phased_file_only_after_it_passes_both_validators(pipeline):
    repo, run = pipeline
    result, calls = run(GRID_PHASE_MODE="apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "grid phase fix (apply)" in result.stdout and "grid phase fix APPLIED" in result.stdout
    assert "DONE: final = Sections V9 Phased.als" in result.stdout
    validators = [i for i, call in enumerate(calls) if call[0] == "Source/validate_als.py" and call[1] == PHASED]
    assert len(validators) == 1 and "--expected-tracks" in calls[validators[0]] and "--require-devices" in calls[validators[0]]
    reconciliations = [call for call in calls if call[0] == RECONCILE]
    assert [call[3] for call in reconciliations] == [PHASED, PHASED]    # the trial, then the build's own
    assert reconciliations[-1][-1] == f"{OUTPUT}/MIX_PLAN_RECONCILIATION.json"
    assert max(validators) < calls.index(reconciliations[0]) < calls.index(reconciliations[1])


def test_apply_mode_keeps_the_original_when_the_phased_file_fails_validation(pipeline):
    repo, run = pipeline
    result, calls = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_PHASED_VALIDATE_FAIL="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GRID PHASE FIX NOT APPLIED" in result.stdout and "DONE: final = Sections V9.als" in result.stdout
    assert [call[3] for call in calls if call[0] == RECONCILE] == [FINAL]


def test_apply_mode_keeps_the_original_when_the_phased_file_fails_the_reconciliation(pipeline):
    _, run = pipeline
    result, calls = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_PHASED_RECON_FAIL="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GRID PHASE FIX NOT APPLIED" in result.stdout and "DONE: final = Sections V9.als" in result.stdout
    assert _last_reconciliation(calls)[3] == FINAL


def test_apply_mode_with_nothing_to_change_does_not_swap_or_validate(pipeline):
    _, run = pipeline
    result, calls = run(GRID_PHASE_MODE="apply", PIPELINE_TEST_GRID_SAME="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing to change" in result.stdout and "DONE: final = Sections V9.als" in result.stdout
    assert not any(call[0] == "Source/validate_als.py" and "Phased" in call[1] for call in calls)


def test_grid_phase_does_not_run_when_an_earlier_step_failed(pipeline):
    _, run = pipeline
    result, calls = run(KICK_ALIGNMENT_MODE="fail", PIPELINE_TEST_KICK_RC="1")
    assert result.returncode == 1 and not any(call[0] == GRID for call in calls)

