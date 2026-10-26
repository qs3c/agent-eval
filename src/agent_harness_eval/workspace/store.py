"""Transactional operational index. Raw experiment files remain authoritative."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .migrations import upgrade
from .models import ExperimentPlan, RunState, canonical_json, identity


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / ".workspace"
        self.path = self.directory / "state.sqlite"
        upgrade(self.path)

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

    @staticmethod
    def event(db, topic, entity, payload):
        db.execute("INSERT INTO events(topic,entity,payload,created) VALUES (?,?,?,?)",
                   (topic, entity, canonical_json(payload), time.time()))

    def create_plan(self, frozen_plan, idempotency_key):
        with self.transaction() as db:
            return self.insert_plan(db, frozen_plan, idempotency_key)

    def insert_plan(self, db, frozen_plan, idempotency_key):
        """Also used inside an atomic legacy import transaction."""
        if not idempotency_key or len(idempotency_key) > 512:
            raise ValueError("A bounded idempotency key is required")
        plan = ExperimentPlan.model_validate(frozen_plan)
        payload, plan_id = canonical_json(plan), identity(plan)
        existing = db.execute("SELECT plan_id FROM requests WHERE key=?", (idempotency_key,)).fetchone()
        if existing:
            if existing[0] != plan_id:
                raise ValueError("Idempotency key conflicts with different frozen input")
            return plan_id
        db.execute("INSERT OR IGNORE INTO plans VALUES (?,?,?)", (plan_id, payload, time.time()))
        db.execute("INSERT INTO requests VALUES (?,?)", (idempotency_key, plan_id))
        self.event(db, "plan.created", plan_id, {"request": idempotency_key})
        return plan_id

    def plan(self, plan_id):
        with self.transaction() as db:
            row = db.execute("SELECT payload FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise KeyError(plan_id)
        value = ExperimentPlan.model_validate_json(row[0])
        if identity(value) != plan_id:
            raise ValueError("Stored plan identity mismatch")
        return value

    def create_record(self, kind, value, *, state=None):
        from .models import Attempt, GradeRun, Incident, ResourceLease

        models = {"attempt": Attempt, "grade": GradeRun, "incident": Incident, "lease": ResourceLease}
        model = models[kind].model_validate(value)
        key, payload = identity(model), canonical_json(model)
        with self.transaction() as db:
            if kind == "attempt" and not db.execute("SELECT 1 FROM plans WHERE id=?", (model.plan_id,)).fetchone():
                raise ValueError("Attempt references an unknown plan")
            if kind in {"grade", "incident"} and not db.execute(
                "SELECT 1 FROM records WHERE kind='attempt' AND id=?", (model.attempt_id,)
            ).fetchone():
                raise ValueError("Record references an unknown attempt")
            exists = db.execute("SELECT 1 FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
            if not exists:
                db.execute("INSERT INTO records(kind,id,payload,state) VALUES (?,?,?,?)",
                           (kind, key, payload, canonical_json(RunState.model_validate(state or {}))))
                self.event(db, kind + ".created", key, {})
        return key

    def transition(self, kind, key, state, *, generation):
        payload = canonical_json(RunState.model_validate(state))
        with self.transaction() as db:
            changed = db.execute("UPDATE records SET state=?,generation=generation+1 WHERE kind=? AND id=? AND generation=?",
                                 (payload, kind, key, generation)).rowcount
            if changed != 1:
                raise ValueError("Stale record generation")
            self.event(db, kind + ".state", key, {"state": json.loads(payload), "generation": generation + 1})
        return generation + 1

    def pending_events(self):
        with self.transaction() as db:
            return [dict(r) for r in db.execute("SELECT * FROM events WHERE delivered=0 ORDER BY seq")]

    def acknowledge(self, seq):
        with self.transaction() as db:
            db.execute("UPDATE events SET delivered=1 WHERE seq=?", (seq,))
