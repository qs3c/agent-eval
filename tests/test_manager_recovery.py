import os
import asyncio
import json
import multiprocessing
import time
from pathlib import Path

import pytest

from agent_harness_eval.operations.commands import Commands
from agent_harness_eval.operations.recovery import reconcile, start_ticks


def test_crash_unknown_outcome_is_fenced_and_never_retried(tmp_path):
    commands = Commands(tmp_path)
    commands.submit("rsi", {"action_id": "publish"}, key="publish")
    job = commands.claim("old-owner")
    commands.worker_update(job["id"], "old-owner", job["generation"], pid=99999999, start_ticks="100")
    result = reconcile(commands)
    assert result == [{"id": job["id"], "reason": "external_outcome_unknown"}]
    assert commands.query(job["id"])["generation"] == job["generation"] + 1
    with pytest.raises(ValueError, match="Stale"):
        commands.worker_update(job["id"], "old-owner", job["generation"], status="completed")
    with pytest.raises(ValueError, match="reconciliation"):
        commands.control(job["id"], "resume")
    assert commands.claim("new-owner") is None


def test_live_process_and_pid_reuse_are_distinguished(tmp_path):
    commands = Commands(tmp_path)
    commands.submit("console", {"run_id": "first"}, key="first")
    job = commands.claim("old-owner")
    commands.worker_update(job["id"], "old-owner", job["generation"],
                           pid=os.getpid(), start_ticks=start_ticks(os.getpid()))
    assert reconcile(commands)[0]["reason"] == "worker_still_alive"


def test_finished_attempt_not_replayed_after_restart(tmp_path):
    commands = Commands(tmp_path)
    commands.submit("console", {"run_id": "first"}, key="first")
    job = commands.claim("old-owner")
    commands.worker_update(job["id"], "old-owner", job["generation"], status="completed")
    assert reconcile(Commands(tmp_path)) == []
    assert Commands(tmp_path).claim("new-owner") is None


def fake_manager(root):
    from agent_harness_eval.operations.manager import Manager
    import agent_harness_eval.operations.worker as worker

    def execute(manager, job):
        path = Path(root) / "executions.log"
        with path.open("a") as stream:
            stream.write(job["id"] + "\n")
        while not (Path(root) / "finish").exists():
            if manager.console.stop.wait(0.02):
                return

    worker.execute = execute
    Manager(root).run()


def eventually(predicate):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("Condition did not become true")


def test_real_manager_drain_and_web_shutdown_do_not_kill_worker(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from agent_harness_eval.operations.manager import status, stop
    from agent_harness_eval.web import create_app

    commands = Commands(tmp_path)
    job = commands.submit("experiment", {"test": "long-running"}, key="long")
    process = multiprocessing.get_context("fork").Process(target=fake_manager, args=(tmp_path,))
    process.start()
    try:
        eventually(lambda: (tmp_path / "executions.log").exists())
        with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
            assert client.get("/api/operations").status_code == 200
        assert status(tmp_path)["alive"]
        assert commands.query(job["id"])["status"] == "running"
        stop(tmp_path)
        time.sleep(0.1)
        assert process.is_alive()  # Drain waits for the current worker.
        (tmp_path / "finish").touch()
        process.join(6)
        assert process.exitcode == 0
        assert commands.query(job["id"])["status"] == "completed"
        assert len((tmp_path / "executions.log").read_text().splitlines()) == 1
    finally:
        if process.is_alive():
            process.kill()
            process.join()


def test_actual_manager_crash_never_replays_unconfirmed_work(tmp_path):
    commands = Commands(tmp_path)
    job = commands.submit("experiment", {"test": "unknown"}, key="unknown")
    context = multiprocessing.get_context("fork")
    first = context.Process(target=fake_manager, args=(tmp_path,))
    first.start()
    eventually(lambda: (tmp_path / "executions.log").exists())
    first.kill()
    first.join(5)
    second = context.Process(target=fake_manager, args=(tmp_path,))
    second.start()
    try:
        eventually(lambda: commands.query(job["id"])["status"] == "interrupted")
        time.sleep(0.15)
        assert len((tmp_path / "executions.log").read_text().splitlines()) == 1
    finally:
        from agent_harness_eval.operations.manager import stop

        stop(tmp_path)
        second.join(5)
        if second.is_alive():
            second.kill()
            second.join()


def test_pause_waits_for_inflight_attempt_and_resume_executes_once(tmp_path, monkeypatch):
    from agent_harness_eval.operations.worker import Admission

    commands = Commands(tmp_path)
    commands.submit("experiment", {"cooperative_pause": True}, key="pause")
    job = commands.claim("owner")
    for name, value in {"ROOT": str(tmp_path), "ID": job["id"], "OWNER": "owner", "GENERATION": str(job["generation"])}.items():
        monkeypatch.setenv("HARNESS_OPERATION_" + name, value)

    async def scenario():
        admission = Admission()
        await admission.enter("first")
        commands.control(job["id"], "pause")
        second = asyncio.create_task(admission.enter("second"))
        await asyncio.sleep(0.1)
        assert commands.query(job["id"])["status"] == "pausing"
        assert not second.done()
        admission.active.remove("first")
        for _ in range(50):
            if commands.query(job["id"])["status"] == "paused":
                break
            await asyncio.sleep(0.02)
        assert commands.query(job["id"])["status"] == "paused"
        commands.control(job["id"], "resume")
        await asyncio.wait_for(second, 2)
        assert admission.active == {"second"}

    asyncio.run(scenario())
