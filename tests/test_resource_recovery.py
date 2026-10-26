import pytest

from agent_harness_eval.operations.resources import Request, Resources
from agent_harness_eval.operations.retention import collect
from test_resource_admission import LIMITS


def test_dead_process_keeps_capacity_until_explicit_release(tmp_path):
    ledger = Resources(tmp_path, limits=LIMITS)
    ledger.acquire(Request("eval", 4, 512), key="dead", owner="run", generation=2)
    with ledger.transaction() as db:
        db.execute("UPDATE capacity SET pid=99999999,start_ticks='1'")
    assert ledger.reconcile() == ["dead"]
    assert ledger.acquire(Request("eval", 1, 512), key="new", owner="other", generation=1)["status"] == "wait"
    ledger.release("dead", owner="run", generation=2)
    assert ledger.acquire(Request("eval", 1, 512), key="new", owner="other", generation=1)["status"] == "granted"


def test_consumers_external_ownership_and_failure_retry(tmp_path):
    ledger = Resources(tmp_path, limits=LIMITS)
    for asset, owned in [("sha256:own", True), ("sha256:external", False)]:
        ledger.attach(asset, kind="image", owner="run", generation=1, consumer="grade", owned=owned, expires=1)
        ledger.attach(asset, kind="image", owner="run", generation=1, consumer="diagnosis", owned=owned, expires=1)
        ledger.detach(asset, consumer="grade", owner="run", generation=1)
    deleted = []
    assert collect(ledger, lambda row: deleted.append(row["id"]) or True, now=10) == []
    for asset in ("sha256:own", "sha256:external"):
        ledger.detach(asset, consumer="diagnosis", owner="run", generation=1)
    def unavailable(row):
        raise OSError("Docker temporarily unavailable")
    assert collect(ledger, unavailable, now=10)[0]["state"] == "cleanup_failed"
    result = collect(ledger, lambda row: deleted.append(row["id"]) or True, now=10)
    assert result[0]["state"] == "deleted"
    assert deleted == ["sha256:own"]
    assert collect(ledger, lambda row: False, now=10) == []
    with pytest.raises(ValueError, match="Stale"):
        ledger.detach("sha256:external", consumer="diagnosis", owner="run", generation=2)


def test_fresh_external_reference_prevents_deletion(tmp_path):
    ledger = Resources(tmp_path, limits=LIMITS)
    ledger.attach("actual-container-id", kind="container", owner="run", generation=1,
                  consumer="execution", owned=True, expires=1)
    ledger.detach("actual-container-id", consumer="execution", owner="run", generation=1)
    assert collect(ledger, lambda row: False, now=10)[0]["state"] == "retained"


def test_periodic_snapshot_expiry_without_new_attempt(tmp_path):
    from agent_harness_eval.guardian.retention import sweep
    from agent_harness_eval.guardian.evidence import write, read

    trial = tmp_path / "jobs/agent/trial"
    trial.mkdir(parents=True)
    archive = trial / "retained-workspace.tar"
    archive.write_bytes(b"diagnostic copy")
    write(trial / "retention.json", {"status": "snapshot_retained", "created_at": "2000-01-01T00:00:00+00:00"})
    original = trial / "result.json"
    original.write_text('{"reward":0}')
    assert sweep(tmp_path, {"retain_hours": 1, "retain_count": 3, "retain_gib": 1}) == [str(archive)]
    assert not archive.exists()
    assert read(trial / "retention.json")["status"] == "snapshot_evicted"
    assert original.read_text() == '{"reward":0}'


def test_actual_container_id_and_consumers_fence_cleanup(tmp_path, monkeypatch):
    from agent_harness_eval import regrading
    from agent_harness_eval.operations.resources import record_container, begin_container_cleanup, finish_container_cleanup

    monkeypatch.setattr(regrading, "state_root", lambda: tmp_path / "grading")
    monkeypatch.setenv("HARNESS_OPERATION_ID", "operation-one")
    monkeypatch.setenv("HARNESS_OPERATION_GENERATION", "3")
    cid = "a" * 64
    record_container(cid, "owned-project")
    ledger = Resources()
    ledger.attach("container:" + cid, kind="container", owner="owned-project", generation=3,
                  consumer="diagnosis", owned=True)
    assert begin_container_cleanup(cid, "owned-project") is False
    ledger.detach("container:" + cid, consumer="diagnosis", owner="owned-project", generation=3)
    assert begin_container_cleanup(cid, "owned-project") is True
    with pytest.raises(ValueError, match="reclaimed"):
        record_container(cid, "owned-project")
    finish_container_cleanup(cid, "owned-project", False)
    assert ledger.snapshot()["assets"][0]["state"] == "cleanup_failed"
    assert collect(ledger, lambda row: True)[0]["state"] == "deleted"


def test_container_cleanup_rejects_another_owner(tmp_path, monkeypatch):
    from agent_harness_eval import regrading
    from agent_harness_eval.operations.resources import record_container, begin_container_cleanup

    monkeypatch.setattr(regrading, "state_root", lambda: tmp_path / "grading")
    monkeypatch.setenv("HARNESS_OPERATION_ID", "one")
    monkeypatch.setenv("HARNESS_OPERATION_GENERATION", "1")
    record_container("b" * 64, "first-project")
    with pytest.raises(ValueError, match="ownership mismatch"):
        begin_container_cleanup("b" * 64, "other-project")
