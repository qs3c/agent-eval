import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_harness_eval.rsi import cycle, merge_tracking
from agent_harness_eval.integrations.sources import Sources
from agent_harness_eval.integrations.store import Releases
from agent_harness_eval.integrations import tracking
from agent_harness_eval.operations.commands import Commands
from agent_harness_eval.rsi.store import Store


@pytest.mark.parametrize("merged,state,sha", [(True, "merged", "c" * 40), (True, "merged", "d" * 40), (False, "open", None), (False, "closed", None)])
def test_github_read_only_merge_and_squash_use_actual_target_sha(merged, state, sha):
    url = "https://github.com/fixture/agent/pull/3"
    def run(argv, **kwargs):
        assert argv == ["gh", "api", "repos/fixture/agent/pulls/3"]
        return SimpleNamespace(stdout=json.dumps({"html_url": url, "merged": merged, "state": "closed" if merged else state,
            "merge_commit_sha": sha, "base": {"repo": {"full_name": "fixture/agent"}, "ref": "main", "sha": "a" * 40}, "head": {"sha": "b" * 40}}))
    result = merge_tracking.observe(url, run=run)
    assert result["state"] == state and result["merge_sha"] == sha
    if merged:
        assert result["merge_sha"] != result["head_sha"]


@pytest.fixture
def context(tmp_path, monkeypatch):
    source = Sources(tmp_path).add("https://github.com/fixture/agent", "main")
    Sources(tmp_path).observe(source["id"], "a" * 40)
    with Sources(tmp_path).transaction() as db:
        db.execute("UPDATE sources SET admitted='baseline' WHERE id=?", (source["id"],))
    def agent(self, identity):
        return {"id": "fixture", "implementation": "nop", "version": identity, "kwargs": {}}
    monkeypatch.setattr(Releases, "agent", agent)
    monkeypatch.setattr(tracking, "refresh", lambda *args, **kwargs: None)
    monkeypatch.setattr(tracking, "ensure_build", lambda *args, **kwargs: None)
    project = Path(__file__).parents[1]
    config = json.loads((project / "experiments/controls.json").read_text())
    config["model"] = {"mode": "offline"}
    config["benchmark"]["tasks"] = [str(project / "tasks/file-create"), str(project / "tasks/file-preserve")]
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps(config))
    record = Store(tmp_path).create("candidates", {"parent_commit": "a" * 40, "commit": "b" * 40,
        "profile": {"github_repository": "fixture/agent"}, "pr_url": "https://github.com/fixture/agent/pull/3", "validation": {"original": "preserved"}})
    observation = {"url": record["pr_url"], "repository": source["repository"], "state": "open", "base_ref": "main", "base_sha": "a" * 40, "head_sha": "b" * 40, "merge_sha": None}
    return tmp_path, source, evaluation, record, observation


def test_base_drift_preserves_validation_and_closed_pr_stops(context):
    root, source, evaluation, candidate, observation = context
    value = cycle.create(root, source["id"], evaluation, candidate=candidate["id"])
    observation["base_sha"] = "f" * 40
    result = cycle.refresh(root, value["id"], observer=lambda _: observation)
    assert result["state"] == "waiting_merge"
    updated = Store(root).get("candidates", candidate["id"])
    assert updated["validation_stale"] is True
    assert updated["validation"] == candidate["validation"]
    observation["state"] = "closed"
    assert cycle.refresh(root, value["id"], observer=lambda _: observation)["state"] == "stopped"
    assert not Commands(root).list()


def test_merge_admission_wait_and_retry_does_not_republish_or_duplicate_run(context):
    root, source, evaluation, candidate, observation = context
    value = cycle.create(root, source["id"], evaluation, candidate=candidate["id"])
    observation.update(state="merged", merge_sha="c" * 40)
    assert cycle.refresh(root, value["id"], observer=lambda _: observation)["state"] == "waiting_admission"
    sources = Sources(root)
    assert sources.get(source["id"])["observed"] == "a" * 40
    revision = sources.revisions(source["id"])[-1]
    assert revision["sha"] == "c" * 40 and revision["pinned"] == 1
    sources.update_revision(source["id"], "c" * 40, status="admitted", release="merged")
    running = cycle.refresh(root, value["id"], observer=lambda _: observation)
    assert running["state"] == "evaluating" and running["rounds"][0]["source_sha"] == "c" * 40
    plan_id = running["rounds"][0]["plan_id"]
    before = Commands(root).store.plan(plan_id).model_dump(mode="json")
    sources.observe(source["id"], "f" * 40)
    cycle.refresh(root, value["id"], observer=lambda _: observation)
    assert Commands(root).store.plan(plan_id).model_dump(mode="json") == before
    assert len(Commands(root).list()) == 1
    # Crash between submitting the experiment and saving the cycle journal.
    value.update(state="waiting_admission", rounds=[])
    cycle.save(root, value)
    recovered = cycle.refresh(root, value["id"], observer=lambda _: observation)
    assert recovered["rounds"][0]["plan_id"] == plan_id
    assert len(Commands(root).list()) == 1


def test_external_push_budget_and_frozen_conditions(context):
    root, source, evaluation, _, _ = context
    value = cycle.create(root, source["id"], evaluation, max_rounds=2, max_attempts=4)
    sources = Sources(root)
    sources.observe(source["id"], "d" * 40)
    sources.update_revision(source["id"], "d" * 40, status="admitted", release="external")
    with sources.transaction() as db:
        db.execute("UPDATE sources SET admitted='external' WHERE id=?", (source["id"],))
    result = cycle.refresh(root, value["id"])
    assert result["state"] == "evaluating" and len(result["rounds"]) == 1
    result.update(state="waiting_update", last_release="external")
    cycle.save(root, result)
    with sources.transaction() as db:
        db.execute("UPDATE sources SET admitted='another' WHERE id=?", (source["id"],))
    assert cycle.refresh(root, value["id"])["state"] == "stopped"
    assert len(Commands(root).list()) == 1
    frozen = cycle.path(root, value["id"]) / "evaluation.json"
    assert json.loads(frozen.read_text())["model"]["mode"] == "offline"
    assert sources.get(source["id"])["recommended"] is None


def test_lost_github_response_is_read_only_and_retriable(context):
    root, source, evaluation, candidate, _ = context
    value = cycle.create(root, source["id"], evaluation, candidate=candidate["id"])
    def lost(_):
        raise subprocess.TimeoutExpired("gh api", 30)
    with pytest.raises(subprocess.TimeoutExpired):
        cycle.refresh(root, value["id"], observer=lost)
    assert cycle.get(root, value["id"])["state"] == "waiting_merge"
    assert not Commands(root).list()
    value["created"] = time.time() - value["max_seconds"] - 1
    cycle.save(root, value)
    assert cycle.refresh(root, value["id"])["state"] == "stopped"


def test_deadline_cancels_active_round_without_waiting_for_worker_completion(context):
    root, source, evaluation, _, _ = context
    value = cycle.create(root, source["id"], evaluation)
    job = Commands(root).submit("experiment", {"fixture": True}, key="active-round")
    claimed = Commands(root).claim("fixture-owner")
    value.update(state="evaluating", created=time.time() - 90000, rounds=[{"job": job["id"]}])
    cycle.save(root, value)
    cycle.enforce_deadlines(root)
    assert cycle.get(root, value["id"])["state"] == "stopped"
    assert Commands(root).query(claimed["id"])["status"] == "cancelling"


def test_disabling_release_blocks_new_rounds_but_keeps_source_history(context):
    root, source, evaluation, _, _ = context
    value = cycle.create(root, source["id"], evaluation)
    sources = Sources(root)
    sources.observe(source["id"], "d" * 40)
    sources.update_revision(source["id"], "d" * 40, status="admitted", release="new")
    with sources.transaction() as db:
        db.execute("UPDATE sources SET admitted='new' WHERE id=?", (source["id"],))
    disabled = Releases(root).directory / "new/disabled.json"
    disabled.parent.mkdir()
    disabled.write_text("{}")
    assert cycle.refresh(root, value["id"])["state"] == "stopped"
    with pytest.raises(ValueError, match="disabled"):
        sources.round_release(source["id"])
    assert len(sources.revisions(source["id"])) == 2
    assert not Commands(root).list()


def test_web_exposes_source_controls_and_cycle_status_without_starting_models(tmp_path):
    from fastapi.testclient import TestClient
    from agent_harness_eval.web import create_app

    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1")
    added = client.post("/api/sources", json={"repository": "https://github.com/Fixture/Agent"})
    assert added.status_code == 202, added.text
    key = added.json()["source"]["id"]
    assert client.post(f"/api/sources/{key}/monitor", json={"enabled": False}).json()["enabled"] == 0
    assert client.get("/api/sources").json()["sources"][0]["recommended"] is None
    assert client.get("/api/rsi/cycles").json() == {"cycles": []}
    refused = client.post(f"/api/sources/{key}/assist", json={"executor": "dsh", "confirm_paid": True})
    assert refused.status_code == 422 and "allow-live" in refused.text
