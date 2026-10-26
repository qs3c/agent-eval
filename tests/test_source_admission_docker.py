"""Real local Git -> Manager -> Docker build -> Harbor checks -> Release pointer."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_harness_eval.integrations.sources import Sources
from agent_harness_eval.integrations.tracking import queue_refresh
from agent_harness_eval.operations.manager import ensure, stop
from agent_harness_eval.operations.commands import Commands
from test_integration_docker import until
from test_release_build import repository
from test_integration_recipe import fixture_recipe
from test_source_tracking import git

pytestmark = pytest.mark.skipif(os.environ.get("RSI_DOCKER_TESTS") != "1", reason="opt-in source admission Docker test")


def test_push_is_admitted_and_bad_followup_preserves_old_release(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = Path(__file__).parents[1]
    root = tmp_path / "project"
    root.mkdir()
    shutil.copytree(project / "tasks", root / "tasks")
    (root / "experiments").mkdir()
    shutil.copy2(project / "experiments/controls.json", root / "experiments/controls.json")
    repo = repository(tmp_path)
    (repo / "agent.py").write_text('''import json,sys
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text())
Path(r["workspace"],"result.txt").write_text("ready\\n")
Path(r["output_dir"],"agent-result.json").write_text(json.dumps({"schema_version":1,"status":"completed"}))
''')
    (repo / "build.py").write_text("from pathlib import Path\nimport shutil\nPath('dist').mkdir()\nshutil.copy2('agent.py','dist/agent.py')\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "adapter")
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", "python:3.12.13-slim-bookworm", "--format", "{{json .RepoDigests}}"], text=True))[0]
    recipe = fixture_recipe(source={"repository": str(repo), "ref": "main"}, build={"image": image, "argv": ["python3", "build.py"]}, model_modes=["offline"], model_protocol="none")
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(recipe))
    sources = Sources(root)
    source = sources.add(str(repo), recipe=path)
    key = source["id"]
    ensure(root)
    try:
        queue_refresh(root, key)
        first = until(lambda: sources.get(key)["admitted"], seconds=180)
        assert first
        assert sources.get(key)["recommended"] is None
        commands = Commands(root)
        plan = next(job for job in commands.list() if job["kind"] == "experiment")["payload"]["plan_id"]
        before = commands.store.plan(plan).model_dump(mode="json")
        # An actual subsequent cycle pairs the new version with its frozen parent.
        from agent_harness_eval.rsi import cycle

        loop = cycle.create(root, key, root / "experiments/controls.json", max_rounds=1, max_attempts=4)
        (repo / "agent.py").write_text((repo / "agent.py").read_text() + "\n# product revision with unchanged control behavior\n")
        git(repo, "add", ".")
        git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "next valid version")
        queue_refresh(root, key)
        next_release = until(lambda: (r if (r := sources.get(key)["admitted"]) != first else None), seconds=180)
        cycle.queue(root, loop["id"])
        completed = until(lambda: (v if (v := cycle.get(root, loop["id"]))["state"] in {"completed", "failed", "waiting_diagnosis"} else None), seconds=180)
        assert completed["state"] == "completed", completed
        assert completed["rounds"][0]["quality"]["assessment"] == "equal"
        assert completed["rounds"][0]["quality"]["recommendable"] is False
        assert sources.get(key)["recommended"] is None
        (repo / "build.py").write_text("raise RuntimeError('fixture failed build')\n")
        git(repo, "add", ".")
        git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "broken followup")
        new = git(repo, "rev-parse", "HEAD")
        queue_refresh(root, key)
        until(lambda: any(r["sha"] == new and r["status"] == "integration_failed" for r in sources.revisions(key)), seconds=120)
        assert sources.get(key)["observed"] == new
        assert sources.get(key)["admitted"] == next_release
        assert sources.round_release(key)["version"] == next_release
        assert commands.store.plan(plan).model_dump(mode="json") == before
        assert len([j for j in commands.list() if j["kind"] == "integration"]) == 3
    finally:
        stop(root)
