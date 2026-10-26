import json

import pytest

from agent_harness_eval.config import Model
from agent_harness_eval.policies.model import Settings, freeze, verify_usage
from agent_harness_eval.policies.budget import Budget
from agent_harness_eval.resources.policy_runtime import Counter, normalize


def profile():
    return {"name": "shared-model", "mode": "mock", "max_tokens": 100,
            "policy": {"parameters": {"temperature": 0, "top_p": 1}, "protocols": ["openai-chat", "anthropic-messages"]}}


def test_freeze_detaches_snapshot_and_does_not_change_legacy_serialization():
    original = profile()
    policy = freeze(original)
    original["name"] = "replacement"
    original["policy"]["parameters"]["temperature"] = 1
    assert policy.model["name"] == "shared-model"
    assert policy.settings.parameters["temperature"] == 0
    assert "policy" not in Model().model_dump(mode="json")
    assert "api_key" not in policy.model


def test_usage_or_price_unknown_does_not_become_free_or_fair():
    policy = freeze(profile())
    body = normalize({"model": "other", "temperature": 2}, policy.model_dump(mode="json"), "openai-chat")
    report = verify_usage(policy, {"requests": [{"body": body, "role": "agent", "policy_digest": policy.digest}]})
    assert not report["passed"]
    assert report["cost_usd"] is None
    assert any("覆盖" in reason for reason in report["reasons"])
    with pytest.raises(ValueError, match="美元硬上限"):
        Budget(hard_cost_usd=1)
    with pytest.raises(ValueError, match="Unsupported"):
        Settings(parameters={"api_key": "not-a-reference"})


def test_persisted_role_budget_does_not_reset_when_relay_restarts(tmp_path):
    path = tmp_path / "ledger.jsonl"
    counter = Counter(path, 2)
    assert counter.reserve("agent", "digest")
    assert Counter(path, 2).reserve("agent", "digest")
    assert not Counter(path, 2).reserve("agent", "digest")
    assert len(path.read_text().splitlines()) == 2


def test_network_review_policy_changes_fingerprint_without_claiming_coverage(tmp_path):
    from agent_harness_eval.policies.network import freeze as freeze_network

    task = tmp_path / "task"
    task.mkdir()
    (task / "task.toml").write_text('[agent]\nnetwork_mode = "no-network"\n')
    first = freeze_network([task], review={"model": "jev-1.0", "deny_threshold": .5})
    second = freeze_network([task], review={"model": "jev-1.0", "deny_threshold": .6})
    assert first["digest"] != second["digest"]
    assert "不自动" in first["review_scope"]
    (task / "task.toml").write_text('schema_version = "1.4"\n')
    assert freeze_network([task])["tasks"][0]["mode"] == "unspecified"


def test_model_pool_edits_do_not_change_frozen_role_or_key_reference(tmp_path):
    from agent_harness_eval.model_pool import ModelInput, ModelPool

    pool = ModelPool(tmp_path)
    first = pool.save(ModelInput(label="测评", name="eval-model", api_key="first-private-key",
        policy=Settings(parameters={"temperature": 0})))
    key = first["models"][0]["id"]
    pool.bind({"experiment": key, "diagnosis": key})
    frozen = pool.frozen_policy("agent")
    diagnosis = pool.frozen_policy("diagnosis")
    pool.save(ModelInput(label="更新后", name="next-model", api_key="next-private-key"), key)
    assert frozen.model["name"] == "eval-model"
    assert frozen.model["api_key_env"] != pool.resolve("experiment")["api_key_env"]
    assert frozen.role == "agent" and diagnosis.role == "diagnosis"
    assert frozen.digest != diagnosis.digest
    assert "first-private-key" not in frozen.model_dump_json()


def test_comparison_exposes_observed_consistency_without_promoting_self_report(tmp_path):
    from agent_harness_eval.policies.assurance import assess

    config = profile()
    exp = {"model": config, "agents": [{"id": "own"}], "benchmark": {"tasks": ["one"]}, "attempts": 1}
    policy = freeze(config)
    logs = tmp_path / "jobs/own/one/agent"
    logs.mkdir(parents=True)
    (logs / "model-policy-report.json").write_text(json.dumps({"policy_digest": policy.digest,
        "observed_consistent": True, "passed": True, "reasons": []}))
    result = assess(tmp_path, exp)
    assert result["agents"]["own"]["observed_consistent"]
    assert not result["comparable"]
    assert any("尚未认证" in reason for reason in result["agents"]["own"]["reasons"])
