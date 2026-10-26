from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_harness_eval.operations.resources import Request, Resources


LIMITS = {"cpus": 4, "memory_mb": 4096, "disk_bytes": 10000,
          "lanes": {"eval": 4, "grade": 2, "build": 1, "platform-agent": 1}}


def test_cross_workspace_capacity_and_idempotency(tmp_path):
    a, b = Resources(tmp_path, limits=LIMITS), Resources(tmp_path)
    request = Request("eval", 2, 1024)
    assert a.acquire(request, key="a", owner="workspace-a", generation=1)["status"] == "granted"
    assert a.acquire(request, key="a", owner="workspace-a", generation=1)["status"] == "granted"
    assert b.acquire(request, key="b", owner="workspace-b", generation=1)["status"] == "granted"
    assert b.acquire(request, key="c", owner="workspace-b", generation=1)["status"] == "wait"
    with pytest.raises(ValueError, match="Stale"):
        a.release("a", owner="workspace-a", generation=2)
    a.release("a", owner="workspace-a", generation=1)
    a.release("a", owner="workspace-a", generation=1)
    assert b.acquire(request, key="c", owner="workspace-b", generation=1)["status"] == "granted"
    with pytest.raises(ValueError, match="identity"):
        b.acquire(request, key="b", owner="different", generation=1)


def test_reject_impossible_request_and_wait_for_disk(tmp_path):
    ledger = Resources(tmp_path, limits=LIMITS)
    assert ledger.acquire(Request("eval", 5, 100), key="large", owner="a", generation=1)["status"] == "rejected"
    assert ledger.acquire(Request("build", 1, 100, 50), key="disk", owner="a", generation=1,
                          free_bytes=60, headroom_bytes=20)["reason"] == "disk_headroom"
    with pytest.raises(ValueError, match="share one budget"):
        Resources(tmp_path, limits={**LIMITS, "cpus": 100})


@pytest.mark.parametrize("count", [20, 80, 500])
def test_concurrent_admission_is_bounded_for_large_task_sets(tmp_path, count):
    ledger = Resources(tmp_path, limits=LIMITS)
    def submit(index):
        return ledger.acquire(Request("eval", 1, 512), key=str(index), owner="run", generation=1)
    with ThreadPoolExecutor(12) as pool:
        decisions = list(pool.map(submit, range(count)))
    assert sum(d["status"] == "granted" for d in decisions) == 4
    assert sum(d["status"] == "wait" for d in decisions) == count - 4
    with ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM capacity").fetchone()[0] == 4


def test_lanes_share_cpu_and_have_independent_concurrency_limits(tmp_path):
    ledger = Resources(tmp_path, limits=LIMITS)
    for i in range(2):
        assert ledger.acquire(Request("grade", 1, 512), key=f"g{i}", owner="grader", generation=1)["status"] == "granted"
    assert ledger.acquire(Request("grade", 1, 512), key="g3", owner="grader", generation=1)["status"] == "wait"
    assert ledger.acquire(Request("build", 2, 512), key="build", owner="builder", generation=1)["status"] == "granted"
    assert ledger.acquire(Request("eval", 1, 512), key="eval", owner="run", generation=1)["status"] == "wait"


def test_500_tasks_stop_preparation_when_grading_backlog_is_full(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from agent_harness_eval import batching
    from agent_harness_eval.config import BatchConfig
    from agent_harness_eval.guardian.evidence import write, read

    write(tmp_path / "batch-state.json", {"batches": [
        {"number": 1, "phase": "completed", "official_regrade": {"status": "failed"}},
        {"number": 2, "phase": "completed", "official_regrade": {"status": "failed"}}]})
    prepared = []
    monkeypatch.setattr(batching, "prepare_batch", lambda *args: prepared.append(args))
    from agent_harness_eval.config import load
    from pathlib import Path

    exp = SimpleNamespace(batch=BatchConfig(size=1, max_pending_grade_batches=2), agents=[],
                          benchmark=load(Path("experiments/controls.json")).benchmark)
    tasks = [SimpleNamespace(path=tmp_path / str(i)) for i in range(500)]
    with pytest.raises(ValueError, match="评分积压"):
        asyncio.run(batching.run_batches(exp, tmp_path, tasks, {}, resume=True))
    assert prepared == []
    state = read(tmp_path / "batch-state.json")
    assert len(state["batches"]) == 2
    assert state["phase"] == "failed"


def test_disk_estimate_blocks_preparation_before_download(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from agent_harness_eval import batching
    from agent_harness_eval.config import BatchConfig, load

    exp = load(Path("experiments/controls.json"))
    exp.batch = BatchConfig(prepare_swe=True, min_free_gib=20, estimated_task_gib=2)
    monkeypatch.setattr(batching.shutil, "disk_usage", lambda _: SimpleNamespace(free=21 * 1024**3))
    prepared = []
    monkeypatch.setattr(batching, "_prepare_batch", lambda *args: prepared.append(args))
    with pytest.raises(ValueError, match="disk headroom"):
        batching.prepare_batch(exp, tmp_path, [tmp_path / "task"], 1)
    assert prepared == []


@pytest.mark.parametrize("cpus,memory,disk", [(float("nan"), 1, 0), (1, float("nan"), 0), (1, 1, -1), (True, 1, 0)])
def test_invalid_resource_dimensions_are_rejected(cpus, memory, disk):
    with pytest.raises(ValueError):
        Request("eval", cpus, memory, disk)
