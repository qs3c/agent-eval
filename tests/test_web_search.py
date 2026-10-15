import asyncio
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_harness_eval.agents.bkcrab import Bkcrab
from agent_harness_eval.batching import search_network_policy
from agent_harness_eval.config import Experiment
from agent_harness_eval.search import WebSearch


def test_search_network_policy_preserves_other_tables():
    original = '''[agent]
network_mode = "no-network"
allowed_hosts = [
  "old.example"
]
timeout_sec = 10
[verifier]
network_mode = "no-network"
'''
    hosts = ["api.deepseek.com", "api.exa.ai"]
    updated = search_network_policy(original, hosts)
    data = tomllib.loads(updated)
    assert data["agent"]["allowed_hosts"] == hosts
    assert data["agent"]["timeout_sec"] == 10
    assert data["verifier"]["network_mode"] == "no-network"
    assert search_network_policy(updated, hosts) == updated


@pytest.mark.parametrize("config", [
    {"provider": "exa"},
    {"provider": "searxng", "endpoint": "http://user:secret@host"},
    {"provider": "searxng", "endpoint": "https://host?token=secret"},
    {"provider": "brave", "credential_env": "key=value"},
])
def test_invalid_search_config(config):
    with pytest.raises(ValueError):
        WebSearch.model_validate(config)


def test_config_rejects_search_on_mock_and_credentials():
    data = json.loads(Path("experiments/swe-live-80-assist.json").read_text())
    data["agents"][0]["kwargs"]["web_search"] = {"provider": "exa", "credential_env": "SEARCH_KEY"}
    Experiment.model_validate(data)
    data["model"]["mode"] = "mock"
    with pytest.raises(ValueError, match="external web_search requires live mode"):
        Experiment.model_validate(data)
    data["model"]["mode"] = "live"
    data["agents"][0]["kwargs"]["web_search"]["api_key"] = "never-store-this"
    with pytest.raises(ValueError, match="credentials belong"):
        Experiment.model_validate(data)


def test_missing_search_key_fails_before_preparation(tmp_path, monkeypatch):
    from agent_harness_eval.run import run_experiment

    data = json.loads(Path("experiments/swe-live-80-assist.json").read_text())
    data["agents"][0]["kwargs"]["web_search"] = {"provider": "exa", "credential_env": "MISSING_SEARCH_KEY"}
    monkeypatch.delenv("MISSING_SEARCH_KEY", raising=False)
    with pytest.raises(ValueError, match="Missing web search credential"):
        asyncio.run(run_experiment(Experiment.model_validate(data), tmp_path / "run"))
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("transport", ["direct", "platform-relay"])
def test_adapter_uploads_private_key_and_passes_only_file_path(tmp_path, monkeypatch, transport):
    agent = object.__new__(Bkcrab)
    agent.binary = tmp_path / "binary"
    agent.web_search = WebSearch(provider="exa", credential_env="EVAL_SEARCH_TEST_KEY", transport=transport)
    agent.model_setup = AsyncMock()
    agent.exec_as_root = AsyncMock()
    agent.exec_as_agent = AsyncMock()
    agent.prepare = AsyncMock(return_value="/testbed")
    agent.wrap_model = lambda c: c
    agent.model_config = {"name": "fixture", "max_tokens": 100, "context_window": 1000}
    agent.timeout_seconds = 30
    uploaded = {}

    async def upload(source, target):
        if target.endswith("search.key"):
            uploaded[target] = Path(source).read_text()
            assert Path(source).stat().st_mode & 0o777 == 0o600

    environment = SimpleNamespace(upload_file=upload, default_user="agent")
    monkeypatch.delenv("EVAL_SEARCH_TEST_KEY", raising=False)
    with pytest.raises(ValueError, match="Missing web search credential"):
        asyncio.run(agent.install(environment))
    monkeypatch.setenv("EVAL_SEARCH_TEST_KEY", "private-fixture-key")
    asyncio.run(agent.install(environment))
    asyncio.run(agent.run("fixture", environment, None))
    assert uploaded == {"/installed-agent/search.key": "private-fixture-key"}
    command = agent.exec_as_agent.call_args.args[1]
    assert "--web-search-provider exa" in command
    if transport == "platform-relay":
        assert "--web-search-endpoint http://127.0.0.1:18765/search/exa" in command
        assert "--web-search-key-file" not in command
    else:
        assert "--web-search-key-file /installed-agent/search.key" in command
    assert "private-fixture-key" not in str(agent.exec_as_agent.call_args)
    assert "chmod 600" in agent.exec_as_root.call_args.args[1]
