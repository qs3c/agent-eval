import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from ..guardian.evidence import now, read, write


class Store:
    def __init__(self, root):
        self.project = Path(root).resolve()
        self.root = self.project / ".rsi"
        self.root.mkdir(parents=True, exist_ok=True)
        for kind in ("cases", "candidates", "actions"):
            (self.root / kind).mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, candidate TEXT, kind TEXT, status TEXT, created TEXT, updated TEXT, error TEXT, confirm_live INTEGER)"
            )

    def connect(self):
        db = sqlite3.connect(self.root / "queue.sqlite", timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def path(self, kind, identity):
        if kind not in {"cases", "candidates", "actions"} or not re.fullmatch(
            r"[0-9a-f]{32}", identity
        ):
            raise ValueError("Invalid RSI record ID")
        result = self.root / kind / identity
        if result.is_symlink() or not result.resolve().is_relative_to(
            self.root.resolve()
        ):
            raise ValueError("RSI record path escapes store")
        return result

    def get(self, kind, identity):
        value = read(self.path(kind, identity) / "record.json")
        if not isinstance(value, dict):
            raise KeyError(identity)
        return value

    def save(self, kind, value):
        value = {**value, "updated_at": now()}
        write(self.path(kind, value["id"]) / "record.json", value)
        return value

    def create(self, kind, value):
        identity = uuid4().hex
        self.path(kind, identity).mkdir()
        return self.save(kind, {**value, "id": identity, "created_at": now()})

    def list(self, kind):
        values = []
        for directory in (self.root / kind).iterdir():
            try:
                values.append(self.get(kind, directory.name))
            except (ValueError, KeyError):
                continue
        return sorted(values, key=lambda x: x["created_at"], reverse=True)

    def enqueue(self, candidate, kind, confirm_live=False):
        record = self.get("candidates", candidate)
        if kind not in {"improve", "evaluate", "publish"}:
            raise ValueError("Unknown RSI action")
        required = "created" if kind == "improve" else "validated"
        if record["phase"] != required:
            raise ValueError(f"{kind} requires phase {required}")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM actions WHERE candidate=? AND status IN ('queued','running')",
                (candidate,),
            ).fetchone():
                raise ValueError("Candidate already has an active action")
            identity, stamp = uuid4().hex, now()
            db.execute(
                "INSERT INTO actions VALUES (?,?,?,'queued',?,?,NULL,?)",
                (identity, candidate, kind, stamp, stamp, int(confirm_live)),
            )
        return self.action(identity)

    def action(self, identity):
        with self.connect() as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return dict(row)

    def actions(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM actions ORDER BY created DESC LIMIT 1000"
                )
            ]

    def next_action(self):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM actions WHERE status='queued' ORDER BY created LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def claim(self, identity):
        with self.connect() as db:
            count = db.execute(
                "UPDATE actions SET status='running',updated=? WHERE id=? AND status='queued'",
                (now(), identity),
            ).rowcount
        if not count:
            raise ValueError("Action already claimed or finished")
        return self.action(identity)

    def finish(self, identity, status, error=None):
        with self.connect() as db:
            db.execute(
                "UPDATE actions SET status=?,updated=?,error=? WHERE id=?",
                (status, now(), error, identity),
            )

    def recover(self):
        import fcntl

        # Called only while the console's exclusive server lock is held.
        for action in self.actions():
            if action["status"] == "running":
                root = self.path("candidates", action["candidate"])
                with (root / "operation.lock").open("a") as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    self.finish(
                        action["id"],
                        "interrupted",
                        "Process stopped; no automatic replay",
                    )
                    candidate = self.get("candidates", action["candidate"])
                    candidate["phase"] = "interrupted"
                    self.save("candidates", candidate)
