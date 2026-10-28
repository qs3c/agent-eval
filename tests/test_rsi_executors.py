import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest
from agent_harness_eval.integrations import catalog as recipes
from agent_harness_eval.integrations.builder import build
from agent_harness_eval.integrations.schema import Recipe, load
from agent_harness_eval.integrations.store import Releases
from agent_harness_eval.platform_agents import releases
from agent_harness_eval.rsi import native_adapter
from agent_harness_eval.rsi.executors import ExecutorSettings, catalog, save_settings
from agent_harness_eval.model_pool import ModelPool, ModelInput
from test_release_build import repository, fake_build

@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / "platform"
    root.mkdir()
    source = repository(tmp_path)
    sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    originals = {name: load(recipes.recipe_path(name)) for name in releases.EXECUTORS}
    directory = tmp_path / "recipes"
    directory.mkdir()
    for name, original in originals.items():
        data = original.model_dump(mode="json")
        data["source"] = {"repository": "https://example.com/agent.git", "commit": sha}
        data["patches"] = []
        recipe = Recipe.model_validate(data)
        (directory / (name + ".yaml")).write_text(recipe.model_dump_json())
        output = tmp_path / ("built-" + name)
        build(recipe, source, output, recipe_directory=directory, executor=fake_build)
        Releases(root).add(output)
    monkeypatch.setattr(recipes, "RECIPES", directory)
    (root / ".cache").mkdir()
    (root / ".cache/platform-runtime.json").write_text(json.dumps({"image": "sha256:" + "b" * 64}))
    return root

def test_public_catalog_has_only_three_independent_executors(installed):
    options = releases.catalog(installed)
    assert {o["id"] for o in options} == {"opencode", "dsh", "hermes"}
    assert all(o["ready"] and o["release_id"] for o in options)
    path, receipt = releases.resolve(installed, "hermes")
    (path / "disabled.json").write_text("{}")
    with pytest.raises(ValueError, match="禁用"):
        releases.resolve(installed, "hermes", receipt["release_id"])
    assert releases.resolve(installed, "dsh")

def test_tampered_release_is_not_an_executor(installed):
    path, receipt = releases.resolve(installed, "opencode")
    (path / "agent.tar").write_bytes(b"changed")
    with pytest.raises(ValueError, match="无效"):
        releases.resolve(installed, "opencode", receipt["release_id"])

@pytest.mark.parametrize("name", ["opencode", "dsh", "hermes"])
def test_settings_use_public_model_pool(installed, name):
    result = save_settings(installed, ExecutorSettings(executor=name, api_key="fixture-key"))
    assert result["ready"] and result["executor"] == name
    assert "fixture-key" not in (installed / ".rsi/executor-settings.json").read_text()
    assert {o["id"] for o in catalog(installed)["options"]} == {"opencode", "dsh", "hermes"}

@pytest.mark.parametrize("name,entry", [("opencode", "entry.js"), ("dsh", "entry.mjs"), ("hermes", "entry.py")])
def test_native_executor_uses_frozen_recipe_and_readonly_source(installed, tmp_path, monkeypatch, name, entry):
    _, receipt = releases.resolve(installed, name)
    pool = ModelPool(installed)
    model_id = pool.save(ModelInput(label="fixture", name="fixed-model", api_key="test-key"))["models"][-1]["id"]
    model = pool.resolve("integration", model_id)
    model["policy"] = {"protocols": [receipt["recipe"]["model_protocol"]], "budget": {"max_requests": 2}}
    workspace, evidence, source = [tmp_path / n for n in ("candidate", "evidence", "readonly")]
    for p in (workspace, evidence, source): p.mkdir()
    result = tmp_path / "result.json"
    calls = []
    def container(image, mounts, command, timeout, **kwargs):
        calls.append(command)
        assert entry in " ".join(command)
        assert (source, "/source", True) in mounts
        assert next(m for m in mounts if m[1] == "/installed-agent/recipe")[2] is True
        request = json.loads(next(m[0] for m in mounts if m[1] == "/installed-agent/request.json").read_text())
        assert request["model"]["id"] == "fixed-model"
        assert "test-key" not in json.dumps(request)
        logs = next(m[0] for m in mounts if m[1] == "/logs/agent")
        (logs / "agent-result.json").write_text('{"schema_version":1,"status":"completed"}')
        output = next(m[0] for m in mounts if m[1] == "/output")
        (output / "integration.json").write_text('{"summary":"协议夹具候选"}')
    monkeypatch.setattr(native_adapter, "run_container", container)
    args = SimpleNamespace(root=installed, executor=name, release=receipt["release_id"], image="sha256:" + "b" * 64,
        timeout=30, model_policy_json=json.dumps(model))
    native_adapter.execute(args, {"purpose":"integration_recipe", "instruction":"inspect source", "workspace":str(workspace),
        "evidence_directory":str(evidence), "source_directory":str(source), "result_path":str(result)})
    assert json.loads(result.read_text())["summary"] == "协议夹具候选"
    assert len(calls) == 1

def test_native_timeout_cleans_only_owned_container(tmp_path, monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "run"]: raise subprocess.TimeoutExpired(argv, 1)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        native_adapter.run_container("fixture", [(tmp_path, "/workspace", False)], ["false"], 1)
    assert calls[-1][:3] == ["docker", "rm", "-f"]
    assert calls[-1][-1] == calls[0][calls[0].index("--name") + 1]
