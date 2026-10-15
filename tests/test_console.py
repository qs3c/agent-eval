import hashlib
import json
import shutil
from pathlib import Path

import pytest

from agent_harness_eval.console import Console
from agent_harness_eval.report import summarize


def test_running_report_refreshes_each_result_without_rewriting_snapshot(console):
    item = console.submit({"template": "controls"})
    console.update(item["id"], "running")
    run = Path(console.row(item["id"])["path"])
    run.mkdir()
    exp = console.detail(item["id"])["experiment"]
    manifest = {"experiment": exp, "tasks": {}, "evidence_kind": "mock"}
    snapshot = json.dumps(summarize(manifest, []))
    (run / "report.json").write_text(snapshot)
    agent = exp["agents"][0]["id"]
    for index in range(2):
        trial = run / "jobs" / agent / str(index)
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(json.dumps({
            "task_name": str(index), "finished_at": "2026-10-14T10:00:00Z",
            "verifier_result": {"rewards": {"reward": 1}},
            "exception_info": {"exception_type": "AgentTimeoutError"} if index else None,
        }))
        detail = console.detail(item["id"])
        summary = detail["report"]["agents"][agent]
        assert detail["completed"] == summary["recorded"] == index + 1
        assert detail["report"]["live"] is True
        assert summary["scored"] == 1
        assert summary["errors"] == index
        assert summary["mean_on_scored"] == 1
        assert summary["missing"] == summary["expected"] - index - 1
        assert (run / "report.json").read_text() == snapshot
    listing = console.runs()[0]
    assert listing["completed"] == 2
    assert listing["execution_summary"]["errors"] == 1


@pytest.fixture
def console(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "tasks", tmp_path / "tasks")
    (tmp_path / "experiments").mkdir()
    shutil.copyfile(
        root / "experiments/controls.json", tmp_path / "experiments/controls.json"
    )
    return Console(tmp_path)


def test_submission_validation_and_snapshot(console):
    with pytest.raises(ValueError):
        console.submit({"template": "controls", "implementation": "os:system"})
    with pytest.raises(ValueError):
        console.submit({"template": "../controls"})
    with pytest.raises(ValueError):
        console.submit({"template": "controls", "agents": []})
    row = console.submit({"template": "controls", "name": "browser-check"})
    assert row["status"] == "queued"
    frozen = console.detail(row["id"])["experiment"]
    assert frozen["name"] == "browser-check"
    assert all(
        Path(t).is_file()
        for t in [str(Path(x) / "task.toml") for x in frozen["benchmark"]["tasks"]]
    )
    assert console.claim()["id"] == row["id"]
    assert console.claim() is None


def test_history_unknowns_and_symlinks(console):
    run = console.root / "artifacts/old"
    run.mkdir(parents=True)
    exp = json.loads((console.root / "experiments/controls.json").read_text())
    (run / "manifest.json").write_text(json.dumps({"experiment": exp, "tasks": {}}))
    console.import_history()
    item = console.runs()[0]
    assert item["status"] == "incomplete"
    assert console.detail(item["id"])["report"] is None
    (run / "report.json").symlink_to(console.root / "experiments/controls.json")
    assert console.detail(item["id"])["report"] is None
    with pytest.raises(KeyError):
        console.detail("../.env")


@pytest.mark.parametrize(
    "case,expected",
    [
        ("real", "running"),
        ("alias", "running"),
        ("equals", "running"),
        ("relative", "running"),
        ("prefix", "interrupted"),
        ("other_argument", "interrupted"),
        ("reused_pid", "interrupted"),
        ("missing_process", "interrupted"),
    ],
)
def test_history_runner_matches_resolved_output(console, monkeypatch, case, expected):
    run = console.artifacts / "active"
    run.mkdir()
    exp = json.loads((console.root / "experiments/controls.json").read_text())
    (run / "manifest.json").write_text(json.dumps({"experiment": exp, "tasks": {}}))
    (run / "batch-state.json").write_text(json.dumps({
        "phase": "running", "runner": {"pid": 12345, "start_ticks": "99"},
    }))
    console.import_history()
    item = console.row("history-" + hashlib.sha256(b"active").hexdigest()[:20])
    alias = console.root / "shared artifacts"
    alias.symlink_to(console.artifacts, target_is_directory=True)
    with console.connect() as db:
        db.execute("UPDATE runs SET path=? WHERE id=?", (str(alias / "active"), item["id"]))

    output = str(alias / "active") if case == "alias" else str(run)
    args = ["python", "-m", "agent_harness_eval.cli", "run", "config.json", "--output", output]
    if case == "equals":
        args[-2:] = ["--output=" + output]
    elif case == "relative":
        args[-1] = "artifacts/active"
    elif case == "prefix":
        args[-1] = str(alias / "active-other")
    elif case == "other_argument":
        args[-2:] = ["--config", str(alias / "active")]
    process = Path("/proc/12345")
    read_text, read_bytes, resolve = Path.read_text, Path.read_bytes, Path.resolve

    def fake_text(path, *args, **kwargs):
        if path == process / "stat":
            if case == "missing_process":
                raise FileNotFoundError(path)
            return "12345 (python) " + " ".join(["0"] * 19 + ["98" if case == "reused_pid" else "99"])
        return read_text(path, *args, **kwargs)

    def fake_bytes(path, *unused, **kwargs):
        if path == process / "cmdline":
            return ("\0".join(args) + "\0").encode()
        return read_bytes(path, *unused, **kwargs)

    def fake_resolve(path, *args, **kwargs):
        if path == process / "cwd":
            return console.root
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_text)
    monkeypatch.setattr(Path, "read_bytes", fake_bytes)
    monkeypatch.setattr(Path, "resolve", fake_resolve)
    assert console.detail(item["id"])["status"] == expected
    assert console.runs()[0]["status"] == expected


@pytest.mark.parametrize(
    "errors,scored,missing_result,status,finished",
    [(1, 1, False, "failed", True), (0, 1, False, "failed", True),
     (0, 2, False, "completed", True), (1, 1, True, "incomplete", False)],
)
def test_execution_summary_separates_ended_errors_and_missing_results(
    console, errors, scored, missing_result, status, finished
):
    run = console.artifacts / "summary"
    run.mkdir()
    exp = json.loads((console.root / "experiments/controls.json").read_text())
    summary = {
        "expected": 2, "recorded": 2, "missing": 0, "errors": errors,
        "scored": scored, "mean_on_scored": 0, "cost_usd": None,
    }
    report = {
        "agents": {a["id"]: dict(summary) for a in exp["agents"]},
        "trials": [], "report_errors": [],
    }
    for agent in exp["agents"]:
        for attempt in range(2 - int(missing_result)):
            trial = run / "jobs" / agent["id"] / str(attempt)
            trial.mkdir(parents=True)
            (trial / "result.json").write_text(json.dumps({
                "task_name": str(attempt), "finished_at": "2026-10-14T00:45:00Z",
                "verifier_result": {"rewards": {"reward": 0}},
            }))
        if missing_result:
            report["agents"][agent["id"]].update(recorded=1, missing=1)
    (run / "manifest.json").write_text(json.dumps({"experiment": exp, "tasks": {}}))
    (run / "report.json").write_text(json.dumps(report))
    item = console.runs()[0]
    assert item["status"] == status
    assert item["execution_summary"] == {
        "finished": finished, "errors": errors * 2, "unscored": 4 - scored * 2,
    }
    assert console.detail(item["id"])["execution_summary"] == item["execution_summary"]


def test_redaction(console, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-long-sensitive-value")
    row = console.submit({"template": "controls"})
    (console.state / "logs" / (row["id"] + ".log")).write_text(
        "test-long-sensitive-value Bearer secret-value sk-abcdefghijklmnopqrstuvwxyz123456"
    )
    text = console.logs(row["id"])
    assert "test-long-sensitive-value" not in text
    assert "secret-value" not in text
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in text


def test_large_live_manifest_is_imported_and_comparable(console):
    from agent_harness_eval.console import read_json, read_manifest

    run = console.artifacts / "swe-live-20"
    run.mkdir()
    exp = json.loads((console.root / "experiments/controls.json").read_text())
    exp["name"] = "swe-live-20"
    exp["model"] = {"mode": "live", "spending": "unbounded"}
    exp["benchmark"]["tasks"] = [f"tasks/task-{i}" for i in range(20)]
    exp["benchmark"]["score_kind"] = "resolved"
    manifest = {"experiment": exp, "created_at": "2026-10-11T16:31:50+00:00",
                "tasks": {"task-0": {"sha256": "frozen-hash"}},
                "provenance": "x" * (17 * 1024 * 1024)}
    path = run / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert read_json(run, path) is None  # Other JSON retains its smaller bound.
    item = next(r for r in console.runs() if r["name"] == "swe-live-20")
    assert item["mode"] == "live" and item["expected"] == 40
    assert console.detail(item["id"])["task_hashes"] == {"task-0": "frozen-hash"}
    comparison = console.comparison(item["id"])
    assert comparison["task_count"] == 20
    assert comparison["score_kind"] == "resolved"
    with path.open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    assert read_manifest(run) is None


def test_singleton_restart_blocks_and_keeps_queue(console):
    first = console.submit({"template": "controls"})
    second = console.submit({"template": "controls"})
    console.claim()  # Simulate an interrupted owner before starting a new service.
    console.start()
    try:
        assert console.detail(first["id"])["status"] == "interrupted"
        assert console.blocked
        assert console.detail(second["id"])["status"] == "queued"
        another = Console(console.root)
        with pytest.raises(RuntimeError):
            another.start()
        with pytest.raises(ValueError):
            console.submit({"template": "controls"})
    finally:
        console.close()


def test_worker_failure_is_recorded(console, monkeypatch):
    import time

    row = console.submit({"template": "controls"})

    def fail(*args, **kwargs):
        raise OSError("test process could not launch")

    monkeypatch.setattr("agent_harness_eval.console.subprocess.Popen", fail)
    console.start()
    try:
        deadline = time.monotonic() + 3
        while (
            console.detail(row["id"])["status"] in ("queued", "running")
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert console.detail(row["id"])["status"] == "failed"
        assert "could not launch" in console.detail(row["id"])["error"]
    finally:
        console.close()


def test_live_gate_and_template_symlink(console):
    original = json.loads((console.root / "experiments/controls.json").read_text())
    original["model"] = {"mode": "live", "spending": "unbounded"}
    (console.root / "experiments/live.json").write_text(json.dumps(original))
    assert (
        next(x for x in console.templates() if x["id"] == "live")["available"] is False
    )
    with pytest.raises(ValueError):
        console.submit({"template": "live", "confirm_unbounded": True})
    (console.root / "experiments/escape.json").symlink_to(
        console.root / "experiments/controls.json"
    )
    with pytest.raises(ValueError):
        console.submit({"template": "escape"})


def test_comparison_requires_content_identity(console):
    for name in ("one", "two"):
        run = console.artifacts / name
        run.mkdir()
        from agent_harness_eval.config import load

        exp = load(console.root / "experiments/controls.json").model_dump(mode="json")
        summary = {
            "expected": 2,
            "recorded": 2,
            "missing": 0,
            "errors": 0,
            "scored": 2,
            "mean_on_scored": 1,
            "cost_usd": None,
        }
        report = {"agents": {"positive": summary, "negative": summary}, "trials": []}
        (run / "manifest.json").write_text(
            json.dumps({"experiment": exp, "tasks": {"a": {"sha256": "same"}}})
        )
        (run / "report.json").write_text(json.dumps(report))
    console.import_history()
    ids = [r["id"] for r in console.runs()]
    assert console.compare(*ids)["compatible"] is True
    path = console.artifacts / "two" / "manifest.json"
    datum = json.loads(path.read_text())
    datum["tasks"]["a"]["sha256"] = "different"
    path.write_text(json.dumps(datum))
    assert console.compare(*ids)["compatible"] is False


def test_live_queue_rechecks_current_service_policy(console):
    row = console.submit({"template": "controls"})
    config = console.detail(row["id"])["experiment"]
    config["model"]["mode"] = "live"
    with console.connect() as db:
        db.execute(
            "UPDATE runs SET config=? WHERE id=?", (json.dumps(config), row["id"])
        )
    with pytest.raises(ValueError, match="当前服务未开启"):
        console.execute(console.row(row["id"]))


def test_atomic_claim_with_two_connections(console):
    from concurrent.futures import ThreadPoolExecutor

    console.submit({"template": "controls"})
    console.submit({"template": "controls"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: console.claim(), range(2)))
    assert sum(row is not None for row in claimed) == 1


def test_shutdown_terminates_owned_process_group(console, monkeypatch):
    import subprocess
    import sys
    import time

    row = console.submit({"template": "controls"})
    original = subprocess.Popen

    def sleeper(args, **kwargs):
        return original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr("agent_harness_eval.console.subprocess.Popen", sleeper)
    console.start()
    process = None
    try:
        deadline = time.monotonic() + 3
        while console.process is None and time.monotonic() < deadline:
            time.sleep(0.02)
        process = console.process
        assert process is not None
    finally:
        console.close()
    assert process.poll() is not None
    assert console.detail(row["id"])["status"] == "interrupted"


def test_corrupt_nested_report_is_unavailable(console):
    row = console.submit({"template": "controls"})
    run = Path(console.row(row["id"])["path"])
    run.mkdir()
    (run / "report.json").write_text(
        json.dumps({"agents": {"broken": None}, "trials": []})
    )
    assert console.detail(row["id"])["report"] is None


def test_dataset_identity_is_frozen_and_checked(console):
    template = console.root / "experiments/controls.json"
    value = json.loads(template.read_text())
    value["benchmark"].update(
        score_kind="resolved",
        dataset={
            "name": "SWE-bench/SWE-bench_Verified",
            "revision": "abc",
            "split": "test",
        },
        evaluation={
            "name": "harness",
            "version": "1",
            "environment": "image@sha256:abc",
        },
    )
    template.write_text(json.dumps(value))
    first = console.submit({"template": "controls"})
    value["benchmark"]["dataset"]["revision"] = "def"
    template.write_text(json.dumps(value))
    second = console.submit({"template": "controls"})
    data = console.comparison(first["id"])
    assert data["benchmark"]["dataset"]["revision"] == "abc"
    assert data["score_kind"] == "resolved"
    assert data["agents"]["positive"]["score"] is None
    assert data["agents"]["positive"]["missing"] == 2
    assert (
        "数据集版本 / split 不一致"
        in console.compare(first["id"], second["id"])["differences"]
    )
    value["benchmark"]["dataset"]["revision"] = None
    template.write_text(json.dumps(value))
    third = console.submit({"template": "controls"})
    assert (
        "数据集修订或 split 未记录"
        in console.compare(third["id"], third["id"])["differences"]
    )


def test_comparison_reads_finished_records_and_scopes_traces(console):
    item = console.submit({"template": "controls"})
    root = Path(console.row(item["id"])["path"])
    task = Path(item["experiment"]["benchmark"]["tasks"][0]).name
    result = root / "jobs/positive/trial-one/result.json"
    result.parent.mkdir(parents=True)
    result.write_text(
        json.dumps(
            {
                "task_name": "display-name",
                "task_id": {"path": "/tasks/" + task},
                "finished_at": "2026-10-11T00:00:01Z",
                "verifier_result": {"rewards": {"reward": 1}},
                "agent_result": {"n_input_tokens": 100},
            }
        )
    )
    data = console.comparison(item["id"])
    assert data["agents"]["positive"]["missing"] == 1
    assert data["agents"]["negative"]["missing"] == 2
    assert data["tasks"][task]["positive"]["score"] == 1
    from agent_harness_eval.trace_view import trace_list

    traces = trace_list(console, item["id"])
    assert traces[0]["task"] == task
    assert traces[0]["id"] == data["tasks"][task]["positive"]["trials"][0]["id"]
    # Corrupt metric still records an unscored attempt; it is never silently discarded.
    value = json.loads(result.read_text())
    value["verifier_result"]["rewards"] = {"reward": "invalid"}
    result.write_text(json.dumps(value))
    assert console.comparison(item["id"])["agents"]["positive"]["unscored"] == 1


def test_legacy_swe_inference_respects_explicit_metric(console):
    item = console.submit({"template": "controls"})
    root = Path(console.row(item["id"])["path"])
    for task in item["experiment"]["benchmark"]["tasks"]:
        path = root / "tasks" / Path(task).name / "swe-provenance.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"instance_id":"frozen-swe-task"}')
    assert console.comparison(item["id"])["score_kind"] == "resolved"
    (root / "manifest.json").write_text(json.dumps({"experiment": item["experiment"]}))
    assert console.comparison(item["id"])["score_kind"] == "metric"
    assert (
        "历史 SWE 数据集名称 / 修订 / split 未记录"
        in console.compare(item["id"], item["id"])["differences"]
    )


def test_submission_copies_dirty_source_evidence(console):
    path = console.root / "experiments/controls.json"
    config = json.loads(path.read_text())
    source = console.root / "build"
    source.mkdir()
    changes = source / "provenance.source"
    changes.mkdir()
    patch = changes / "changes.patch"
    patch.write_text("frozen source patch")
    provenance = source / "provenance.json"
    provenance.write_text(json.dumps({"changes_directory": changes.name,
                                     "changes": {patch.name: hashlib.sha256(patch.read_bytes()).hexdigest()}}))
    config["agents"][0]["kwargs"] = {"provenance": str(provenance)}
    path.write_text(json.dumps(config))
    row = console.submit({"template": "controls"})
    frozen = Path(row["experiment"]["agents"][0]["kwargs"]["provenance"])
    patch.write_text("subsequently modified source")
    assert (frozen.parent / changes.name / patch.name).read_text() == "frozen source patch"
