from pathlib import Path

import pytest

from agent_harness_eval.config import load
from agent_harness_eval.operations.commands import Commands
from agent_harness_eval.operations.submission import submit_experiment, verify_inputs
from agent_harness_eval.workspace.models import canonical_json


def test_cli_freezes_inputs_and_remembers_original_resume_configuration(tmp_path):
    exp = load(Path("experiments/controls.json"))
    original = exp.model_dump(mode="json")
    job = submit_experiment(tmp_path, exp, tmp_path / "result")
    commands = Commands(tmp_path)
    assert canonical_json(job["payload"]["requested_config"]) == canonical_json(original)
    plan = commands.store.plan(job["payload"]["plan_id"])
    assert plan.experiment["benchmark"]["tasks"] != original["benchmark"]["tasks"]
    verify_inputs(plan)
    resumed = submit_experiment(tmp_path, exp, tmp_path / "result", resume=True)
    assert commands.store.plan(resumed["payload"]["plan_id"]).experiment == plan.experiment
    exp.concurrency += 1
    with pytest.raises(ValueError, match="configuration differs"):
        submit_experiment(tmp_path, exp, tmp_path / "result", resume=True)


def test_changed_frozen_task_rejected_before_execution(tmp_path):
    exp = load(Path("experiments/controls.json"))
    job = submit_experiment(tmp_path, exp, tmp_path / "result")
    plan = Commands(tmp_path).store.plan(job["payload"]["plan_id"])
    task = Path(plan.experiment["benchmark"]["tasks"][0])
    (task / "instruction.md").write_text("tampered input")
    with pytest.raises(ValueError, match="Frozen task changed"):
        verify_inputs(plan)


def test_selection_receipt_is_frozen_and_checked(tmp_path):
    import shutil

    exp = load(Path("experiments/controls.json"))
    source = tmp_path / "source"
    source.mkdir()
    tasks = []
    for task in exp.benchmark.tasks:
        target = source / task.name
        shutil.copytree(task, target)
        tasks.append(target)
    exp.benchmark.tasks = tasks
    receipt = source / "selection.json"
    receipt.write_text('{"seed":42}')
    job = submit_experiment(tmp_path, exp, tmp_path / "result")
    plan = Commands(tmp_path).store.plan(job["payload"]["plan_id"])
    frozen = Path(plan.experiment["benchmark"]["tasks"][0]).parent / "selection.json"
    receipt.write_text('{"seed":43}')
    assert frozen.read_text() == '{"seed":42}'
    verify_inputs(plan)
    frozen.write_text('{"seed":44}')
    with pytest.raises(ValueError, match="selection changed"):
        verify_inputs(plan)
