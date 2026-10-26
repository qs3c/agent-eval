"""Transactional job commands. Unknown external outcomes are never replayed."""

import json
import time

from ..workspace.models import canonical_json, identity
from ..workspace.store import Store

ACTIVE = {"running", "pausing", "paused", "cancelling"}
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class Commands:
    def __init__(self, root):
        self.store = Store(root)

    def submit(self, kind, payload, *, key):
        if kind not in {"console", "rsi", "experiment", "grade", "integration"}:
            raise ValueError("Unknown operation kind")
        if not key or len(key) > 512:
            raise ValueError("A bounded idempotency key is required")
        job_id = identity({"operation_key": key})
        serialized = canonical_json(payload)
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row:
                if row["payload"] != serialized or row["kind"] != kind:
                    raise ValueError("Operation key conflicts with different input")
            else:
                db.execute("INSERT INTO jobs(id,kind,payload,created) VALUES (?,?,?,?)",
                           (job_id, kind, serialized, time.time()))
                self.store.event(db, "job.submitted", job_id, {"kind": kind})
        return self.query(job_id)

    def query(self, job_id):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        return {**dict(row), "payload": json.loads(row["payload"])}

    def list(self):
        with self.store.transaction() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY created,id")]
        return [self.query(key) for key in ids]

    def control(self, job_id, action):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            state = row["status"]
            if action == "pause":
                if state == "queued":
                    target = "held"
                elif state in {"running", "pausing", "paused"}:
                    if not json.loads(row["payload"]).get("cooperative_pause"):
                        raise ValueError("This executor cannot pause between attempts")
                    target = "pausing" if state == "running" else state
                elif state == "held":
                    target = state
                else:
                    raise ValueError("Only pending or active jobs can pause")
            elif action == "resume":
                if state == "held":
                    target = "queued"
                elif state in {"paused", "pausing"}:
                    target = "running"
                else:
                    raise ValueError("Resume requires a paused job; interrupted outcomes need reconciliation")
            elif action == "cancel":
                target = "cancelled" if state in {"queued", "held"} else "cancelling"
                if state in TERMINAL:
                    target = state
            else:
                raise ValueError("Unknown control action")
            db.execute("UPDATE jobs SET status=? WHERE id=?", (target, job_id))
            self.store.event(db, "job.control", job_id, {"action": action, "state": target})
        return self.query(job_id)

    def claim(self, owner):
        with self.store.transaction() as db:
            # One execution owner per workspace, even when two callers race.
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('running','pausing','paused','cancelling')").fetchone():
                return None
            row = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',owner=?,generation=generation+1,heartbeat=? WHERE id=?",
                       (owner, time.time(), row[0]))
            self.store.event(db, "job.claimed", row[0], {"owner": owner})
        return self.query(row[0])

    def acknowledge_interruption(self, job_id):
        """CLI-only acknowledgement after the operator inspects external resources."""
        from .recovery import start_ticks

        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] != "interrupted":
                raise ValueError("Only interrupted operations can be acknowledged")
            observed = start_ticks(row["pid"])
            if observed is not None and observed == row["start_ticks"]:
                raise ValueError("Original worker is still alive")
            db.execute("UPDATE jobs SET error=? WHERE id=?",
                       ("acknowledged: " + (row["error"] or "unknown outcome"), job_id))
            self.store.event(db, "job.interruption_acknowledged", job_id, {"replayed": False})
        return self.query(job_id)

    def worker_update(self, job_id, owner, generation, *, status=None, pid=None, start_ticks=None, error=None):
        if status is not None and status not in TERMINAL | {"paused"}:
            raise ValueError("Invalid worker state")
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=? AND generation=?",
                             (job_id, owner, generation)).fetchone()
            if not row or row["status"] not in ACTIVE:
                raise ValueError("Stale worker generation or terminal operation")
            target = status or row["status"]
            # Cancellation remains explicit, including racing normal completion.
            if row["status"] == "cancelling" and status in {"completed", "failed"}:
                target = "cancelled"
            if status == "paused" and row["status"] != "pausing":
                raise ValueError("Pause acknowledgement no longer requested")
            db.execute("UPDATE jobs SET status=?,heartbeat=?,pid=COALESCE(?,pid),start_ticks=COALESCE(?,start_ticks),error=? WHERE id=?",
                       (target, time.time(), pid, start_ticks, error, job_id))
            if status:
                self.store.event(db, "job.state", job_id, {"state": target, "generation": generation})
        return self.query(job_id)
