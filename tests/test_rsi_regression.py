import asyncio
import copy
import json

import pytest
from test_rsi import git
from test_rsi_native_evaluation import native_case as native_fixture

from agent_harness_eval.guardian.evidence import write
from agent_harness_eval.rsi import evaluation, regression
from agent_harness_eval.rsi.publication import readiness

native_case = native_fixture


def plan():
    return {
        "objective": "复现故障",
        "rationale": "模拟连续失败后的恢复",
        "limitations": "仅覆盖此故障",
        "evidence_refs": ["e1"],
        "overlays": ["internal/agent/recovery_test.go"],
        "tests": [
            {
                "package": "./internal/agent",
                "name": "TestRecovery",
                "role": "regression",
                "failure_contains": "missing reasoning",
            },
            {"package": "./internal/agent", "name": "TestNormal", "role": "control"},
        ],
    }


def output(name, outcome="pass", message=""):
    events = [
        {"Test": name, "Action": "output", "Output": message},
        {"Test": name, "Action": outcome},
        {"Action": outcome},
    ]
    return {
        "returncode": int(outcome != "pass"),
        "stdout": "\n".join(map(json.dumps, events)),
    }


def results():
    return {
        "parent": [
            output("TestRecovery", "fail", "missing reasoning"),
            output("TestNormal"),
        ],
        "candidate": [output("TestRecovery"), output("TestNormal")],
    }


def test_expected_assertion_failure_is_improvement_not_execution_error():
    result = regression.compare(plan(), results())
    assert result["assessment"] == "improved"
    assert result["errors"] == {"parent": 0, "candidate": 0}


@pytest.mark.parametrize(
    "failure",
    [
        "compile",
        "skip",
        "timeout",
        "wrong_assertion",
        "panic",
        "truncated",
        "missing",
        "control",
    ],
)
def test_invalid_or_unrelated_failure_never_promotes(failure):
    data = results()
    if failure == "compile":
        data["parent"][0] = {"returncode": 1, "stdout": "compile error"}
    elif failure == "skip":
        data["parent"][0] = output("TestRecovery", "skip")
    elif failure == "timeout":
        data["parent"][0]["returncode"] = 124
    elif failure == "wrong_assertion":
        data["parent"][0] = output("TestRecovery", "fail", "unrelated error")
    elif failure == "panic":
        data["parent"][0] = output("TestRecovery", "fail", "panic: missing reasoning")
    elif failure == "truncated":
        data["parent"][0]["truncated"] = True
    elif failure == "missing":
        data["candidate"][0] = output("TestDifferent")
    elif failure == "control":
        data["parent"][1] = output("TestNormal", "fail", "bad")
    assert regression.compare(plan(), data)["assessment"] == "inconclusive"


def test_both_pass_means_bug_not_reproduced():
    data = results()
    data["parent"] = copy.deepcopy(data["candidate"])
    assert regression.compare(plan(), data)["assessment"] == "equal"


def test_same_failure_on_both_versions_is_not_a_new_regression():
    data = results()
    data["candidate"] = copy.deepcopy(data["parent"])
    assert regression.compare(plan(), data)["assessment"] == "equal"


def test_expected_assertion_can_be_in_named_tests_subtest():
    data = results()
    data["parent"][0]["stdout"] = "\n".join(
        map(
            json.dumps,
            [
                {
                    "Test": "TestRecovery/nested",
                    "Action": "output",
                    "Output": "missing reasoning",
                },
                {"Test": "TestRecovery/nested", "Action": "fail"},
                {"Test": "TestRecovery", "Action": "fail"},
                {"Action": "fail"},
            ],
        )
    )
    assert regression.compare(plan(), data)["assessment"] == "improved"


def test_plan_allows_only_test_overlay_and_requires_control(tmp_path):
    value = plan()
    write(tmp_path / "internal/agent/recovery_test.go", {})
    assert regression.validate_plan(value, tmp_path, {"e1"})
    for bad in (
        "../recovery_test.go",
        "/tmp/recovery_test.go",
        "internal/agent/loop.go",
    ):
        value["overlays"] = [bad]
        with pytest.raises(ValueError):
            regression.validate_plan(value, tmp_path, {"e1"})
    value = plan()
    value["tests"][1]["role"] = "regression"
    value["tests"][1]["failure_contains"] = "fails"
    with pytest.raises(ValueError, match="正常对照"):
        regression.validate_plan(value, tmp_path, {"e1"})


def test_builtin_default_dispatches_to_dsh_not_task_replay(monkeypatch):
    called = []

    async def targeted(store, record, live):
        called.append(live)

    async def forbidden(*args):
        pytest.fail("original task replay must not be used")

    monkeypatch.setattr(regression, "evaluate", targeted)
    monkeypatch.setattr(evaluation, "replay_tasks", forbidden)
    asyncio.run(
        evaluation.evaluate(None, {"profile_id": "builtin-dsh", "profile": {}}, True)
    )
    assert called == [True]


def test_task_replay_cannot_promote_builtin_bugfix():
    record = {
        "profile_id": "builtin-dsh",
        "profile": {},
        "parent_commit": "a",
        "commit": "b",
        "evaluations": [
            {
                "assessment": "improved",
                "finished_at": "done",
                "parent_commit": "a",
                "candidate_commit": "b",
            }
        ],
    }
    assert not readiness(None, record)[0]
    record["evaluations"][0]["kind"] = "targeted_regression"
    assert readiness(None, record)[0]


def test_dsh_plan_is_frozen_and_executed_on_both_exact_commits(
    native_case, monkeypatch
):
    from pathlib import Path

    from agent_harness_eval.guardian.evidence import read

    store, case, record, _ = native_case
    commands = []

    async def execute(command, cwd, log=None, values=None, stdin=None, *, lane=None):
        assert lane == (None if stdin else "build")
        if stdin:
            assert read(log.parent / "platform-request.json")["role"] == "diagnosis"
        if stdin:
            assert stdin["purpose"] == "targeted_regression_plan"
            destination = Path(stdin["result_path"]).parent
            value = plan()
            value["evidence_refs"] = [
                next(e["id"] for e in case["evidence"] if e.get("file"))
            ]
            write(destination / "plan.json", value)
            file = destination / "output/native/tests/internal/agent/recovery_test.go"
            file.parent.mkdir(parents=True)
            file.write_text("// same regression overlay\n")
        else:
            argv = command.argv
            checkout = Path(argv[argv.index("--workspace") + 1])
            output_path = Path(argv[argv.index("--output") + 1])
            commands.append(
                (
                    git(checkout, "rev-parse", "HEAD"),
                    (checkout / "internal/agent/recovery_test.go").read_text(),
                )
            )
            assert (
                checkout / "harness.py"
            ).read_text() == f"VALUE = {len(commands) - 1}\n"
            write(output_path / "results.json", results()[output_path.name])

    monkeypatch.setattr(regression, "checked", execute)
    asyncio.run(regression.evaluate(store, record, True))
    assert [c[0] for c in commands] == [record["parent_commit"], record["commit"]]
    assert commands[0][1] == commands[1][1]
    outcome = store.get("candidates", record["id"])["evaluations"][-1]
    assert outcome["assessment"] == "improved" and outcome["planner"] == "dsh"
    assert outcome["plan_sha256"] and outcome["overlays"]
    root = store.path("candidates", record["id"])
    assert read(root / "evaluations" / outcome["id"] / "comparison.json") == outcome
    assert not (root / "workspace/internal/agent/recovery_test.go").exists()
