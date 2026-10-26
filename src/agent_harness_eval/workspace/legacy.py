"""Read-only legacy snapshots. Import never schedules old work or fills unknown provenance."""

import hashlib
import json
import sqlite3
from contextlib import ExitStack
from pathlib import Path

from .migrations import backup, exclusive, upgrade
from .models import canonical_json
from .store import Store


def read_database(path, table):
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("Legacy database symlinks are not supported")
    if Path(str(path) + "-wal").exists():
        raise ValueError("Checkpoint the stopped legacy database before importing WAL state")
    # table comes exclusively from internal constants below.
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(f"SELECT * FROM {table}")]


def inventory(root, *, regrading_root=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Legacy workspace does not exist")
    records = []

    def add(key, payload):
        raw = canonical_json(payload)
        records.append({"identity": key, "source_hash": hashlib.sha256(raw.encode()).hexdigest(), "payload": raw})

    for directory, table, filename in [(".console", "runs", "runs.sqlite3"), (".rsi", "actions", "queue.sqlite")]:
        base = root / directory
        if base.is_symlink():
            raise ValueError("Legacy directory symlinks are not supported")
        for row in read_database(base / filename, table):
            if directory == ".console" and isinstance(row.get("config"), str):
                canonical_json(json.loads(row["config"]))
            add(directory + "/" + str(row["id"]), row)
    if regrading_root is not None:
        for row in read_database(Path(regrading_root) / "queue.sqlite3", "jobs"):
            # A user-wide queue may contain unrelated checkouts.
            if Path(row["output"]).resolve().is_relative_to(root):
                add("regrading/" + row["id"], row)
    for pattern in ("artifacts/*/manifest.json", ".rsi/cases/*/record.json", ".rsi/candidates/*/record.json"):
        for path in sorted(root.glob(pattern)):
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
                raise ValueError("Legacy evidence symlinks are not supported")
            value = json.loads(path.read_text(encoding="utf-8"))
            add(path.relative_to(root).as_posix(), {
                "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_revision": None, "value": value,
            })
    return records


def import_legacy(root, *, regrading_root=None, dry_run=True, before_insert=None):
    root = Path(root).resolve()
    # Preview has no Store construction, directory creation, locks or write connections.
    if dry_run:
        schema = upgrade(root / ".workspace/state.sqlite", dry_run=True)
        records = inventory(root, regrading_root=regrading_root)
        from ..freezing import plan_from_manifest

        for record in records:
            if record["identity"].endswith("manifest.json"):
                value = json.loads(record["payload"])
                canonical_json(plan_from_manifest(value["value"], evidence=(value["path"],)))
        return {"dry_run": True, "count": len(records), "identities": [r["identity"] for r in records],
                "schema": schema,
                "index_payload_bytes": sum(len(r["payload"].encode()) for r in records),
                "unknown_provenance": sum(r["identity"].endswith("manifest.json") for r in records)}
    with ExitStack() as stack:
        # Use the old owner's exact lock, not a new lock it cannot see.
        stack.enter_context(exclusive(root / ".console/server.lock"))
        stack.enter_context(exclusive(root / ".workspace/manager.lock"))
        for output in sorted((root / "artifacts").glob("*")):
            if output.is_dir() and not output.is_symlink():
                stack.enter_context(exclusive(output.parent / ("." + output.name + ".run.lock")))
        for candidate in sorted((root / ".rsi/candidates").glob("*")):
            if candidate.is_dir() and not candidate.is_symlink():
                stack.enter_context(exclusive(candidate / "operation.lock"))
        if regrading_root is not None:
            stack.enter_context(exclusive(Path(regrading_root) / "batches.lock"))
        records = inventory(root, regrading_root=regrading_root)
        for record in records:
            row = json.loads(record["payload"])
            if row.get("status") == "running":
                raise ValueError("Running legacy work requires reconciliation before import")
        store = Store(root)
        with sqlite3.connect(store.path) as db:
            saved = backup(db, store.directory / "backups")
        imported = 0
        with store.transaction() as db:
            for record in records:
                old = db.execute("SELECT source_hash FROM legacy_records WHERE identity=?", (record["identity"],)).fetchone()
                if old:
                    if old[0] != record["source_hash"]:
                        raise ValueError("Legacy input changed since import: " + record["identity"])
                    continue
                if before_insert is not None:
                    before_insert(record)  # Failure-injection seam; never a CLI-supplied command.
                if record["identity"].endswith("manifest.json"):
                    from ..freezing import plan_from_manifest

                    value = json.loads(record["payload"])
                    plan = plan_from_manifest(value["value"], evidence=(value["path"],))
                    store.insert_plan(db, plan, "legacy:" + record["identity"])
                db.execute("INSERT INTO legacy_records VALUES (:identity,:source_hash,:payload)", record)
                store.event(db, "legacy.imported", record["identity"], {"source_hash": record["source_hash"]})
                imported += 1
        return {"dry_run": False, "count": len(records), "imported": imported, "backup": str(saved)}
