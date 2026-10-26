import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from agent_harness_eval.console import Console
from agent_harness_eval.manifests import load_manifest
from agent_harness_eval.provenance import digest
from agent_harness_eval.rsi.store import Store
from agent_harness_eval.run import resume_configuration, verify_workbench_repair


FIXTURE = Path(__file__).parent / "fixtures/legacy_workspace"


def snapshot(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob("*"))
            if p.is_file() and p.name not in {"hashes.json", "README.md"}}


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    root = tmp_path / "legacy"
    shutil.copytree(FIXTURE, root)
    for script, database in [(".console/runs.sql", ".console/runs.sqlite3"),
                             (".rsi/queue.sql", ".rsi/queue.sqlite"),
                             ("host-regrading/queue.sql", "host-regrading/queue.sqlite3")]:
        with sqlite3.connect(root / database) as db:
            db.executescript((root / script).read_text().replace("__WORKSPACE__", str(root).replace("'", "''")))
    from agent_harness_eval import regrading

    monkeypatch.setattr(regrading, "state_root", lambda: root / "host-regrading")
    return root


def test_fixture_inventory_is_complete_and_untampered():
    assert snapshot(FIXTURE) == json.loads((FIXTURE / "hashes.json").read_text())


def test_history_import_is_idempotent_and_preserves_evidence(legacy):
    before = snapshot(legacy / "artifacts")
    original = snapshot(FIXTURE)
    console = Console(legacy)
    console.import_history()
    first = {row["id"] for row in console.runs()}
    console.import_history()
    assert {row["id"] for row in console.runs()} == first
    assert len(first) == 3
    statuses = {row["directory"]: row["status"] for row in console.runs()}
    assert statuses["completed"] == "completed"
    assert statuses["interrupted"] == "interrupted"
    assert snapshot(legacy / "artifacts") == before
    assert snapshot(FIXTURE) == original


def test_missing_provenance_and_cost_stay_unknown(legacy):
    console = Console(legacy)
    console.import_history()
    identity = "history-" + hashlib.sha256(b"partial-grade").hexdigest()[:20]
    detail = console.detail(identity)
    assert detail["task_hashes"] == {}
    assert detail["report"]["agents"]["positive"]["cost_usd"] is None
    assert "workbench" not in load_manifest(legacy / "artifacts/partial-grade")
    comparison = console.compare("legacy-completed", identity)
    assert not comparison["compatible"]
    assert "任务内容哈希 不一致或缺失" in comparison["differences"]
    official = json.loads((legacy / "artifacts/partial-grade/official-summary.json").read_text())
    assert official["agents"]["positive"]["complete"] is False
    assert official["agents"]["negative"]["rate_on_observed"] is None


def test_old_rsi_and_regrading_state_remain_readable(legacy):
    from agent_harness_eval import regrading

    before = snapshot(legacy / ".rsi/cases")
    store = Store(legacy)
    assert store.get("cases", "1" * 32)["phase"] == "created"
    assert store.actions()[0]["status"] == "interrupted"
    jobs = regrading.jobs(legacy / "artifacts/partial-grade")
    assert jobs[1]["status"] == "failed"
    assert snapshot(legacy / ".rsi/cases") == before


def test_repair_and_set_order_compatibility(legacy, tmp_path):
    path = legacy / "recovery/repair.json"
    record = json.loads(path.read_text())
    assert verify_workbench_repair(record["previous"], record["current"], path) == record
    with pytest.raises(ValueError):
        verify_workbench_repair(record["previous"], record["current"], None)
    exp = load_manifest(legacy / "artifacts/completed")["experiment"]
    reordered = json.loads(json.dumps(exp))
    reordered["benchmark"]["requires"].reverse()
    for agent in reordered["agents"]:
        agent["capabilities"].reverse()
    assert resume_configuration(exp) == resume_configuration(reordered)
    reordered["benchmark"]["tasks"].reverse()
    assert resume_configuration(exp) != resume_configuration(reordered)
    disallowed = {**record["current"], "agents/bkcrab.py": "changed"}
    forged = tmp_path / "repair.json"
    forged.write_text(json.dumps({**record, "current": disallowed}))
    with pytest.raises(ValueError, match="non-infrastructure"):
        verify_workbench_repair(record["previous"], disallowed, forged)
