"""Installed real upstream releases, deterministic model, no provider spending."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness_eval.config import Agent, load
from agent_harness_eval.integrations.conformance import check
from agent_harness_eval.integrations.store import Releases
from agent_harness_eval.integrations.catalog import recipe_path
from agent_harness_eval.integrations.schema import load as load_recipe
from agent_harness_eval.operations.commands import Commands
from agent_harness_eval.operations.manager import ensure, status, stop
from agent_harness_eval.operations.submission import submit_experiment
from test_integration_docker import until

pytestmark = pytest.mark.skipif(sys.platform != "linux" or not os.environ.get("BUILTIN_RELEASE_ROOT"),
                                reason="requires separately built real upstream releases")


@pytest.mark.parametrize("name", os.environ.get("BUILTIN_AGENTS", "hermes,opencode,bkcrab,dsh").split(","))
def test_builtin_controls_evidence_and_cancel_after_container_start(tmp_path, name):
    releases = Releases(Path(os.environ["BUILTIN_RELEASE_ROOT"]))
    recipe = load_recipe(recipe_path(name))
    matches = [row for row in releases.list() if row["release"]["recipe_digest"] == recipe.digest]
    assert matches, "Build current pinned recipe before running its acceptance: " + name
    release_id = matches[-1]["release_id"]
    project = tmp_path / "project"
    project.mkdir()
    commands = Commands(project)
    def completed(job):
        row = commands.query(job["id"])
        return row if row["status"] in {"completed", "failed", "cancelled", "interrupted"} else None
    ensure(project)
    try:
        exp = load(Path("experiments/controls.json"))
        exp.agents = [Agent.model_validate(releases.agent(release_id))]
        exp.model.mode, exp.model.name = "mock", "fixture-model"
        exp.concurrency = 1
        output = project / "artifacts" / name
        job = submit_experiment(project, exp, output)
        done = until(lambda: completed(job), seconds=360)
        assert done["status"] == "completed", done
        report = json.loads((output / "report.json").read_text())
        assert report["agents"][name]["errors"] == 0, report
        assert report["agents"][name]["mean_on_scored"] == 1.0, report
        result = check(releases.directory / release_id, output)
        assert result["levels"]["runnable"]["passed"], result
        assert result["observed_model_calls"], result
        assert not result["levels"]["comparable"]["passed"]
        if name in {"hermes", "opencode", "bkcrab"}:
            assert result["levels"]["diagnosable"]["passed"], result
        exp.attempts = 20
        cancel_output = project / "artifacts" / (name + "-cancel")
        cancelled = submit_experiment(project, exp, cancel_output)
        def containers():
            found = []
            for config in cancel_output.glob("jobs/*/*/config.json"):
                label = re.sub(r"[^a-z0-9_-]", "", (config.parent.name + "__env").lower())
                found += subprocess.check_output(["docker", "ps", "-aq", "--filter",
                    "label=com.docker.compose.project=" + label], text=True).split()
            return found
        until(containers, seconds=120)
        commands.control(cancelled["id"], "cancel")
        assert until(lambda: completed(cancelled), seconds=180)["status"] == "cancelled"
        assert containers() == []
        from agent_harness_eval.operations.resources import Resources

        assert not [row for row in Resources().snapshot()["reservations"]
                    if row["owner"] == cancelled["id"] and row["status"] != "released"]
    finally:
        stop(project, cancel=True)
        until(lambda: not status(project)["alive"], seconds=150)
