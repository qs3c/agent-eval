import json
import shutil

from agent_harness_eval.integrations.builder import build
from agent_harness_eval.integrations.conformance import check
from agent_harness_eval.integrations.schema import Recipe
from agent_harness_eval.integrations.store import Releases
from test_integration_recipe import fixture_recipe
from test_release_build import repository, fake_build


def test_execution_evidence_cannot_self_declare_fairness_or_hide_duplicate_tasks(tmp_path):
    source = repository(tmp_path)
    output = tmp_path / "built"
    receipt = build(Recipe.model_validate(fixture_recipe()), source, output, recipe_directory=tmp_path, executor=fake_build)
    catalog = Releases(tmp_path)
    release = catalog.add(output)
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"experiment": {
        "agents": [catalog.agent(receipt["release_id"])], "benchmark": {"tasks": ["a", "b"]}, "attempts": 1}}))
    for task in ("a", "b"):
        trial = run / "jobs/fixture" / task
        (trial / "agent").mkdir(parents=True)
        (trial / "result.json").write_text(json.dumps({"task_name": task, "finished_at": "now", "exception_info": None}))
        shutil.copy2(release / "provenance.json", trial / "agent/provenance.json")
        (trial / "agent/agent-result.json").write_text(json.dumps({"schema_version": 1, "status": "completed", "comparable": True}))
    result = check(release, run)
    assert result["levels"]["runnable"]["passed"]
    assert not result["levels"]["comparable"]["passed"]
    assert not result["levels"]["diagnosable"]["passed"]
    (run / "jobs/fixture/b/result.json").write_text(json.dumps({"task_name": "a", "finished_at": "now", "exception_info": None}))
    result = check(release, run)
    assert not result["levels"]["runnable"]["passed"]
    assert any("重复次数" in reason for reason in result["levels"]["runnable"]["reasons"])
