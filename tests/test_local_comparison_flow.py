import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_harness_eval.console import Console
from agent_harness_eval.integrations.builder import build
from agent_harness_eval.integrations.schema import Recipe
from agent_harness_eval.integrations.store import Releases
from agent_harness_eval.web import create_app
from test_integration_recipe import fixture_recipe
from test_release_build import repository, fake_build


@pytest.fixture
def flow(tmp_path):
    root = tmp_path / "platform"
    root.mkdir()
    project = Path(__file__).parents[1]
    shutil.copytree(project / "tasks", root / "tasks")
    (root / "experiments").mkdir()
    shutil.copyfile(project / "experiments/controls.json", root / "experiments/controls.json")
    source = repository(tmp_path)
    record = build(Recipe.model_validate(fixture_recipe()), source, tmp_path / "built", recipe_directory=tmp_path, executor=fake_build)
    Releases(root).add(tmp_path / "built")
    return Console(root), record["release_id"]


def test_preflight_freezes_subset_and_rejects_changed_content_without_queueing(flow):
    console, release = flow
    request = dict(template="controls", releases=[release], model_mode="mock", sample_count=1, sample_seed=45)
    preview = console.preflight(request)
    assert preview["planned_attempts"] == 1
    assert preview["model_calls_made"] == 0 and not preview["paid"]
    assert preview["estimated_cost_usd"] is None
    assert not console.runs()
    run = console.submit({**request, "preflight_digest": preview["digest"]})
    frozen = console.detail(run["id"])["experiment"]
    selected = Path(frozen["benchmark"]["tasks"][0])
    receipt = json.loads((selected.parent / "selection.json").read_text())
    assert receipt == preview["selection"]
    assert frozen["agents"][0]["version"] == release
    original = Path(preview["experiment"]["benchmark"]["tasks"][0]) / "instruction.md"
    original.write_text(original.read_text() + "\nchanged")
    assert "changed" not in (selected / "instruction.md").read_text()
    with pytest.raises(ValueError, match="重新预检"):
        console.submit({**request, "preflight_digest": preview["digest"]})
    assert len(console.runs()) == 1


def test_import_selection_rejects_disabled_and_wrong_protocol(flow):
    console, release = flow
    request = dict(template="controls", releases=[release])
    with pytest.raises(ValueError, match="模型模式"):
        console.submit(request)
    marker = Releases(console.root).directory / release / "disabled.json"
    marker.write_text("{}")
    with pytest.raises(ValueError, match="停用"):
        console.preflight({**request, "model_mode": "mock"})
    assert not console.runs()


def test_local_api_preflight_and_build_are_same_origin_and_never_start_on_read(flow):
    console, release = flow
    client = TestClient(create_app(console.root), base_url="http://127.0.0.1")
    response = client.get("/api/integrations")
    assert response.status_code == 200
    assert response.json()["releases"][0]["release_id"] == release
    request = dict(template="controls", releases=[release], model_mode="mock", sample_count=1)
    assert client.post("/api/runs/preflight", json=request).status_code == 200
    assert not client.get("/api/runs").json()["runs"]
    assert client.post("/api/integrations/build", json={"builtin": "hermes"}, headers={"Origin": "https://other.example"}).status_code == 403
    response = client.post("/api/integrations/build", json={"builtin": "hermes"})
    assert response.status_code == 202
    job = response.json()
    assert job["payload"]["fetch"] and not Path(job["payload"]["source"]).exists()
    assert job["status"] == "queued"


def test_live_preflight_requires_service_authorization_and_key_and_execution_confirmation(flow):
    from agent_harness_eval.model_pool import ModelInput

    console, release = flow
    saved = console.models.save(ModelInput(label="Fixture", name="test", api_base="https://fixture.invalid", api_key="fixture-secret"))
    request = dict(template="controls", releases=[release], model_mode="live", model_id=saved["models"][-1]["id"], sample_count=1)
    with pytest.raises(ValueError, match="allow-live"):
        console.preflight(request)
    console.allow_live = True
    report = console.preflight(request)
    assert report["paid"] and "fixture-secret" not in json.dumps(report)
    with pytest.raises(ValueError, match="确认"):
        console.submit({**request, "preflight_digest": report["digest"]})
    assert not console.runs()


def test_real_docker_custom_agent_and_builtin_share_model_through_product_flow(tmp_path, monkeypatch):
    import os
    import subprocess
    from agent_harness_eval.integrations.commands import submit
    from agent_harness_eval.operations.commands import Commands
    from agent_harness_eval.operations.manager import ensure, stop
    from test_integration_docker import until

    if not os.environ.get("BUILTIN_RELEASE_ROOT"):
        pytest.skip("requires installed builtin Docker Release")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = Path(__file__).parents[1]
    root = tmp_path / "project"
    root.mkdir()
    shutil.copytree(project / "tasks", root / "tasks")
    (root / "experiments").mkdir()
    config = json.loads((project / "experiments/controls.json").read_text())
    config["model"] = {"mode": "mock", "policy": {"parameters": {"temperature": 0, "top_p": 1}, "protocols": ["openai-chat", "anthropic-messages"]}}
    (root / "experiments/controls.json").write_text(json.dumps(config))
    source = repository(tmp_path)
    (source / "agent.py").write_text('''import json,sys,urllib.request
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text())
model=r["model"]
body={"model":model["id"],"messages":[{"role":"user","content":"write result.txt ready"}]}
body["tools"]=[{"type":"function","function":{"name":"write_file","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}}}}}]
request=urllib.request.Request(model["api_base"].rstrip("/")+"/chat/completions",data=json.dumps(body).encode(),headers={"Content-Type":"application/json"})
with urllib.request.urlopen(request) as response: response.read()
Path(r["workspace"],"result.txt").write_text("ready\\n")
Path(r["output_dir"],"agent-result.json").write_text(json.dumps({"schema_version":1,"status":"completed"}))
''')
    (source / "build.py").write_text("from pathlib import Path\nimport shutil\nPath('dist').mkdir()\nshutil.copy2('agent.py','dist/agent.py')\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "model fixture"], check=True)
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", "python:3.12.13-slim-bookworm", "--format", "{{json .RepoDigests}}"], text=True))[0]
    recipe = fixture_recipe(id="custom-fixture", build={"image": image, "argv": ["python3", "build.py"]})
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(json.dumps(recipe))
    catalog = Releases(root)
    builtin = next(row for row in Releases(os.environ["BUILTIN_RELEASE_ROOT"]).list() if row["release"]["agent_id"] == "bkcrab")
    catalog.add(Releases(os.environ["BUILTIN_RELEASE_ROOT"]).directory / builtin["release_id"])
    commands = Commands(root)
    ensure(root)
    try:
        job = submit(root, recipe_path, source)
        done = until(lambda: (row if (row := commands.query(job["id"]))["status"] in {"completed", "failed"} else None))
        assert done["status"] == "completed", done
        own = next(row for row in catalog.list() if row["release"]["agent_id"] == "custom-fixture")
        client = TestClient(create_app(root), base_url="http://127.0.0.1")
        request = {"template": "controls", "releases": [own["release_id"], builtin["release_id"]], "model_mode": "mock", "sample_count": 2}
        preview = client.post("/api/runs/preflight", json=request)
        assert preview.status_code == 200, preview.text
        submitted = client.post("/api/runs", json={**request, "preflight_digest": preview.json()["digest"]})
        assert submitted.status_code == 201, submitted.text
        run = submitted.json()
        done = until(lambda: (row if (row := client.get("/api/runs/" + run["id"]).json())["status"] in {"completed", "failed"} else None), seconds=180)
        assert done["status"] == "completed", done
        result = client.get("/api/runs/" + run["id"] + "/comparison").json()
        assert result["task_count"] == 2 and len(result["agents"]) == 2
        assert result["model_assurance"]["comparable"] is False
        for agent in done["report"]["agents"].values():
            assert agent["errors"] == 0 and agent["mean_on_scored"] == 1, done
        for release_id in request["releases"]:
            checked = client.post("/api/integrations/check", json={"release_id": release_id, "run_id": run["id"]})
            assert checked.status_code == 200, checked.text
            assert checked.json()["levels"]["runnable"]["passed"], checked.text
        path = Path(Console(root).row(run["id"])["path"])
        reports = list(path.glob("jobs/*/*/agent/model-policy-report.json"))
        assert len(reports) == 4
        assert all(json.loads(p.read_text())["observed_consistent"] for p in reports)
    finally:
        stop(root)
