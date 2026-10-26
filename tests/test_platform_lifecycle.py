"""Fault recovery and fake-volume acceptance, not real benchmark throughput."""
import sqlite3

import pytest

from agent_harness_eval.operations.commands import Commands
from agent_harness_eval.operations.recovery import reconcile
from agent_harness_eval.operations.resources import Resources, Request
from agent_harness_eval.workspace.models import ExperimentPlan
from agent_harness_eval.integrations.sources import Sources
from test_resource_admission import LIMITS


@pytest.mark.parametrize("count", [20, 80, 500])
def test_fake_task_queue_drains_once_with_bounded_resources_and_restart(tmp_path, count):
    commands = Commands(tmp_path)
    plan = commands.store.create_plan(ExperimentPlan(experiment={"name": "fake-volume", "tasks": count}), "frozen")
    jobs = [commands.submit("experiment", {"plan_id": plan, "fixture_task": i}, key="task:" + str(i)) for i in range(count)]
    ledger = Resources(tmp_path / "host", limits=LIMITS)
    executed = set()
    while len(executed) < count:
        batch = []
        # Operations are serial per workspace; task-level concurrency has its own ledger.
        for _ in range(1):
            job = commands.claim("fixture-worker")
            assert job and job["id"] not in executed
            assert ledger.acquire(Request("eval", 1, 512), key=job["id"], owner="fixture-worker", generation=job["generation"])["status"] == "granted"
            batch.append(job)
            assert commands.claim("competing-owner") is None
        assert len(ledger.snapshot()["reservations"]) <= 4
        for job in batch:
            commands.worker_update(job["id"], "fixture-worker", job["generation"], status="completed")
            ledger.release(job["id"], owner="fixture-worker", generation=job["generation"])
            executed.add(job["id"])
        # Reopen persistent scheduling state, as a clean Manager restart does.
        commands = Commands(tmp_path)
        assert reconcile(commands) == []
    assert executed == {j["id"] for j in jobs}
    assert commands.claim("new-worker") is None
    assert ledger.snapshot()["reservations"] == []
    assert commands.store.plan(plan).experiment["tasks"] == count


def test_stopped_workspace_backup_restore_preserves_plans_source_and_unknown_cost(tmp_path):
    commands = Commands(tmp_path)
    plan = commands.store.create_plan(ExperimentPlan(experiment={"name": "original", "cost_usd": None}), "plan")
    first = commands.submit("experiment", {"plan_id": plan}, key="original")
    sources = Sources(tmp_path)
    row = sources.add("https://github.com/fixture/agent")
    sources.observe(row["id"], "a" * 40)
    files = [commands.store.directory / "state.sqlite", sources.path]
    snapshots = []
    for original in files:
        backup = tmp_path / (original.name + ".backup")
        with sqlite3.connect(original) as live, sqlite3.connect(backup) as copied:
            live.backup(copied)
        snapshots.append(backup)
    commands.submit("experiment", {"plan_id": plan}, key="after-backup")
    sources.observe(row["id"], "b" * 40)
    # No Manager or open writers exist during replacement; restore all related DBs.
    for original, backup in zip(files, snapshots):
        with sqlite3.connect(backup) as copied, sqlite3.connect(original) as restored:
            copied.backup(restored)
    assert [j["id"] for j in Commands(tmp_path).list()] == [first["id"]]
    assert Commands(tmp_path).store.plan(plan).experiment["cost_usd"] is None
    assert Sources(tmp_path).get(row["id"])["observed"] == "a" * 40
    assert len(Sources(tmp_path).revisions(row["id"])) == 1


def test_source_acquisition_checks_disk_before_creating_checkout(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent_harness_eval.integrations import checkout
    from agent_harness_eval.workspace.models import SourceRevision

    monkeypatch.setattr(checkout.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match="disk"):
        checkout.fetch_source(SourceRevision(repository="https://github.com/fixture/agent", commit="a" * 40), tmp_path / "checkout", tmp_path / "fetch.log")
    assert not (tmp_path / "checkout").exists()
