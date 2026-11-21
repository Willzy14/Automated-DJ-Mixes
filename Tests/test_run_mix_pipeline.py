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

STUB = r'''
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ["PIPELINE_TEST_CALLS"]).open("a", encoding="ascii") as calls:
    calls.write(json.dumps(args) + "\n")

if args[0] == "-m":
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
    print(f"Generated: {layout}")
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

    def run(**options):
        calls_path.unlink(missing_ok=True)
        result = subprocess.run([bash, script.as_posix(), "Test Mix", "A,B,C", "3"],
                                cwd=tmp_path, env={**env, **options}, capture_output=True,
                                text=True, timeout=60)
        calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
        return result, calls

    return repo, run


@pytest.mark.parametrize("options, arranger, decision_args", [
    ({"DEC": "My hand decisions.json", "PLANNER": "off"},
     "hand decisions My hand decisions.json", ["--decisions", "My hand decisions.json"]),
    ({"PLANNER": "off"}, "rule-based (PLANNER=off)", []),
    ({}, "planner (1 of 2 pairs planned, fallback: T2)",
     ["--decisions", PLANNED, "--decisions-drop-rejected-loops"]),
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
    if not options:
        planner_call = [PLANNER, PROJECT, "--sections", SECTIONS, "--out", PLANNED,
                        "--content", f"{OUTPUT}/planner_content.json", "--allow-partial"]
        assert planner_calls == [planner_call]
        hints_gate = next(i for i, call in enumerate(calls) if call[0] == "Source/validate_hints_vs_sections.py")
        assert hints_gate < calls.index(planner_call) < next(i for i, call in enumerate(calls) if call[0] == ARRANGE)
    else:
        assert not planner_calls
    assert (repo / OUTPUT / "Sections V9.als").is_file()


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
