import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_harness_eval.console import Console
from agent_harness_eval.guardian import model_driver
from agent_harness_eval.guardian.config import GuardianConfig
from agent_harness_eval.model_pool import SCENES, ModelInput, ModelPool
from agent_harness_eval.resources.live_model import normalize, upstream_url
from agent_harness_eval.web import create_app


@pytest.fixture
def pool(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return ModelPool(tmp_path)


def add(pool, **kwargs):
    data = pool.save(
        ModelInput(
            label="Coding", name="deepseek-flash", api_key="private-model-key", **kwargs
        )
    )
    return data["models"][-1]["id"]


def test_migration_once_without_returning_credentials(pool):
    old = pool.root / ".rsi"
    old.mkdir()
    (old / "executor-settings.json").write_text(
        json.dumps({"executor": "sample_agent", "model": "existing-model"})
    )
    (old / "executor-key").write_text("existing-secret")
    first = pool.public()
    assert len(first["models"]) == 1
    assert first["models"][0]["name"] == "existing-model"
    assert len(first["bindings"]) == len(SCENES)
    assert "existing-secret" not in json.dumps(first)
    assert "key_ref" not in json.dumps(first)
    assert pool.public() == first


def test_snapshot_and_secret_version_survive_edits_and_deletion(pool):
    identity = add(pool)
    pool.bind({"experiment": identity})
    frozen = pool.resolve("experiment")
    first_key = pool.environment(frozen)
    assert list(first_key.values()) == ["private-model-key"]
    pool.save(
        ModelInput(label="Changed", name="new-model", api_key="new-secret"), identity
    )
    assert pool.resolve("experiment")["name"] == "new-model"
    assert pool.environment(frozen) == first_key
    assert frozen["name"] == "deepseek-flash"
    pool.save(ModelInput(label="Changed", name="new-model", api_key=""), identity)
    assert list(pool.environment(pool.resolve("experiment")).values()) == ["new-secret"]
    with pytest.raises(ValueError, match="场景"):
        pool.delete(identity)
    pool.bind({"experiment": None})
    pool.delete(identity)
    assert pool.public()["models"] == []
    assert pool.environment(frozen) == first_key
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in pool.keys.iterdir())


def test_dsh_requires_messages_and_bindings_validate(pool):
    identity = add(pool, api_base="https://chat.example/v1")
    with pytest.raises(ValueError, match="Messages"):
        pool.bind({"executor_dsh": identity})
    with pytest.raises(ValueError):
        pool.bind({"unknown": identity})
    with pytest.raises(ValueError):
        pool.bind({"experiment": "missing"})
    pool.bind({"executor_opencode": identity})
    assert pool.resolve("executor_opencode")["api_base"] == "https://chat.example/v1"


def test_api_validation_never_echoes_key_and_rejects_cross_site(pool):
    client = TestClient(create_app(pool.root))
    headers = {"Host": "localhost"}
    response = client.post(
        "/api/models",
        headers=headers,
        json={
            "label": "Example",
            "name": "m",
            "api_key": "private-model-key",
            "api_base": "https://key:secret@example.com",
        },
    )
    assert response.status_code == 422
    assert "private-model-key" not in response.text and "secret" not in response.text
    assert (
        client.post(
            "/api/models",
            headers={**headers, "Origin": "https://evil.example"},
            json={"label": "x", "name": "m", "api_key": "key"},
        ).status_code
        == 403
    )
    response = client.post(
        "/api/models",
        headers=headers,
        json={"label": "Example", "name": "m", "api_key": "private-model-key"},
    )
    assert response.status_code == 201 and "private-model-key" not in response.text
    assert "private-model-key" not in client.get("/api/models", headers=headers).text


@pytest.mark.parametrize("with_review", [False, True])
def test_experiment_submission_freezes_global_selection_and_keeps_controls_offline(
    pool, with_review,
):
    repo = Path(__file__).resolve().parents[1]
    shutil.copytree(repo / "tasks", pool.root / "tasks")
    (pool.root / "experiments").mkdir()
    shutil.copyfile(
        repo / "experiments/controls.json", pool.root / "experiments/controls.json"
    )
    identity = add(pool)
    pool.bind({"experiment": identity, "guardian": identity})
    console = Console(pool.root, allow_live=True)
    offline = console.submit({"template": "controls"})
    assert (
        json.loads(console.row(offline["id"])["config"])["model"]["mode"] == "offline"
    )
    with pytest.raises(ValueError, match="离线"):
        console.submit({"template": "controls", "model_id": identity})
    template = json.loads((pool.root / "experiments/controls.json").read_text())
    template["model"] = {"mode": "live", "spending": "unbounded"}
    if with_review:
        template["model"]["search_review"] = {
            "endpoint": "https://openrouter.ai/api/v1/systemone",
            "model": "typesafe/jev-1.13-20260917",
            "api_key_env": "EVAL_SEARCH_REVIEW_API_KEY",
        }
        from agent_harness_eval.batching import search_network_policy
        for task in template["benchmark"]["tasks"]:
            policy = (pool.root / "experiments" / task).resolve() / "task.toml"
            policy.write_text(search_network_policy(policy.read_text(), ["openrouter.ai"]))
    template["guardian"] = {
        "mode": "assist",
        "command": ["existing-driver"],
        "version": "v1",
    }
    (pool.root / "experiments/live.json").write_text(json.dumps(template))
    with pytest.raises(ValueError):
        console.submit({"template": "live", "model_id": identity})
    run = console.submit(
        {
            "template": "live",
            "model_id": identity,
            "guardian_model_id": identity,
            "confirm_unbounded": True,
        }
    )
    frozen = json.loads(console.row(run["id"])["config"])
    assert frozen["model"]["pool_id"] == identity
    if with_review:
        assert frozen["model"]["search_review"]["model"] == "typesafe/jev-1.13-20260917"
        assert frozen["model"]["search_review"]["api_key_env"] == "EVAL_SEARCH_REVIEW_API_KEY"
    else:
        assert frozen["model"]["search_review"] is None
    assert frozen["guardian"]["model"]["pool_id"] == identity
    assert "private-model-key" not in json.dumps(frozen)
    pool.save(ModelInput(label="new", name="new", api_key="new-key"), identity)
    assert json.loads(console.row(run["id"])["config"]) == frozen
    assert list(pool.environment(frozen["model"]).values()) == ["private-model-key"]


def test_guardian_observe_does_not_gain_model_calls(pool):
    identity = add(pool)
    config = GuardianConfig(mode="observe", model=pool.resolve("guardian", identity))
    assert config.mode == "observe" and config.command == []


def test_configured_endpoints_and_provider_parameters():
    model = {
        "name": "custom",
        "max_tokens": 300,
        "api_base": "https://example.test/v1",
        "messages_base": "https://example.test/anthropic",
    }
    assert upstream_url(model) == "https://example.test/v1/chat/completions"
    assert upstream_url(model, True) == "https://example.test/anthropic/v1/messages"
    assert normalize({"messages": []}, False, model) == {
        "messages": [],
        "model": "custom",
        "max_tokens": 300,
    }
    assert (
        upstream_url({"api_base": "https://api.deepseek.com"}, True)
        == "https://api.deepseek.com/anthropic/v1/messages"
    )


@pytest.mark.parametrize(
    "base",
    [
        "https://example.test/v1",
        "https://api.deepseek.com",
        "https://api.deepseek.com/v1/",
    ],
)
def test_guardian_driver_uses_selected_model_and_key(monkeypatch, base):
    import io
    from contextlib import closing

    monkeypatch.setenv("TEST_MODEL_KEY", "guardian-key")
    captured = []

    def request(req, timeout):
        captured.append(req)
        return closing(
            io.BytesIO(
                json.dumps({"choices": [{"message": {"tool_calls": []}}]}).encode()
            )
        )

    monkeypatch.setattr(model_driver, "urlopen", request)
    model_driver.request_message(
        {
            "name": "guardian-model",
            "api_base": base,
            "api_key_env": "TEST_MODEL_KEY",
            "max_tokens": 1000,
        },
        [{"role": "user", "content": "diagnose"}],
    )
    expected_base = base.rstrip("/")
    assert captured[0].full_url == expected_base + "/chat/completions"
    body = json.loads(captured[0].data)
    assert body["model"] == "guardian-model"
    assert body["tool_choice"] == "required"
    if "api.deepseek.com" in base:
        assert body["thinking"] == {"type": "disabled"}
    else:
        assert "thinking" not in body
    assert captured[0].get_header("Authorization") == "Bearer guardian-key"


def test_guardian_model_driver_completes_supervisor_protocol(tmp_path, monkeypatch):
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from agent_harness_eval.guardian.driver import run_driver

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            seen.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "finish",
                            "arguments": json.dumps(
                                {
                                    "summary": "Fixture diagnosis",
                                    "assessment": "observe",
                                }
                            ),
                        },
                    }
                ],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": message}]}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("GUARDIAN_TEST_KEY", "local-fixture-key")
    config = GuardianConfig(
        mode="assist",
        version="model-pool-driver/v1",
        model={
            "mode": "live",
            "name": "guardian-fixture",
            "api_base": f"http://127.0.0.1:{server.server_port}",
            "api_key_env": "GUARDIAN_TEST_KEY",
            "spending": "unbounded",
        },
    )
    try:
        result = asyncio.run(run_driver(config, tmp_path, None, lambda: True))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert result["status"] == "finished"
    assert seen[0]["model"] == "guardian-fixture"
    assert "local-fixture-key" not in (tmp_path / "driver-context.json").read_text()
