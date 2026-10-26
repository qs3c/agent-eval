"""Shared per-host admission ledger, separate from workspace experiment history."""

import json
import math
import os
import sqlite3
import time
from uuid import uuid4
from contextlib import contextmanager, asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from .recovery import start_ticks

LANES = {"eval", "grade", "build", "platform-agent"}


@dataclass(frozen=True)
class Request:
    lane: str
    cpus: float
    memory_mb: int
    disk_bytes: int = 0

    def __post_init__(self):
        if (self.lane not in LANES or type(self.cpus) not in (int, float) or not math.isfinite(self.cpus) or self.cpus <= 0
                or type(self.memory_mb) is not int or self.memory_mb <= 0
                or type(self.disk_bytes) is not int or self.disk_bytes < 0):
            raise ValueError("Invalid resource request")


class Resources:
    def __init__(self, directory=None, *, limits=None):
        if directory is None:
            from ..regrading import state_root

            directory = state_root().parent / "operations"
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "resources.sqlite"
        if self.path.is_symlink():
            raise ValueError("Resource ledger cannot be a symlink")
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS capacity (id TEXT PRIMARY KEY,owner TEXT NOT NULL,generation INTEGER NOT NULL,request TEXT NOT NULL,status TEXT NOT NULL,pid INTEGER NOT NULL,start_ticks TEXT,heartbeat REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY,kind TEXT NOT NULL,owner TEXT NOT NULL,generation INTEGER NOT NULL,owned INTEGER NOT NULL,state TEXT NOT NULL,expires REAL,error TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS consumers (asset TEXT NOT NULL REFERENCES assets(id),consumer TEXT NOT NULL,PRIMARY KEY(asset,consumer))")
            existing = db.execute("SELECT payload FROM settings WHERE id=1").fetchone()
            if not existing:
                value = limits or self.defaults()
                self.validate_limits(value)
                db.execute("INSERT INTO settings VALUES(1,?)", (json.dumps(value, sort_keys=True),))
            elif limits is not None and json.loads(existing[0]) != limits:
                raise ValueError("Host limits already exist; all workspaces must share one budget")

    @staticmethod
    def defaults():
        try:
            memory = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                              if line.startswith("MemTotal:"))) // 1024
        except (OSError, StopIteration, ValueError):
            memory = 4096
        return {"cpus": float(os.cpu_count() or 1), "memory_mb": max(512, int(memory * 0.8)),
                "disk_bytes": 100 * 1024**3, "lanes": {"eval": 16, "grade": 2, "build": 1, "platform-agent": 1}}

    @staticmethod
    def validate_limits(value):
        if (set(value) != {"cpus", "memory_mb", "disk_bytes", "lanes"}
                or type(value["cpus"]) not in (int, float)
                or set(value["lanes"]) != LANES
                or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                       for v in [value["cpus"], value["memory_mb"], value["disk_bytes"], *value["lanes"].values()])
                or any(type(v) is not int for v in [value["memory_mb"], value["disk_bytes"], *value["lanes"].values()])):
            raise ValueError("Invalid host resource limits")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def acquire(self, request, *, key, owner, generation, free_bytes=None, headroom_bytes=0):
        if generation < 1 or not key or not owner:
            raise ValueError("Resource ownership is required")
        payload = json.dumps(asdict(request), sort_keys=True)
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM capacity WHERE id=?", (key,)).fetchone()
            if existing:
                if (existing["owner"], existing["generation"], existing["request"]) != (owner, generation, payload):
                    raise ValueError("Lease identity conflict")
                return {"status": "granted" if existing["status"] == "active" else existing["status"], "id": key}
            limits = json.loads(db.execute("SELECT payload FROM settings WHERE id=1").fetchone()[0])
            dimensions = ("cpus", "memory_mb", "disk_bytes")
            if any(asdict(request)[field] > limits[field] for field in dimensions):
                return {"status": "rejected", "reason": "request_exceeds_host_capacity"}
            if free_bytes is not None and free_bytes - request.disk_bytes < headroom_bytes:
                return {"status": "wait", "reason": "disk_headroom"}
            current = [json.loads(row[0]) for row in db.execute("SELECT request FROM capacity WHERE status IN ('active','reconciliation_required')")]
            if (any(sum(r[field] for r in current) + asdict(request)[field] > limits[field] for field in dimensions)
                    or sum(r["lane"] == request.lane for r in current) >= limits["lanes"][request.lane]):
                return {"status": "wait", "reason": "host_capacity"}
            db.execute("INSERT INTO capacity VALUES (?,?,?,?, 'active',?,?,?)",
                       (key, owner, generation, payload, os.getpid(), start_ticks(os.getpid()), time.time()))
            return {"status": "granted", "id": key}

    def release(self, key, *, owner, generation):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM capacity WHERE id=?", (key,)).fetchone()
            if not row or (row["owner"], row["generation"]) != (owner, generation):
                raise ValueError("Stale resource owner or generation")
            db.execute("UPDATE capacity SET status='released',heartbeat=? WHERE id=?", (time.time(), key))

    def reconcile(self):
        unknown = []
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM capacity WHERE status='active'").fetchall():
                observed = start_ticks(row["pid"])
                if observed is None or observed != row["start_ticks"]:
                    db.execute("UPDATE capacity SET status='reconciliation_required' WHERE id=?", (row["id"],))
                    unknown.append(row["id"])
        return unknown

    def snapshot(self):
        with self.transaction() as db:
            return {"limits": json.loads(db.execute("SELECT payload FROM settings WHERE id=1").fetchone()[0]),
                    "reservations": [{**dict(row), "request": json.loads(row["request"])} for row in db.execute(
                        "SELECT * FROM capacity WHERE status!='released' ORDER BY heartbeat")],
                    "assets": [dict(row) for row in db.execute("SELECT * FROM assets WHERE state!='deleted'")]}

    def configure(self, limits):
        self.validate_limits(limits)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM capacity WHERE status!='released'").fetchone():
                raise ValueError("Host budget can only change when reservations have been released")
            db.execute("UPDATE settings SET payload=? WHERE id=1", (json.dumps(limits, sort_keys=True),))
        return self.snapshot()

    def attach(self, asset, *, kind, owner, generation, consumer, owned, expires=None):
        if generation < 1 or not all((asset, kind, owner, consumer)):
            raise ValueError("Actual resource ID and consumer are required")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM assets WHERE id=?", (asset,)).fetchone()
            if row:
                if (row["kind"], row["owner"], row["generation"], row["owned"]) != (kind, owner, generation, int(owned)):
                    raise ValueError("Asset identity or ownership conflict")
                if row["state"] in {"deleting", "owner_deleting", "deleted"}:
                    raise ValueError("Asset is already being reclaimed")
            else:
                db.execute("INSERT INTO assets VALUES(?,?,?,?,?,'retained',?,NULL)",
                           (asset, kind, owner, generation, int(owned), expires))
            db.execute("INSERT OR IGNORE INTO consumers VALUES(?,?)", (asset, consumer))

    def detach(self, asset, *, consumer, owner, generation):
        with self.transaction() as db:
            row = db.execute("SELECT owner,generation FROM assets WHERE id=?", (asset,)).fetchone()
            if not row or tuple(row) != (owner, generation):
                raise ValueError("Stale asset owner or generation")
            db.execute("DELETE FROM consumers WHERE asset=? AND consumer=?", (asset, consumer))


def cancellation_requested():
    root, key = os.environ.get("HARNESS_OPERATION_ROOT"), os.environ.get("HARNESS_OPERATION_ID")
    if not root or not key:
        return False
    from .commands import Commands

    job = Commands(root).query(key)
    return (job["status"] in {"cancelling", "cancelled", "interrupted"}
            or str(job["generation"]) != os.environ.get("HARNESS_OPERATION_GENERATION"))


def record_container(cid, project):
    job = os.environ.get("HARNESS_OPERATION_ID")
    if not job:
        return
    Resources().attach("container:" + cid, kind="container", owner=project,
        generation=int(os.environ["HARNESS_OPERATION_GENERATION"]), consumer="execution:" + job,
        owned=True, expires=time.time() + 86400)


def begin_container_cleanup(cid, project):
    """Fence new consumers before deletion. DockerTarget supplied the inspected ID."""
    job = os.environ.get("HARNESS_OPERATION_ID")
    if not job:
        return True
    ledger = Resources()
    asset = "container:" + cid
    with ledger.transaction() as db:
        row = db.execute("SELECT * FROM assets WHERE id=?", (asset,)).fetchone()
        if row is None:
            # The owner may have failed before its first observation snapshot.
            db.execute("INSERT INTO assets VALUES(?,?,?,?,1,'retained',?,NULL)",
                       (asset, "container", project, int(os.environ["HARNESS_OPERATION_GENERATION"]), time.time()))
        elif row["owner"] != project or row["generation"] != int(os.environ["HARNESS_OPERATION_GENERATION"]):
            raise ValueError("Container cleanup ownership mismatch")
        db.execute("DELETE FROM consumers WHERE asset=? AND consumer=?", (asset, "execution:" + job))
        if db.execute("SELECT 1 FROM consumers WHERE asset=?", (asset,)).fetchone():
            return False
        if row and row["state"] in {"deleting", "owner_deleting"}:
            return False
        db.execute("UPDATE assets SET state='owner_deleting',expires=? WHERE id=?", (time.time(), asset))
        return True


def finish_container_cleanup(cid, project, success):
    if not os.environ.get("HARNESS_OPERATION_ID"):
        return
    with Resources().transaction() as db:
        db.execute("UPDATE assets SET state=?,error=? WHERE id=? AND owner=? AND generation=? AND state='owner_deleting'",
                   ("deleted" if success else "cleanup_failed", None if success else "Docker removal failed",
                    "container:" + cid, project, int(os.environ["HARNESS_OPERATION_GENERATION"])))


@contextmanager
def reservation(request, *, free_bytes=None, headroom_bytes=0):
    """Admission for blocking build/grading processes; caller owns actual cleanup."""
    ledger = Resources()
    key = uuid4().hex
    owner = os.environ.get("HARNESS_OPERATION_ID") or f"process:{os.getpid()}:{start_ticks(os.getpid())}"
    generation = int(os.environ.get("HARNESS_OPERATION_GENERATION", "1"))
    while True:
        if cancellation_requested():
            raise InterruptedError("Operation cancelled before resource admission")
        decision = ledger.acquire(request, key=key, owner=owner, generation=generation,
                                  free_bytes=free_bytes() if callable(free_bytes) else free_bytes,
                                  headroom_bytes=headroom_bytes)
        if decision.get("reason") == "disk_headroom":
            raise ValueError("Estimated preparation footprint exceeds current disk headroom")
        if decision["status"] == "granted":
            break
        if decision["status"] != "wait":
            raise ValueError("Resource admission rejected: " + str(decision))
        time.sleep(0.1)
    try:
        yield key
    finally:
        ledger.release(key, owner=owner, generation=generation)


@asynccontextmanager
async def async_reservation(request):
    import asyncio

    ledger = Resources()
    key = uuid4().hex
    owner = os.environ.get("HARNESS_OPERATION_ID") or f"process:{os.getpid()}:{start_ticks(os.getpid())}"
    generation = int(os.environ.get("HARNESS_OPERATION_GENERATION", "1"))
    while True:
        if cancellation_requested():
            raise asyncio.CancelledError("Operation cancelled before resource admission")
        decision = ledger.acquire(request, key=key, owner=owner, generation=generation)
        if decision["status"] == "granted":
            break
        if decision["status"] != "wait":
            raise ValueError("Resource admission rejected: " + str(decision))
        await asyncio.sleep(0.1)
    try:
        yield key
    finally:
        ledger.release(key, owner=owner, generation=generation)
