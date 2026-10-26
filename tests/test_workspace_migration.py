import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from agent_harness_eval.manifests import index_plan
from agent_harness_eval.workspace.legacy import import_legacy
from agent_harness_eval.workspace.migrations import SCHEMA, exclusive, upgrade
from agent_harness_eval.workspace.store import Store


FIXTURE = Path(__file__).parent / "fixtures/legacy_workspace"


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path / "legacy"
    shutil.copytree(FIXTURE, root)
    for sql, db in [(".console/runs.sql", ".console/runs.sqlite3"), (".rsi/queue.sql", ".rsi/queue.sqlite"),
                    ("host-regrading/queue.sql", "host-regrading/queue.sqlite3")]:
        with sqlite3.connect(root / db) as connection:
            connection.executescript((root / sql).read_text().replace("__WORKSPACE__", str(root)))
    return root


def evidence(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and ".workspace" not in p.relative_to(root).parts
            and p.suffix in {".json", ".sql", ".sqlite", ".sqlite3"}}


def test_preview_creates_nothing_and_apply_is_idempotent(legacy):
    before = evidence(legacy)
    files = {p.relative_to(legacy) for p in legacy.rglob("*")}
    preview = import_legacy(legacy, regrading_root=legacy / "host-regrading")
    assert preview["count"] == 7 and preview["unknown_provenance"] == 3
    assert files == {p.relative_to(legacy) for p in legacy.rglob("*")}
    applied = import_legacy(legacy, regrading_root=legacy / "host-regrading", dry_run=False)
    assert applied["imported"] == preview["count"]
    assert Path(applied["backup"]).is_file()
    assert import_legacy(legacy, regrading_root=legacy / "host-regrading", dry_run=False)["imported"] == 0
    assert evidence(legacy) == before
    store = Store(legacy)
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 3


def test_import_failure_rolls_back_then_retry_succeeds(legacy):
    count = 0
    def fail(record):
        nonlocal count
        count += 1
        if count == 5:
            raise RuntimeError("injected interruption")
    before = evidence(legacy)
    with pytest.raises(RuntimeError):
        import_legacy(legacy, dry_run=False, before_insert=fail)
    store = Store(legacy)
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM legacy_records").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0
    assert evidence(legacy) == before
    assert import_legacy(legacy, dry_run=False)["imported"] == 6


def test_changed_legacy_input_is_not_overwritten(legacy):
    import_legacy(legacy, dry_run=False)
    manifest = legacy / "artifacts/completed/manifest.json"
    data = json.loads(manifest.read_text())
    data["created_at"] = "changed"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="changed"):
        import_legacy(legacy, dry_run=False)


@pytest.mark.parametrize("owner", [".console/server.lock", ".workspace/manager.lock", "artifacts/.completed.run.lock"])
def test_import_refuses_active_legacy_or_new_owner(legacy, owner):
    with exclusive(legacy / owner):
        with pytest.raises(RuntimeError, match="another writer"):
            import_legacy(legacy, dry_run=False)


def test_schema_failure_preserves_previous_schema_and_backup(tmp_path):
    path = tmp_path / "state.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE original (value TEXT)")
        db.execute("INSERT INTO original VALUES ('keep')")
    with pytest.raises(sqlite3.OperationalError):
        upgrade(path, statements=(*SCHEMA, "INVALID SQL"))
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT value FROM original").fetchone()[0] == "keep"
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='plans'").fetchall()
    saved = list((tmp_path / "backups").glob("*.sqlite"))
    assert len(saved) == 1
    with sqlite3.connect(saved[0]) as db:
        assert db.execute("SELECT value FROM original").fetchone()[0] == "keep"


def test_newer_schema_refused_and_dry_run_does_not_create_directory(tmp_path):
    path = tmp_path / "new/state.sqlite"
    assert upgrade(path, dry_run=True)["from"] == 0
    assert not path.parent.exists()
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="newer"):
        upgrade(path)


def test_legacy_plan_index_keeps_unknown_source_and_original_bytes(legacy):
    store = Store(legacy)
    root = legacy / "artifacts/partial-grade"
    before = (root / "manifest.json").read_bytes()
    key = index_plan(root, store, idempotency_key="legacy:partial-grade")
    plan = store.plan(key)
    assert plan.benchmark.task_hashes == {}
    assert all(agent.source is None and agent.provenance == "unknown" for agent in plan.agents)
    assert (root / "manifest.json").read_bytes() == before


def test_preview_rejects_embedded_secrets_without_creating_index(legacy):
    with sqlite3.connect(legacy / ".console/runs.sqlite3") as db:
        db.execute("UPDATE runs SET config=?", (json.dumps({"model": {"api_key": "private"}}),))
    with pytest.raises(ValueError, match="Credentials"):
        import_legacy(legacy)
    assert not (legacy / ".workspace").exists()


def test_old_wal_requires_checkpoint_and_symlink_index_is_rejected(legacy, tmp_path):
    wal = legacy / ".console/runs.sqlite3-wal"
    wal.write_bytes(b"not a valid checkpoint")
    with pytest.raises(ValueError, match="Checkpoint"):
        import_legacy(legacy)
    assert not (legacy / ".workspace").exists()
    wal.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    (legacy / ".workspace").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        Store(legacy)
    assert list(outside.iterdir()) == []
