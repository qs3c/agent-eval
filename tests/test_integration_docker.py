"""Opt-in real build/Manager/Harbor contract acceptance; no provider calls."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_harness_eval.config import Agent, load
from agent_harness_eval.integrations.commands import submit
from agent_harness_eval.integrations.conformance import check
from agent_harness_eval.integrations.store import Releases
from agent_harness_eval.operations.commands import Commands
from agent_harness_eval.operations.manager import ensure, status, stop
from agent_harness_eval.operations.submission import submit_experiment
from test_integration_recipe import fixture_recipe
from test_release_build import repository

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.environ.get("INTEGRATION_DOCKER_TESTS") != "1",
                                reason="opt-in offline integration Docker acceptance")


def until(predicate, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError("Integration acceptance timed out")


def test_recipe_build_and_cli_python_protocol_run_in_isolated_containers(tmp_path, monkeypatch):
    # Child processes use a separate HOME ledger, never the user's queue.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    source = repository(tmp_path)
    (source / "agent.py").write_text('''import json,sys
from pathlib import Path
def execute(request):
    assert not Path("/tests/test.sh").exists(), "hidden verifier visible to Agent"
    assert not Path("/solution/solve.sh").exists(), "reference solution visible to Agent"
    Path(request["workspace"], "result.txt").write_text("ready\\n")
    return {"schema_version":1,"status":"completed"}
if __name__ == "__main__":
    r=json.loads(Path(sys.argv[1]).read_text())
    Path(r["output_dir"],"agent-result.json").write_text(json.dumps(execute(r)))
''')
    (source / "build.py").write_text("from pathlib import Path\nimport shutil\nPath('dist').mkdir()\nshutil.copy2('agent.py','dist/agent.py')\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "protocol"], check=True)
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", "python:3.12.13-slim-bookworm", "--format", "{{json .RepoDigests}}"], text=True))[0]
    commands = Commands(project)
    def completed(job):
        row = commands.query(job["id"])
        return row if row["status"] in {"completed", "failed", "cancelled", "interrupted"} else None
    ensure(project)
    try:
        for entrypoint in ({"argv": ["python3", "{agent_dir}/agent.py", "{request_file}"]}, {"python": "agent:execute"}):
            name = "cli" if "argv" in entrypoint else "python"
            recipe = fixture_recipe(id="fixture-" + name, build={"image": image, "argv": ["python3", "build.py"]},
                                    entrypoint=entrypoint, model_modes=["offline"], model_protocol="none")
            recipe_path = tmp_path / (name + ".json")
            recipe_path.write_text(json.dumps(recipe))
            job = submit(project, recipe_path, source)
            built = until(lambda: completed(job))
            assert built["status"] == "completed", built
            receipt = json.loads((Path(job["payload"]["output"]) / "provenance.json").read_text())
            catalog = Releases(project)
            exp = load(Path("experiments/controls.json"))
            exp.agents = [Agent.model_validate(catalog.agent(receipt["release_id"]))]
            exp.concurrency = 1
            output = project / "artifacts" / name
            run = submit_experiment(project, exp, output)
            done = until(lambda: completed(run))
            assert done["status"] == "completed", done
            report = json.loads((output / "report.json").read_text())
            assert report["agents"]["fixture-" + name]["mean_on_scored"] == 1.0, report
            checked = check(catalog.directory / receipt["release_id"], output)
            assert checked["levels"]["runnable"]["passed"], checked
            assert not checked["levels"]["comparable"]["passed"]
            cancelled_output = project / "artifacts" / (name + "-cancel")
            exp.attempts = 20
            cancelled = submit_experiment(project, exp, cancelled_output)
            def containers():
                found = []
                for config in cancelled_output.glob("jobs/*/*/config.json"):
                    label = re.sub(r"[^a-z0-9_-]", "", (config.parent.name + "__env").lower())
                    found += subprocess.check_output(["docker", "ps", "-aq", "--filter",
                        "label=com.docker.compose.project=" + label], text=True).split()
                return found
            until(containers)
            commands.control(cancelled["id"], "cancel")
            assert until(lambda: completed(cancelled))["status"] == "cancelled"
            assert containers() == []
            from agent_harness_eval.operations.resources import Resources

            ledger = Resources(home / ".cache/agent-harness-eval/operations")
            assert not [row for row in ledger.snapshot()["reservations"] if row["owner"] == cancelled["id"] and row["status"] != "released"]
    finally:
        stop(project, cancel=True)
        until(lambda: not status(project)["alive"], seconds=50)
