"""Durable batch queue and two host-wide official grading slots (same Unix user)."""

import fcntl
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from .guardian.evidence import read, write
from .manifests import load_manifest
from .provenance import digest, tree_manifest, validate_tree

EVALUATOR = "swebench==4.1.0"
TIMEOUT = 1800
TRANSIENT = (
    "ConnectionResetError",
    "ConnectionError",
    "ReadTimeout",
    "ConnectTimeout",
    "Temporary failure in name resolution",
    "HTTP Error 429",
    "HTTP Error 502",
    "HTTP Error 503",
    "HTTP Error 504",
)


def state_root():
    root = Path.home() / ".cache/agent-harness-eval/regrading"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


@contextmanager
def gate(name, blocking=True, *, shared=False):
    with (state_root() / (name + ".lock")).open("a") as stream:
        try:
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(stream, mode | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield None
            return
        try:
            yield stream.fileno()
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def slot():
    while True:
        for number in range(2):
            with gate(f"slot-{number}", False) as fd:
                if fd is not None:
                    yield fd
                    return
        time.sleep(0.1)


def run_command(command, work, log_path, inherited=()):
    """Kill the whole grading process group on timeout; children hold capacity locks."""
    with gate("image-use", shared=True) as image_fd, slot() as fd, log_path.open("w") as log:
        process = subprocess.Popen(
            command,
            cwd=work,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=(*inherited, fd, image_fd),
        )
        try:
            return process.wait(timeout=TIMEOUT)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


def valid_report(work, ids):
    reports = []
    for path in work.glob("*.json"):
        value = read(path, {})
        if isinstance(value, dict) and "submitted_instances" in value:
            reports.append((path, value))
    if len(reports) != 1:
        raise ValueError("官方报告缺失或数量异常")
    path, value = reports[0]
    if (
        set(value.get("submitted_ids", [])) != ids
        or value.get("submitted_instances") != len(ids)
        or value.get("error_instances", 0)
        or value.get("incomplete_ids")
        or not isinstance(value.get("resolved_instances"), int)
        or not 0 <= value["resolved_instances"] <= len(ids)
    ):
        raise ValueError("官方报告覆盖不完整或存在评分异常")
    return path, value


def import_legacy(directory, agent, attempt, records, predictions, output):
    """Reuse a complete old batch report only when its saved inputs still match."""
    try:
        saved_records = read(directory / "records.json", [])
        if {r["instance_id"]: r for r in saved_records} != records:
            return {}
        saved = directory / "predictions" / f"{agent}.attempt-{attempt}.jsonl"
        rows = {
            r["instance_id"]: r
            for r in (json.loads(line) for line in saved.read_text().splitlines())
        }
        if any(rows.get(key) != predictions[key] for key in records):
            return {}
        path, report = valid_report(
            directory / agent / f"attempt-{attempt}", set(records)
        )
        resolved = report["resolved_ids"]
        if len(set(resolved)) != report["resolved_instances"] or not set(
            resolved
        ).issubset(records):
            return {}
        return {
            "path": str(path.relative_to(output)),
            "sha256": digest(path),
            "resolved": resolved,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def grade_units(exp, output, paths, number, inherited, source="automatic"):
    from .benchmarks.swe import export, regrade_command

    directory = output / "batches" / f"{number:03d}" / "official"
    directory.mkdir(parents=True, exist_ok=True)
    staging = directory / ("export-" + uuid.uuid4().hex)
    export(output, staging)
    records = {task.name: read(task / "tests/config.json") for task in paths}
    if any(
        not record or record.get("instance_id") != key
        for key, record in records.items()
    ):
        raise ValueError("冻结任务记录缺失或题目 ID 不一致")
    progress_path = directory / "progress.json"
    units = {}
    mutex = threading.Lock()

    def persist():
        write(
            progress_path,
            {
                "evaluator": EVALUATOR,
                "units": units,
                "total": len(units),
                "completed": sum(u["status"] == "completed" for u in units.values()),
                "updated_unix": time.time(),
            },
        )

    inputs = []
    for agent in exp.agents:
        for attempt in range(1, exp.attempts + 1):
            prediction_file = staging / f"{agent.id}.attempt-{attempt}.jsonl"
            predictions = {
                row["instance_id"]: row
                for row in (
                    json.loads(line)
                    for line in prediction_file.read_text().splitlines()
                )
            }
            if not set(records).issubset(predictions):
                raise ValueError("补丁未覆盖本批全部题目")
            legacy = import_legacy(
                directory, agent.id, attempt, records, predictions, output
            )
            for task, record in records.items():
                key = f"{agent.id}/attempt-{attempt}/{task}"
                fingerprint = hashlib.sha256(
                    json.dumps(
                        [record, predictions[task], EVALUATOR], sort_keys=True
                    ).encode()
                ).hexdigest()
                previous = read(directory / "units" / key / "status.json", {})
                cached = False
                if (
                    previous.get("input_sha256") == fingerprint
                    and previous.get("status") == "completed"
                ):
                    try:
                        report_path = output / previous["report"]
                        if report_path.resolve().is_relative_to(
                            output.resolve()
                        ) and digest(report_path) == previous.get("report_sha256"):
                            valid_report(report_path.parent, {task})
                            cached = True
                    except (KeyError, ValueError, OSError):
                        pass
                if not cached and legacy:
                    report_path = (
                        directory
                        / "units"
                        / key
                        / ("import-" + uuid.uuid4().hex)
                        / "report.json"
                    )
                    write(
                        report_path,
                        {
                            "submitted_instances": 1,
                            "submitted_ids": [task],
                            "resolved_instances": int(task in legacy["resolved"]),
                            "error_instances": 0,
                            "incomplete_ids": [],
                            "source_report": legacy["path"],
                            "source_sha256": legacy["sha256"],
                        },
                    )
                    previous = {
                        "status": "completed",
                        "task": task,
                        "agent": agent.id,
                        "attempt": attempt,
                        "input_sha256": fingerprint,
                        "report": str(report_path.relative_to(output)),
                        "report_sha256": digest(report_path),
                    }
                    write(directory / "units" / key / "status.json", previous)
                    cached = True
                units[key] = (
                    previous
                    if cached
                    else {
                        "status": "pending",
                        "task": task,
                        "agent": agent.id,
                        "attempt": attempt,
                        "input_sha256": fingerprint,
                    }
                )
                if not cached:
                    inputs.append((key, record, predictions[task]))
    provenance = read(staging / "export-manifest.json", {})
    write(
        directory / ("export-evidence-" + uuid.uuid4().hex + ".json"),
        {
            "source": source,
            "created_unix": time.time(),
            "evaluator": EVALUATOR,
            "source_manifest_sha256": digest(output / "manifest.json")
            if (output / "manifest.json").exists()
            else None,
            "trials": [
                t for t in provenance.get("trials", []) if t["instance_id"] in records
            ],
            "inputs": {key: unit["input_sha256"] for key, unit in units.items()},
        },
    )
    shutil.rmtree(staging)
    persist()

    def grade(item):
        key, record, prediction = item
        status = units[key]
        base = directory / "units" / key
        for retry in range(1, 4):
            work = base / uuid.uuid4().hex
            work.mkdir(parents=True)
            write(work / "records.json", [record])
            (work / "predictions.jsonl").write_text(json.dumps(prediction) + "\n")
            with mutex:
                status.update(
                    status="running",
                    retry=retry,
                    log=str((work / "console.log").relative_to(output)),
                )
                persist()
            error = None
            transient = False
            try:
                code = run_command(
                    regrade_command(
                        work / "predictions.jsonl",
                        str((work / "records.json").resolve()),
                        "regrade-" + work.name,
                    ),
                    work,
                    work / "console.log",
                    inherited,
                )
                if code:
                    tail = (work / "console.log").read_text(errors="replace")[-65536:]
                    transient = any(marker in tail for marker in TRANSIENT)
                    raise RuntimeError(f"官方复评进程退出，退出码 {code}")
                report_path, _ = valid_report(work, {record["instance_id"]})
            except subprocess.TimeoutExpired:
                transient, error = True, "官方复评超过 1800 秒期限"
            except Exception as exc:  # noqa: BLE001 - persist independent unit failures
                error = str(exc)
            with mutex:
                status.update(
                    status="completed" if error is None else "failed", error=error
                )
                if error is None:
                    status["report"] = str(report_path.relative_to(output))
                    status["report_sha256"] = digest(report_path)
                write(
                    work / "execution.json",
                    {
                        **status,
                        "source": source,
                        "evaluator": EVALUATOR,
                        "finished_unix": time.time(),
                    },
                )
                write(base / "status.json", status)
                persist()
            if error is None or not transient or retry == 3:
                return
            with mutex:
                status["status"] = "retrying"
                persist()
            time.sleep((5, 15)[retry - 1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(grade, inputs))
    errors = [u for u in units.values() if u["status"] != "completed"]
    write(
        directory / "status.json",
        {
            "finished_unix": time.time(),
            "errors": errors,
            "tasks": sorted(records),
            "evaluator": EVALUATOR,
        },
    )
    if errors:
        raise RuntimeError(
            f"官方复评未完成：{len(errors)}/{len(units)} 项；请点击复评按钮重试"
        )


@contextmanager
def database():
    db = sqlite3.connect(state_root() / "queue.sqlite3", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, output TEXT NOT NULL, batch INTEGER NOT NULL,
        status TEXT NOT NULL, created REAL NOT NULL, error TEXT)""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS active_batch ON jobs(output,batch)
        WHERE status IN ('queued','running')""")
    try:
        with db:
            yield db
    finally:
        db.close()


def enqueue(output, number):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute(
            "SELECT * FROM jobs WHERE output=? AND batch=? AND status IN ('queued','running')",
            (str(output.resolve()), number),
        ).fetchone()
        if previous:
            return dict(previous)
        job = {
            "id": uuid.uuid4().hex,
            "output": str(output.resolve()),
            "batch": number,
            "status": "queued",
            "created": time.time(),
            "error": None,
        }
        db.execute(
            "INSERT INTO jobs VALUES (:id,:output,:batch,:status,:created,:error)", job
        )
    return job


def jobs(output):
    with database() as db:
        rows = db.execute(
            "SELECT * FROM jobs WHERE output=? ORDER BY created DESC",
            (str(output.resolve()),),
        ).fetchall()
    latest = {}
    for row in rows:
        latest.setdefault(row["batch"], dict(row))
    return latest


def queue_worker(stop):
    while not stop.is_set():
        with gate("batches", False) as fd:
            if fd is not None:
                with database() as db:
                    # The inherited batch lock prevents recovery while an old harness is alive.
                    db.execute(
                        "UPDATE jobs SET status='interrupted',error='复评执行中断，请重新点击复评' WHERE status='running'"
                    )
                    row = db.execute(
                        "SELECT * FROM jobs WHERE status='queued' ORDER BY created,rowid LIMIT 1"
                    ).fetchone()
                if row:
                    output = Path(row["output"])
                    state = read(output / "batch-state.json", {})
                    if not state:
                        with database() as db:
                            db.execute(
                                "UPDATE jobs SET status='failed',error='实验批次记录不存在，请检查原始证据' WHERE id=?",
                                (row["id"],),
                            )
                        continue
                    if state.get("phase") not in {"completed", "failed", "interrupted"}:
                        stop.wait(0.5)
                        continue
                    with database() as db:
                        db.execute(
                            "UPDATE jobs SET status='running' WHERE id=?", (row["id"],)
                        )
                    error = None
                    try:
                        from .batching import cleanup_batch, regrade_batch
                        from .image_lifecycle import retain
                        from .config import Experiment

                        manifest = load_manifest(output)
                        exp = Experiment.model_validate(manifest["experiment"])
                        batch = next(
                            b for b in state["batches"] if b["number"] == row["batch"]
                        )
                        paths = [output / "tasks" / name for name in batch["tasks"]]
                        cache = {}
                        for task in paths:
                            if task.is_symlink() or not task.resolve().is_relative_to(
                                output.resolve()
                            ):
                                raise ValueError("冻结任务路径越界")
                            validate_tree(task)
                            if (
                                tree_manifest(task, hash_cache=cache)["sha256"]
                                != manifest["tasks"][task.name]["sha256"]
                            ):
                                raise ValueError(
                                    "冻结任务内容已改变，拒绝复评：" + task.name
                                )
                        retain(output, paths, row["batch"])
                        regrade_batch(
                            exp,
                            output,
                            paths,
                            row["batch"],
                            inherited=(fd,),
                            force=True,
                        )
                        batch["official_regrade"] = {
                            "status": "completed", "updated_unix": time.time()
                        }
                        write(output / "batch-state.json", state)
                        cleanup_batch(exp, output, paths, row["batch"])
                    except Exception as exc:  # noqa: BLE001 - persist job failure
                        error = str(exc)
                    with database() as db:
                        db.execute(
                            "UPDATE jobs SET status=?,error=? WHERE id=?",
                            ("failed" if error else "completed", error, row["id"]),
                        )
        stop.wait(0.5)


def update_summary(exp, output):
    expected = len(exp.benchmark.tasks) * exp.attempts
    agents = {}
    for agent in exp.agents:
        reports = []
        resolved = 0
        for progress_path in (output / "batches").glob("*/official/progress.json"):
            for unit in read(progress_path, {}).get("units", {}).values():
                if unit.get("agent") != agent.id or unit.get("status") != "completed":
                    continue
                path = output / unit["report"]
                _, value = valid_report(path.parent, {unit["task"]})
                reports.append(value.get("source_report", unit["report"]))
                resolved += value["resolved_instances"]
        unit_observed = len(reports)
        legacy_observed = 0
        for directory in (output / "batches").glob("*/official"):
            if (directory / "progress.json").exists():
                continue
            records = read(directory / "records.json", [])
            ids = {r["instance_id"] for r in records}
            for attempt in range(1, exp.attempts + 1):
                try:
                    path, value = valid_report(
                        directory / agent.id / f"attempt-{attempt}", ids
                    )
                except (ValueError, OSError):
                    continue
                legacy_observed += value["submitted_instances"]
                resolved += value["resolved_instances"]
                reports.append(str(path.relative_to(output)))
        observed = unit_observed + legacy_observed
        agents[agent.id] = {
            "observed": observed,
            "resolved": resolved,
            "complete": observed == expected,
            "rate_on_observed": resolved / observed if observed else None,
            "raw_reports": sorted(set(reports)),
        }
    write(
        output / "official-summary.json",
        {"expected_per_agent": expected, "agents": agents},
    )
