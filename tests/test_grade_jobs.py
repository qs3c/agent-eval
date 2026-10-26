import json

import pytest

from agent_harness_eval.measurement.grading import begin, finish
from agent_harness_eval.operations.commands import Commands
from agent_harness_eval.workspace.models import ExperimentPlan


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
