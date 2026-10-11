import json
import shutil
from pathlib import Path

import pytest

from agent_eval.console import Console


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

    monkeypatch.setattr("agent_eval.console.subprocess.Popen", fail)
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
        from agent_eval.config import load

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

    monkeypatch.setattr("agent_eval.console.subprocess.Popen", sleeper)
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
