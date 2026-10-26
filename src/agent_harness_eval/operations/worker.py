"""Compatibility executor facade; no second scheduling loop."""

import asyncio
import os

from .commands import Commands


def is_managed_worker():
    root, key = os.environ.get("HARNESS_OPERATION_ROOT"), os.environ.get("HARNESS_OPERATION_ID")
    if root and key:
        job = Commands(root).query(key)
        if (job["owner"] != os.environ.get("HARNESS_OPERATION_OWNER")
                or str(job["generation"]) != os.environ.get("HARNESS_OPERATION_GENERATION")
                or job["status"] not in {"running", "pausing", "paused", "cancelling"}):
            raise ValueError("Worker ownership is stale")
        return True
    # Compatibility executor inherits its actual singleton flock, not a boolean flag.
    descriptor, path = os.environ.get("HARNESS_LEGACY_LOCK_FD"), os.environ.get("HARNESS_LEGACY_LOCK_PATH")
    if descriptor and path:
        import fcntl

        fd = int(descriptor)
        if not os.path.samestat(os.fstat(fd), os.stat(path)):
            raise ValueError("Worker lock identity mismatch")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    return False


def execute(manager, job):
    console = manager.console
    # Passed only by the trusted manager, never selected by an HTTP/model payload.
    console.operation_environment = {
        "HARNESS_OPERATION_ROOT": str(manager.root),
        "HARNESS_OPERATION_ID": job["id"],
        "HARNESS_OPERATION_GENERATION": str(job["generation"]),
        "HARNESS_OPERATION_OWNER": manager.owner,
    }
    try:
        if job["kind"] == "console":
            row = console.row(job["payload"]["run_id"])
            if row["status"] != "queued":
                return  # A legacy owner may have already finished this inbox item.
            import json
            from ..workspace.models import canonical_json

            plan = manager.commands.store.plan(job["payload"]["plan_id"])
            if canonical_json(json.loads(row["config"])) != canonical_json(plan.experiment):
                raise ValueError("Console configuration differs from admitted plan")
            from .submission import verify_inputs

            verify_inputs(plan)
            console.execute(row)
        elif job["kind"] == "rsi":
            from ..rsi.store import Store

            action = Store(manager.root).action(job["payload"]["action_id"])
            if action["status"] == "queued":
                console.execute_rsi(action)
        elif job["kind"] == "experiment":
            execute_experiment(manager, job)
        elif job["kind"] == "grade":
            execute_grade(manager, job)
        elif job["kind"] == "integration":
            execute_integration(manager, job)
        else:
            raise ValueError("Executor not registered: " + job["kind"])
    finally:
        console.operation_environment = {}


def execute_integration(manager, job):
    import subprocess
    import sys

    console = manager.console
    logs = manager.commands.store.directory / "logs"
    logs.mkdir(exist_ok=True)
    console.shutdown_seconds = 40
    with (logs / (job["id"] + ".log")).open("ab") as log:
        console.process = subprocess.Popen([sys.executable, "-m", "agent_harness_eval.cli",
            "integration-work", job["id"], "--root", str(manager.root)], cwd=manager.root,
            env={**os.environ, **console.operation_environment}, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, pass_fds=(console.lock.fileno(),))
        try:
            while console.process.poll() is None:
                if console.stop.wait(.2):
                    console.terminate()
                    break
            code = console.process.wait()
            if code:
                raise ValueError("Integration build failed; inspect " + str(logs / (job["id"] + ".log")))
        finally:
            if console.process.poll() is None:
                console.terminate()
            console.process = None


def execute_grade(manager, job):
    import subprocess
    import sys

    console = manager.console
    logs = manager.commands.store.directory / "logs"
    logs.mkdir(exist_ok=True)
    console.shutdown_seconds = 30
    with (logs / (job["id"] + ".log")).open("ab") as log:
        console.process = subprocess.Popen([sys.executable, "-m", "agent_harness_eval.cli",
            "grade-work", job["payload"]["grade_id"]], cwd=manager.root,
            env={**os.environ, **console.operation_environment}, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, pass_fds=(console.lock.fileno(),))
        try:
            while console.process.poll() is None:
                if console.stop.wait(0.2):
                    console.terminate()
                    break
            console.process.wait()
        finally:
            if console.process.poll() is None:
                console.terminate()
            console.process = None


def execute_experiment(manager, job):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from ..provenance import digest

    payload, console = job["payload"], manager.console
    config_path = Path(payload["config"])
    if digest(config_path) != payload["config_digest"]:
        raise ValueError("Frozen configuration changed")
    plan = manager.commands.store.plan(payload["plan_id"])
    config = json.loads(config_path.read_text())
    from ..workspace.models import canonical_json

    if canonical_json(config) != canonical_json(plan.experiment):
        raise ValueError("Configuration differs from the admitted plan")
    from .submission import verify_inputs

    verify_inputs(plan)
    model, guardian = config["model"], config.get("guardian") or {}
    if (model["mode"] == "live" or guardian.get("model")) and not console.allow_live:
        raise ValueError("Manager has not enabled live models")
    console.shutdown_seconds = (4 * guardian["command_seconds"] + guardian["cleanup_seconds"] + 30) if guardian else 120
    argv = [sys.executable, "-u", "-m", "agent_harness_eval.cli", "run", str(config_path), "--output", payload["output"]]
    if payload["resume"]:
        argv.append("--resume")
    if payload["workbench_repair"]:
        argv.extend(["--workbench-repair", payload["workbench_repair"]])
    logs = manager.commands.store.directory / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / (job["id"] + ".log")).open("ab") as log:
        console.process = subprocess.Popen(argv, cwd=manager.root,
            env={**os.environ, **console.models.environment(model, guardian.get("model")), **console.operation_environment},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(console.lock.fileno(),))
        try:
            while console.process.poll() is None:
                if console.stop.wait(0.2):
                    console.terminate()
                    break
            code = console.process.wait()
            if code and not console.stop.is_set():
                raise RuntimeError(f"Experiment worker exited {code}; inspect {logs / (job['id'] + '.log')}")
        finally:
            if console.process.poll() is None:
                console.terminate()
            console.process = None


async def checkpoint(*, idle=True):
    """Gate new attempts while allowing already admitted attempts to finish."""
    root, key = os.environ.get("HARNESS_OPERATION_ROOT"), os.environ.get("HARNESS_OPERATION_ID")
    if not root or not key:
        return
    commands = Commands(root)
    generation = int(os.environ["HARNESS_OPERATION_GENERATION"])
    owner = os.environ["HARNESS_OPERATION_OWNER"]
    while True:
        job = commands.query(key)
        if job["generation"] != generation or job["owner"] != owner:
            raise asyncio.CancelledError("Execution ownership changed")
        if job["status"] in {"cancelling", "cancelled", "interrupted"}:
            raise asyncio.CancelledError("Operation cancelled")
        if job["status"] == "running":
            return
        if job["status"] == "pausing" and idle:
            try:
                commands.worker_update(key, owner, generation, status="paused")
            except ValueError:
                # A concurrent resume/cancel wins; reread rather than undo it.
                continue
        await asyncio.sleep(0.2)


class Admission:
    """One instance per event loop; no await between admission and accounting."""

    def __init__(self, *, cpus=None, memory_mb=None):
        self.active = set()
        self.started_trials = set()
        self.cpus, self.memory_mb = cpus, memory_mb
        self.leases = {}

    async def enter(self, key):
        if not os.environ.get("HARNESS_OPERATION_ID"):
            self.active.add(key)
            return
        # Re-evaluate after each wait: in-flight attempts may have finished.
        task = asyncio.create_task(checkpoint(idle=False))
        try:
            while not task.done():
                if not self.active:
                    await checkpoint(idle=True)
                await asyncio.sleep(0.05)
            await task
            await self.reserve(key)
            self.active.add(key)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def started(self, event):
        if self.cpus is None:
            self.cpus = event.config.environment.override_cpus
            self.memory_mb = event.config.environment.override_memory_mb
        await self.enter(event.trial_name)
        self.started_trials.add(event.trial_name)

    async def ended(self, event):
        await self.cleanup_trial(event.trial_name)

    async def cleanup_trial(self, trial_name):
        from ..guardian.docker import DockerTarget

        result = await DockerTarget(trial_name, timeout=15).stop()
        if not result["resources_released"]:
            raise RuntimeError("Native trial cleanup is unconfirmed: " + trial_name)
        await self.finish_guarded(trial_name, trial_name)

    async def cleanup_native(self):
        results = await asyncio.gather(*(self.cleanup_trial(name) for name in self.started_trials), return_exceptions=True)
        errors = [value for value in results if isinstance(value, BaseException)]
        if errors:
            raise RuntimeError("Native container cleanup failed; resource reservations retained") from errors[0]

    async def reserve(self, key):
        if not os.environ.get("HARNESS_OPERATION_ID") or self.cpus is None or self.memory_mb is None:
            return
        from ..workspace.models import identity
        from .resources import Request, Resources

        ledger = Resources()
        owner = os.environ["HARNESS_OPERATION_ID"]
        generation = int(os.environ["HARNESS_OPERATION_GENERATION"])
        lease = identity({"owner": owner, "generation": generation, "attempt": str(key)})
        while True:
            await checkpoint(idle=not self.active)
            decision = ledger.acquire(Request("eval", self.cpus, self.memory_mb), key=lease,
                                      owner=owner, generation=generation)
            if decision["status"] == "granted":
                self.leases[key] = (ledger, lease, owner, generation)
                return
            if decision["status"] != "wait":
                raise ValueError("Resource admission failed: " + str(decision))
            await asyncio.sleep(0.25)

    def leave(self, key):
        self.active.discard(key)
        lease = self.leases.pop(key, None)
        if lease:
            ledger, identity, owner, generation = lease
            ledger.release(identity, owner=owner, generation=generation)

    async def finish_guarded(self, key, trial_name):
        if key not in self.leases:
            self.leave(key)
            return
        from ..guardian.docker import DockerTarget
        from ..guardian.process import command

        project = DockerTarget(trial_name).project
        try:
            result = await command(["docker", "ps", "-aq", "--no-trunc", "--filter",
                                    "label=com.docker.compose.project=" + project], 20)
            if result["returncode"] == 0 and not result["stdout"].strip():
                self.leave(key)
                return
        except Exception:
            pass
        ledger, lease, owner, generation = self.leases.pop(key)
        self.active.discard(key)
        with ledger.transaction() as db:
            db.execute("UPDATE capacity SET status='reconciliation_required' WHERE id=? AND owner=? AND generation=?",
                       (lease, owner, generation))
