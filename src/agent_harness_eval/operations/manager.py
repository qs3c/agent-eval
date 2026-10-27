"""Detached local manager. The HTTP server is only a command client."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from ..workspace.migrations import exclusive
from .commands import Commands
from .recovery import reconcile, start_ticks


class Manager:
    def __init__(self, root, *, allow_live=False):
        from ..console import Console

        self.root = Path(root).resolve()
        self.commands = Commands(self.root)
        self.console = Console(self.root, allow_live=allow_live)
        self.owner = uuid4().hex
        self.draining = False
        self.last_gc = 0
        self.last_cycle_check = 0

    def maintenance(self):
        if time.monotonic() - self.last_cycle_check >= 1:
            self.last_cycle_check = time.monotonic()
            from ..rsi.cycle import enforce_deadlines

            enforce_deadlines(self.root)
        if time.monotonic() - self.last_gc < 60:
            return
        self.last_gc = time.monotonic()
        if self.console.process is None:
            from .resources import Resources
            from .retention import collect, delete_stopped_container

            ledger = Resources()
            ledger.reconcile()
            try:
                collect(ledger, delete_stopped_container, limit=2)
            except RuntimeError:
                pass  # Another workspace's collector owns the same host lock.
        from ..guardian.retention import sweep
        from ..guardian.evidence import read

        outputs = set(self.console.artifacts.iterdir())
        outputs.update(Path(job["payload"]["output"]) for job in self.commands.list()
                       if job["kind"] == "experiment" and "output" in job["payload"])
        for output in outputs:
            if output.is_symlink() or not output.is_dir():
                continue
            config = read(output / "manifest.json", {}).get("experiment", {})
            policy = config.get("batch")
            if policy:
                try:
                    sweep(output, policy)
                except (OSError, ValueError) as exc:
                    with self.commands.store.transaction() as db:
                        self.commands.store.event(db, "retention.retry_required", str(output), {"error": str(exc)})

    def adopt(self):
        """Idempotent compatibility inbox; legacy tables remain read projections."""
        from ..integrations.tracking import tick

        tick(self.root)
        from ..rsi.cycle import tick as tick_cycles

        tick_cycles(self.root)
        with self.console.connect() as db:
            rows = db.execute("SELECT * FROM runs WHERE status='queued' AND historical=0 ORDER BY created").fetchall()
        for row in rows:
            from .submission import submit_console

            submit_console(self.console, row)
        from ..rsi.store import Store

        for action in Store(self.root).actions():
            if action["status"] == "queued":
                self.commands.submit("rsi", {"action_id": action["id"]}, key="rsi:" + action["id"])
        from ..regrading import database

        owned_outputs = {str(Path(j["payload"]["output"]).resolve())
                         for j in self.commands.list() if j["kind"] == "experiment" and "output" in j["payload"]}
        with database() as db:
            grades = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created").fetchall()
        for grade in grades:
            output = Path(grade["output"]).resolve()
            if output.is_relative_to(self.console.artifacts) or str(output) in owned_outputs:
                from ..guardian.evidence import read

                state = read(output / "batch-state.json", {})
                if state and state.get("phase") not in {"completed", "failed", "interrupted"}:
                    continue
                self.commands.submit("grade", {"grade_id": grade["id"]}, key="grade:" + grade["id"])

    def project(self):
        for job in self.commands.list():
            if job["kind"] == "grade" and job["status"] == "cancelled":
                from ..regrading import database

                with database() as db:
                    db.execute("UPDATE jobs SET status='cancelled' WHERE id=? AND status IN ('queued','running')",
                               (job["payload"]["grade_id"],))
            if job["kind"] == "rsi" and job["status"] == "cancelled":
                from ..rsi.store import Store

                store = Store(self.root)
                key = job["payload"]["action_id"]
                if store.action(key)["status"] == "queued":
                    store.finish(key, "cancelled", "Cancelled before execution")
            if job["kind"] != "console":
                continue
            state, key = job["status"], job["payload"]["run_id"]
            row = self.console.row(key)
            if state in {"held", "paused", "pausing", "cancelled"}:
                self.console.update(key, "paused" if state == "held" else state)
            elif state == "queued" and row["status"] == "paused":
                self.console.update(key, "queued")
            elif state in {"failed", "interrupted"} and row["status"] in {"queued", "running", "pausing", "paused"}:
                self.console.update(key, state, job["error"])

    def execute(self, job):
        from .worker import execute

        errors = []

        def work():
            try:
                execute(self, job)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=work, name="operation-worker")
        worker.start()
        while worker.is_alive():
            self.maintenance()
            current = self.commands.query(job["id"])
            if current["status"] == "cancelling":
                self.console.stop.set()
            process = self.console.process
            try:
                self.commands.worker_update(job["id"], self.owner, job["generation"],
                    pid=process.pid if process else None,
                    start_ticks=start_ticks(process.pid) if process else None)
            except ValueError:
                self.console.stop.set()
                worker.join(timeout=getattr(self.console, "shutdown_seconds", 8) + 12)
                raise
            worker.join(timeout=0.2)
        self.console.stop.clear()
        if errors:
            from ..console import redact

            cancelled = self.commands.query(job["id"])["status"] == "cancelling"
            self.commands.worker_update(job["id"], self.owner, job["generation"],
                status="cancelled" if cancelled else "failed" if isinstance(errors[0], ValueError) else "interrupted",
                error=redact(str(errors[0])))
            self.project()
            return
        status = "completed"
        if job["kind"] == "console":
            status = self.console.row(job["payload"]["run_id"])["status"]
        elif job["kind"] == "rsi":
            from ..rsi.store import Store

            status = Store(self.root).action(job["payload"]["action_id"])["status"]
        elif job["kind"] == "grade":
            from ..regrading import database

            with database() as db:
                status = db.execute("SELECT status FROM jobs WHERE id=?", (job["payload"]["grade_id"],)).fetchone()[0]
        if status not in {"completed", "failed", "interrupted", "cancelled"}:
            status = "interrupted"
        current = self.commands.query(job["id"])
        if current["status"] == "cancelling":
            status = "cancelled"
        self.commands.worker_update(job["id"], self.owner, job["generation"], status=status)
        self.project()

    def run(self):
        directory = self.commands.store.directory
        with exclusive(directory / "manager.lock"), exclusive(self.console.state / "server.lock") as legacy_lock:
            self.console.lock = legacy_lock
            with self.console.connect() as db:
                abandoned = db.execute("SELECT * FROM runs WHERE historical=0 AND status IN ('running','interrupted')").fetchall()
            from .submission import submit_console

            for row in abandoned:
                job = submit_console(self.console, row)
                if job["status"] == "queued":
                    with self.commands.store.transaction() as db:
                        db.execute("UPDATE jobs SET status='interrupted',error='legacy_external_outcome_unknown',generation=generation+1 WHERE id=?", (job["id"],))
            reconcile(self.commands)
            with self.console.connect() as db:
                db.execute("UPDATE runs SET status='interrupted',error='Manager interrupted; inspect evidence before recovery' WHERE status='running' AND historical=0")
            from ..rsi.store import Store

            Store(self.root).recover()
            info = {"pid": os.getpid(), "start_ticks": start_ticks(os.getpid()),
                    "owner": self.owner, "allow_live": self.console.allow_live}
            (directory / "manager.json").write_text(json.dumps(info))
            previous = signal.signal(signal.SIGTERM, lambda *_: setattr(self, "draining", True))
            try:
                while not self.draining:
                    stop = directory / "stop.json"
                    if stop.exists():
                        stop.unlink()
                        break
                    self.adopt()
                    self.project()
                    self.maintenance()
                    # Interrupted work needs explicit inspection, never implicit replay.
                    if any(job["status"] == "interrupted" and not (job["error"] or "").startswith("acknowledged:")
                           for job in self.commands.list()):
                        time.sleep(0.25)
                        continue
                    job = self.commands.claim(self.owner)
                    if job:
                        self.execute(job)
                    else:
                        time.sleep(0.25)
            finally:
                signal.signal(signal.SIGTERM, previous)
                (directory / "manager.json").unlink(missing_ok=True)
                self.console.lock = None


def status(root):
    path = Path(root) / ".workspace/manager.json"
    try:
        info = json.loads(path.read_text())
        info["alive"] = start_ticks(info["pid"]) == info["start_ticks"] and info["start_ticks"] is not None
        return info
    except (OSError, ValueError, KeyError):
        return {"alive": False}


def ensure(root, *, allow_live=False):
    root = Path(root).resolve()
    commands = Commands(root)
    with exclusive(commands.store.directory / "start.lock"):
        info = status(root)
        if info["alive"]:
            if info["allow_live"] != allow_live:
                raise ValueError("Manager live policy differs：后台 Manager 的真实模型开关与本次启动不一致。请先在该工作区执行 manager stop，等待 manager status 显示 alive=false，再以需要的 --allow-live 设置启动服务；关闭 Web 不会停止 Manager。")
            return info
        (commands.store.directory / "stop.json").unlink(missing_ok=True)
        argv = [sys.executable, "-m", "agent_harness_eval.cli", "manager", "run", "--root", str(root)]
        if allow_live:
            argv.append("--allow-live")
        with (commands.store.directory / "manager.log").open("ab") as log:
            process = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(100):
            info = status(root)
            if info["alive"]:
                return info
            if process.poll() is not None:
                break
            time.sleep(0.05)
        raise RuntimeError("Manager failed to start; inspect .workspace/manager.log")


def stop(root, *, cancel=False):
    commands = Commands(root)
    if cancel:
        manager = Manager(root)
        manager.adopt()
        for job in commands.list():
            if job["status"] in {"queued", "held", "running", "pausing", "paused"}:
                commands.control(job["id"], "cancel")
        manager.project()
    (commands.store.directory / "stop.json").write_text(json.dumps({"cancel": cancel}))
    return {"requested": "cancel" if cancel else "drain", **status(root)}
