from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_harness_eval.operations.commands import Commands


def test_submission_idempotency_and_claim_race(tmp_path):
    commands = Commands(tmp_path)
    first = commands.submit("console", {"run_id": "one"}, key="request")
    assert commands.submit("console", {"run_id": "one"}, key="request")["id"] == first["id"]
    with pytest.raises(ValueError, match="conflicts"):
        commands.submit("console", {"run_id": "two"}, key="request")
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(commands.claim, ["owner1", "owner2"]))
    assert sum(row is not None for row in claimed) == 1


def test_pause_resume_cancel_preserve_execution_identity(tmp_path):
    commands = Commands(tmp_path)
    job = commands.submit("console", {"run_id": "one", "cooperative_pause": True}, key="one")
    key = job["id"]
    assert commands.control(key, "pause")["status"] == "held"
    assert commands.claim("manager") is None
    commands.control(key, "resume")
    claimed = commands.claim("manager")
    commands.control(key, "pause")
    commands.worker_update(key, "manager", claimed["generation"], status="paused")
    assert commands.worker_update(key, "manager", claimed["generation"])["status"] == "paused"
    resumed = commands.control(key, "resume")
    assert resumed["generation"] == claimed["generation"]
    assert resumed["payload"] == job["payload"]
    commands.control(key, "cancel")
    assert commands.worker_update(key, "manager", claimed["generation"], status="completed")["status"] == "cancelled"
    with pytest.raises(ValueError, match="Stale"):
        commands.worker_update(key, "manager", claimed["generation"], status="completed")
    with pytest.raises(ValueError, match="reconciliation"):
        commands.control(key, "resume")


def test_pause_unsupported_executor_is_explicit(tmp_path):
    commands = Commands(tmp_path)
    job = commands.submit("rsi", {"action_id": "a"}, key="rsi")
    commands.claim("manager")
    with pytest.raises(ValueError, match="cannot pause"):
        commands.control(job["id"], "pause")


def test_queued_cancel_never_executes(tmp_path):
    commands = Commands(tmp_path)
    job = commands.submit("console", {"run_id": "one"}, key="one")
    commands.control(job["id"], "cancel")
    assert commands.claim("manager") is None
