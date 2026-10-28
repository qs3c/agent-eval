import asyncio
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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


def search_experiment():
    data = json.loads(Path("experiments/controls.json").read_text())
    data["model"] = {"mode": "live", "name": "fixture", "spending": "unbounded"}
    data["agents"][0]["implementation"] = "external_test_adapter:Agent"
    data["agents"][0]["kwargs"] = {}
    return data


def test_config_rejects_search_on_mock_and_credentials():
    data = search_experiment()
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

    data = search_experiment()
    data["agents"][0]["kwargs"]["web_search"] = {"provider": "exa", "credential_env": "MISSING_SEARCH_KEY"}
    monkeypatch.delenv("MISSING_SEARCH_KEY", raising=False)
    with pytest.raises(ValueError, match="Missing web search credential"):
        asyncio.run(run_experiment(Experiment.model_validate(data), tmp_path / "run"))
    assert not (tmp_path / "run").exists()
