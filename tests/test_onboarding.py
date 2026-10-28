import json

import pytest
from fastapi.testclient import TestClient

from agent_harness_eval.integrations.assistant import submit
from agent_harness_eval.integrations.onboarding import instructions
from agent_harness_eval.integrations.sources import Sources
from agent_harness_eval.model_pool import ModelPool, ModelInput
from agent_harness_eval.operations.commands import Commands
from agent_harness_eval.platform_agents.releases import resolve
from agent_harness_eval.web import create_app
from test_rsi_executors import installed


def test_external_instructions_are_readonly_and_include_contract(tmp_path):
    result = instructions(tmp_path, "https://github.com/example/agent", "main")
    assert "docs/agent-integration.md" in result["instructions"]
    assert "integration validate" in result["instructions"]
    assert "两次完整隔离尝试" in result["instructions"]
    assert not (tmp_path / ".workspace").exists()


@pytest.mark.parametrize("executor", ["opencode", "dsh", "hermes"])
def test_assist_freezes_explicit_executor_model_and_budget(installed, executor):
    source = Sources(installed).add("https://github.com/example/agent")
    pool = ModelPool(installed)
    model_id = pool.save(ModelInput(label="fixture", name="fixed", api_key="secret", policy={"protocols": ["anthropic-messages" if executor == "dsh" else "openai-chat"], "budget": {"max_requests": 3}}))["models"][-1]["id"]
    job = submit(installed, source["id"], executor=executor, model_id=model_id, confirm_paid=True)
    payload = job["payload"]
    assert payload["executor"] == executor
    assert payload["executor_release"] == resolve(installed, executor)[1]["release_id"]
    assert payload["model"]["pool_id"] == model_id
    assert payload["model"]["policy"]["budget"]["max_requests"] == 3
    assert payload["source"]["observed"] is None
    assert Sources(installed).get(source["id"])["admitted"] is None
    assert "secret" not in json.dumps(payload)
    assert not (installed / ".rsi/executor-settings.json").exists()


def test_assist_rejects_switching_executor_during_active_job(installed):
    source = Sources(installed).add("https://github.com/example/agent")
    pool = ModelPool(installed)
    model_id = pool.save(ModelInput(label="fixture", name="fixed", api_key="secret"))["models"][-1]["id"]
    first = submit(installed, source["id"], executor="opencode", model_id=model_id, confirm_paid=True)
    assert submit(installed, source["id"], executor="opencode", model_id=model_id, confirm_paid=True)["id"] == first["id"]
    with pytest.raises(ValueError, match="已有接入任务"):
        submit(installed, source["id"], executor="hermes", model_id=model_id, confirm_paid=True)


def test_dsh_rejects_chat_only_model_before_queueing(installed):
    source = Sources(installed).add("https://github.com/example/agent")
    model_id = ModelPool(installed).save(ModelInput(label="chat", name="fixed", api_base="https://fixture.invalid/v1", api_key="secret"))["models"][-1]["id"]
    with pytest.raises(ValueError, match="Messages"):
        submit(installed, source["id"], executor="dsh", model_id=model_id, confirm_paid=True)
    assert not Commands(installed).list()


def test_external_import_enters_shared_admission_without_model(installed, tmp_path):
    recipe = tmp_path / "external.json"
    recipe.write_text(json.dumps({"id": "user-agent", "source": {"repository": "https://github.com/example/agent"},
        "build": {"image": "sha256:" + "f" * 64, "argv": ["python3", "build.py"]},
        "entrypoint": {"python": "agent:execute"}, "model_modes": ["offline"], "model_protocol": "none"}))
    client = TestClient(create_app(installed), base_url="http://localhost")
    response = client.post("/api/integrations/import", headers={"Origin":"http://localhost"},
        json={"repository":"https://github.com/example/agent", "recipe":str(recipe)})
    assert response.status_code == 202, response.text
    value = response.json()
    assert value["job"]["kind"] == "source-refresh"
    assert value["source"]["admitted"] is None
    assert value["source"]["recipe"] == str(recipe)
    assert all(j["kind"] != "integration-assist" for j in Commands(installed).list())


def test_auto_endpoint_requires_paid_authorization_and_valid_executor(installed):
    client = TestClient(create_app(installed), base_url="http://localhost")
    headers = {"Origin":"http://localhost"}
    request = {"repository":"https://github.com/example/agent", "executor":"opencode", "confirm_paid":True}
    refused = client.post("/api/integrations/onboard", headers=headers, json=request)
    assert refused.status_code == 422 and "allow-live" in refused.text
    assert client.post("/api/integrations/onboard", headers=headers, json={**request,"executor":"custom-shell"}).status_code == 422
    assert not Sources(installed).list()
