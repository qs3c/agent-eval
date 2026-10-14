import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_harness_eval import regrading
from agent_harness_eval.config import BatchConfig, load
from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.provenance import tree_manifest


@pytest.fixture
def grading(tmp_path, monkeypatch):
    from agent_harness_eval.benchmarks import swe

    exp = load(Path("experiments/controls.json"))
    exp.batch = BatchConfig(size=20)
    paths = [tmp_path / "tasks" / name for name in ("one", "two")]
    for task in paths:
        write(task / "tests/config.json", {"instance_id": task.name})

    def export(output, staging):
        staging.mkdir()
        for agent in exp.agents:
            (staging / f"{agent.id}.attempt-1.jsonl").write_text(
                "".join(
                    json.dumps({"instance_id": task.name, "model_patch": "patch"})
                    + "\n"
                    for task in paths
                )
            )

    monkeypatch.setattr(swe, "export", export)
    monkeypatch.setattr(swe, "regrade_command", lambda *args: ["grader"])
    return exp, tmp_path, paths


def report(work):
    instance = read(work / "records.json")[0]["instance_id"]
    write(
        work / "report.json",
        {
            "submitted_ids": [instance],
            "submitted_instances": 1,
            "resolved_instances": 0,
            "error_instances": 0,
            "incomplete_ids": [],
        },
    )


def test_transient_retry_skip_success_and_nontransient_failure(grading, monkeypatch):
    exp, output, paths = grading
    calls = {}
    mutex = threading.Lock()
    delays = []

    def run(command, work, log, inherited):
        key = str(work.parent)
        with mutex:
            calls[key] = calls.get(key, 0) + 1
            count = calls[key]
        log.write_text("ConnectionResetError" if count == 1 else "ok")
        if count == 1:
            return 1
        report(work)
        return 0

    monkeypatch.setattr(regrading, "run_command", run)
    monkeypatch.setattr(regrading.time, "sleep", delays.append)
    regrading.grade_units(exp, output, paths, 1, ())
    progress = read(output / "batches/001/official/progress.json")
    assert progress["completed"] == progress["total"] == 4
    assert set(calls.values()) == {2}
    assert delays == [5] * 4
    regrading.grade_units(exp, output, paths, 1, ())
    assert set(calls.values()) == {2}  # No repeated scoring of successful inputs.
    write(paths[0] / "tests/config.json", {"instance_id": "one", "changed": True})

    def bad(command, work, log, inherited):
        log.write_text("invalid configuration")
        calls["bad"] = calls.get("bad", 0) + 1
        return 1

    monkeypatch.setattr(regrading, "run_command", bad)
    with pytest.raises(RuntimeError, match="2/4"):
        regrading.grade_units(exp, output, paths, 1, ())
    assert calls["bad"] == 2


def test_retries_exhaust_then_manual_run_only_failed_units(grading, monkeypatch):
    exp, output, paths = grading
    calls = []

    def run(command, work, log, inherited):
        calls.append(str(work.parent))
        log.write_text("ConnectionResetError")
        return 1

    monkeypatch.setattr(regrading, "run_command", run)
    monkeypatch.setattr(regrading.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="4/4"):
        regrading.grade_units(exp, output, paths, 1, ())
    assert len(calls) == 12
    assert (
        len(
            list((output / "batches/001/official/units").glob("*/*/*/*/execution.json"))
        )
        == 12
    )
    monkeypatch.setattr(
        regrading, "run_command", lambda command, work, *args: report(work) or 0
    )
    regrading.grade_units(exp, output, paths, 1, ())
    assert read(output / "batches/001/official/progress.json")["completed"] == 4


def test_global_two_slots_across_processes(tmp_path):
    # Six subprocesses compete for the same two advisory locks.
    code = """
import fcntl, json, sys, time
from pathlib import Path
from agent_harness_eval import regrading
root = Path(sys.argv[1])
regrading.state_root = lambda: root
with regrading.slot():
    with (root / 'counter.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        p = root / 'counter.json'
        v = json.loads(p.read_text()) if p.exists() else {'active':0,'max':0}
        v['active'] += 1
        v['max'] = max(v['max'],v['active'])
        p.write_text(json.dumps(v))
    time.sleep(0.15)
    with (root / 'counter.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        v = json.loads(p.read_text()); v['active'] -= 1
        p.write_text(json.dumps(v))
"""
    processes = [
        subprocess.Popen([sys.executable, "-c", code, str(tmp_path)]) for _ in range(6)
    ]
    for process in processes:
        assert process.wait(timeout=10) == 0
    assert read(tmp_path / "counter.json") == {"active": 0, "max": 2}


def test_duplicate_batch_is_one_job_and_other_batches_fifo(grading, monkeypatch):
    from agent_harness_eval import batching

    exp, output, paths = grading
    write(
        output / "manifest.json",
        {
            "experiment": exp.model_dump(mode="json"),
            "tasks": {p.name: tree_manifest(p) for p in paths},
        },
    )
    write(
        output / "batch-state.json",
        {
            "phase": "completed",
            "batches": [
                {"number": n, "tasks": [p.name for p in paths]} for n in (1, 2)
            ],
        },
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: regrading.enqueue(output, 1)["id"], range(20)))
    assert len(set(ids)) == 1
    regrading.enqueue(output, 2)
    events = []
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()

    def grade(exp, output, paths, number, **kwargs):
        events.append(number)
        if number == 1:
            entered.set()
            assert release.wait(timeout=3)
        else:
            stop.set()

    monkeypatch.setattr(batching, "regrade_batch", grade)
    thread = threading.Thread(target=regrading.queue_worker, args=(stop,))
    thread.start()
    assert entered.wait(timeout=3)
    assert regrading.enqueue(output, 1)["id"] == ids[0]
    assert regrading.jobs(output)[2]["status"] == "queued"
    assert events == [1]
    release.set()
    thread.join(timeout=4)
    assert not thread.is_alive()
    assert events == [1, 2]
    assert all(job["status"] == "completed" for job in regrading.jobs(output).values())


def test_restart_recovers_interrupted_job_and_keeps_queued(grading):
    _, output, _ = grading
    first = regrading.enqueue(output, 1)
    with regrading.database() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (first["id"],))
    stop = threading.Event()
    thread = threading.Thread(target=regrading.queue_worker, args=(stop,))
    thread.start()
    deadline = time.monotonic() + 3
    while (
        regrading.jobs(output)[1]["status"] == "running" and time.monotonic() < deadline
    ):
        stop.wait(0.01)
    stop.set()
    thread.join(timeout=2)
    assert regrading.jobs(output)[1]["status"] == "interrupted"
    assert regrading.enqueue(output, 1)["id"] != first["id"]


def test_harness_timeout_kills_child_and_releases_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(regrading, "TIMEOUT", 0.1)
    with pytest.raises(subprocess.TimeoutExpired):
        regrading.run_command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            tmp_path,
            tmp_path / "log",
        )
    with regrading.gate("slot-0", False) as fd:
        assert fd is not None


def test_incomplete_report_is_not_a_success(grading, monkeypatch):
    exp, output, paths = grading

    def incomplete(command, work, log, inherited):
        report(work)
        value = read(work / "report.json")
        value["incomplete_ids"] = value["submitted_ids"]
        write(work / "report.json", value)
        return 0

    monkeypatch.setattr(regrading, "run_command", incomplete)
    with pytest.raises(RuntimeError, match="4/4"):
        regrading.grade_units(exp, output, paths, 1, ())
    assert read(output / "batches/001/official/progress.json")["completed"] == 0


def test_legacy_success_is_reused_only_with_matching_saved_inputs(grading, monkeypatch):
    exp, output, paths = grading
    directory = output / "batches/001/official"
    write(directory / "records.json", [read(p / "tests/config.json") for p in paths])
    for agent in exp.agents:
        saved = directory / "predictions" / f"{agent.id}.attempt-1.jsonl"
        saved.parent.mkdir(exist_ok=True)
        saved.write_text(
            "".join(
                json.dumps({"instance_id": p.name, "model_patch": "patch"}) + "\n"
                for p in paths
            )
        )
        write(
            directory / agent.id / "attempt-1/report.json",
            {
                "submitted_instances": 2,
                "submitted_ids": [p.name for p in paths],
                "resolved_instances": 1,
                "resolved_ids": [paths[0].name],
                "error_instances": 0,
                "incomplete_ids": [],
            },
        )
    monkeypatch.setattr(
        regrading,
        "run_command",
        lambda *args: pytest.fail("Already graded inputs must be skipped"),
    )
    regrading.update_summary(exp, output)
    assert (
        read(output / "official-summary.json")["agents"][exp.agents[0].id]["observed"]
        == 2
    )
    regrading.grade_units(exp, output, paths, 1, ())
    regrading.update_summary(exp, output)
    summary = read(output / "official-summary.json")["agents"][exp.agents[0].id]
    assert summary["observed"] == 2
    assert summary["resolved"] == 1
