import json

import pytest

from agent_harness_eval.measurement.grading import begin, finish
from agent_harness_eval.operations.commands import Commands
from agent_harness_eval.workspace.models import ExperimentPlan


def test_standalone_official_cli_freezes_inputs_and_uses_manager_queue(tmp_path):
    from agent_harness_eval.operations.official import submit

    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text('{"instance_id":"task","model_patch":"diff original"}\n')
    dataset = tmp_path / "records.json"
    dataset.write_text('[{"instance_id":"task","base_commit":"original"}]')
    job = submit(tmp_path, predictions, str(dataset), "fixture-official", tmp_path / "scores")
    assert job["kind"] == "swe-grade" and job["status"] == "queued"
    predictions.write_text("changed after submission")
    dataset.write_text("changed after submission")
    from pathlib import Path

    assert "diff original" in Path(job["payload"]["predictions"]).read_text()
    assert "original" in Path(job["payload"]["dataset"]).read_text()
    assert job["payload"]["scorer"] == "swebench==4.1.0"
    assert len(job["payload"]["hashes"]) == 2


def test_official_worker_rejects_changed_frozen_input_before_scoring(tmp_path, monkeypatch):
    from pathlib import Path
    from agent_harness_eval.operations import official, worker

    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text('{"instance_id":"task","model_patch":"original"}\n')
    job = official.submit(tmp_path, predictions, "fixture/dataset", "fixture-official", tmp_path / "scores")
    Path(job["payload"]["predictions"]).write_text("changed")
    monkeypatch.setattr(worker, "is_managed_worker", lambda: True)
    with pytest.raises(ValueError, match="inputs changed"):
        official.perform(tmp_path, job["id"])


def test_grade_retry_creates_new_immutable_record_without_agent_execution(tmp_path, monkeypatch):
    commands = Commands(tmp_path)
    plan = commands.store.create_plan(ExperimentPlan(experiment={"name": "frozen"}), "plan")
    commands.submit("experiment", {"plan_id": plan}, key="operation")
    job = commands.claim("owner")
    for name, value in {"ROOT": str(tmp_path), "ID": job["id"], "OWNER": "owner", "GENERATION": str(job["generation"])}.items():
        monkeypatch.setenv("HARNESS_OPERATION_" + name, value)
    work = tmp_path / "grades/one"
    work.mkdir(parents=True)
    first = begin(tmp_path, work, agent="own", task="task", repeat=1, scorer="grader@1", input_digest="patch")
    finish(first, success=False)
    before = (work / "grade-run.json").read_bytes()
    second_work = tmp_path / "grades/two"
    second_work.mkdir()
    second = begin(tmp_path, second_work, agent="own", task="task", repeat=1, scorer="grader@1", input_digest="patch")
    finish(second, success=True)
    assert first != second
    assert (work / "grade-run.json").read_bytes() == before
    with commands.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM records WHERE kind='attempt'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM records WHERE kind='grade'").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert json.loads((second_work / "grade-run.json").read_text())["ordinal"] == 2
    with pytest.raises(ValueError, match="Stale"):
        finish(first, success=True)
