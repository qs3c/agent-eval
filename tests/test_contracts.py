import json
from pathlib import Path

import pytest

from agent_eval.config import Experiment, load
from agent_eval.provenance import tree_manifest
from agent_eval.report import report


def experiment():
    return {
        "name": "test",
        "agents": [{"id": "a", "implementation": "nop", "version": "1"}],
        "benchmark": {"id": "files", "tasks": ["a", "b"]},
    }


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_agents",
        "duplicate_tasks",
        "capabilities",
        "credential",
        "context",
        "minimal",
    ],
)
def test_invalid_contract(change):
    value = experiment()
    if change == "duplicate_agents":
        value["agents"] *= 2
    if change == "duplicate_tasks":
        value["benchmark"]["tasks"] = ["a", "a"]
    if change == "capabilities":
        value["agents"][0]["capabilities"] = []
    if change == "credential":
        value["agents"][0]["kwargs"] = {"nested": {"api_key": "must-not-save"}}
    if change == "context":
        value["model"] = {"max_tokens": 10, "context_window": 10}
    if change == "minimal":
        value["agents"][0]["kwargs"] = {"profile": "sdk-minimal"}
    with pytest.raises(ValueError):
        Experiment.model_validate(value)


def test_fixture_config():
    exp = load(Path("experiments/controls.json"))
    assert len(exp.benchmark.tasks) == 2


def test_symlink_not_silently_followed(tmp_path):
    (tmp_path / "link").symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        tree_manifest(tmp_path)


def test_report_missing_error_and_unknown_usage(tmp_path):
    exp = Experiment.model_validate(experiment()).model_dump(mode="json")
    exp["attempts"] = 2
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {"experiment": exp, "tasks": {"a": {}, "b": {}}, "evidence_kind": "test"}
        )
    )
    for task, suffix, error, reward in [
        ("a", "1", None, 1),
        ("a", "2", {"exception_type": "TimeoutError"}, None),
        ("b", "1", None, 0),
    ]:
        p = tmp_path / "jobs" / "a" / f"{task}-{suffix}" / "result.json"
        p.parent.mkdir(parents=True)
        p.write_text(
            json.dumps(
                {
                    "task_name": "display/" + task,
                    "task_id": {"path": "/tasks/" + task},
                    "trial_name": p.parent.name,
                    "exception_info": error,
                    "verifier_result": {"rewards": {"reward": reward}}
                    if reward is not None
                    else None,
                }
            )
        )
    result = report(tmp_path)
    assert result["agents"]["a"] == {
        "expected": 4,
        "recorded": 3,
        "missing": 1,
        "errors": 1,
        "scored": 2,
        "mean_on_scored": 0.5,
        "mean_with_unscored_zero": 0.25,
        "cost_usd": None,
    }
    assert result["paired_tasks"] == {"a": {"a": None}, "b": {"a": None}}


def test_live_gate_precedes_any_output(tmp_path):
    from agent_eval.run import execute

    value = experiment()
    value["model"] = {"mode": "live"}
    with pytest.raises(ValueError, match="spending policy"):
        execute(Experiment.model_validate(value), tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


@pytest.mark.parametrize(
    "content", ["{broken", '{"verifier_result":{"rewards":{"reward":"unknown"}}}']
)
def test_malformed_results_stay_in_denominator(tmp_path, content):
    exp = Experiment.model_validate(experiment()).model_dump(mode="json")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {"experiment": exp, "tasks": {"a": {}, "b": {}}, "evidence_kind": "test"}
        )
    )
    result = tmp_path / "jobs/a/a__broken/result.json"
    result.parent.mkdir(parents=True)
    result.write_text(content)
    data = report(tmp_path)
    assert len(data["report_errors"]) == 1
    assert (
        data["agents"]["a"]["missing"] == 2
        and data["agents"]["a"]["mean_on_scored"] is None
    )


def test_live_missing_key_precedes_output(tmp_path, monkeypatch):
    from agent_eval.run import execute

    monkeypatch.delenv("EVAL_ABSENT_KEY", raising=False)
    value = experiment()
    value["model"] = {
        "mode": "live",
        "spending": "unbounded",
        "api_key_env": "EVAL_ABSENT_KEY",
    }
    with pytest.raises(ValueError, match="key environment variable is missing"):
        execute(Experiment.model_validate(value), tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()
