"""Transactional schema upgrades with a backup made before any DDL."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


VERSION = 1
SCHEMA = (
    "CREATE TABLE plans (id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL)",
    "CREATE TABLE requests (key TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id))",
    "CREATE TABLE records (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(kind,id))",
    "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, entity TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE legacy_records (identity TEXT PRIMARY KEY, source_hash TEXT NOT NULL, payload TEXT NOT NULL)",
    "CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', generation INTEGER NOT NULL DEFAULT 0, owner TEXT, heartbeat REAL, pid INTEGER, start_ticks TEXT, error TEXT, created REAL NOT NULL)",
)


@contextmanager
def exclusive(path):
    import fcntl

    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Workspace lock must not traverse symlinks")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Workspace is owned by another writer") from None
        yield lock


def backup(db, directory):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex + ".sqlite")
    with sqlite3.connect(target) as copy:
        db.backup(copy)
    return target


def upgrade(path, *, dry_run=False, statements=SCHEMA):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Workspace database must not be a symlink")
    if not dry_run and path.exists():
        preview = upgrade(path, dry_run=True, statements=statements)
        if preview["from"] == VERSION:
            return preview
    if dry_run and not path.exists():
        return {"from": 0, "to": VERSION, "changes": list(statements), "backup": None}
    if dry_run:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > VERSION:
            raise ValueError("Workspace schema is newer than this program")
        return {"from": version, "to": VERSION, "changes": list(statements) if version < VERSION else [], "backup": None}
    with exclusive(path.parent / "schema.lock"):
        with sqlite3.connect(path) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > VERSION:
                raise ValueError("Workspace schema is newer than this program")
            if version == VERSION:
                return {"from": version, "to": VERSION, "changes": [], "backup": None}
            saved = backup(db, path.parent / "backups")
            try:
                db.execute("BEGIN IMMEDIATE")
                for statement in statements:
                    db.execute(statement)
                db.execute(f"PRAGMA user_version={VERSION}")
                db.commit()
            except BaseException:
                db.rollback()  # SQLite rolls back DDL; never replace a live DB file.
                raise
    return {"from": version, "to": VERSION, "changes": list(statements), "backup": str(saved)}
