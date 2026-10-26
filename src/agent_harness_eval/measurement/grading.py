"""Immutable scorer executions linked to the original experiment attempt."""

import json
import os
from pathlib import Path

from ..workspace.models import Attempt, GradeRun
from ..workspace.store import Store


def begin(output, work, *, agent, task, repeat, scorer, input_digest):
    root = os.environ.get("HARNESS_OPERATION_ROOT")
    if not root:
        return None  # Legacy library execution still has its original execution.json.
    from ..operations.commands import Commands

    commands = Commands(root)
    current = commands.query(os.environ["HARNESS_OPERATION_ID"])
    plan_id = current["payload"].get("plan_id")
    if plan_id is None:
        from ..console import Console

        console = Console(root)
        for job in reversed(commands.list()):
            payload = job["payload"]
            path = payload.get("output")
            if job["kind"] == "console":
                path = console.row(payload["run_id"])["path"]
            if path and Path(path).resolve() == output.resolve():
                plan_id = payload.get("plan_id")
                if plan_id:
                    break
    if not plan_id:
        from ..manifests import load_manifest
        from ..freezing import plan_from_manifest

        plan = plan_from_manifest(load_manifest(output), evidence=(str(output / "manifest.json"),))
        plan_id = commands.store.create_plan(plan, "grading-import:" + str(output.resolve()))
    store = Store(root)
    from ..report import task_key

    observed = []
    for path in (output / "jobs" / agent).glob("*/result.json"):
        value = json.loads(path.read_text())
        if task_key(value) == task:
            observed.append((value.get("started_at", ""), str(path), value))
    observed.sort(key=lambda row: row[:2])
    raw = observed[repeat - 1][2] if len(observed) >= repeat else {}
    state = {"execution": "failed" if raw.get("exception_info") else "completed" if raw.get("finished_at") else "queued",
             "scoring": "pending", "evidence": "complete" if raw.get("finished_at") else "missing", "resources": "retained"}
    attempt_id = store.create_record("attempt", Attempt(plan_id=plan_id, agent_id=agent, task_id=task, repeat=repeat), state=state)
    ordinal = 1
    for path in work.parent.glob("*/grade-run.json"):
        value = json.loads(path.read_text())
        ordinal = max(ordinal, value["ordinal"] + 1)
    grade = GradeRun(attempt_id=attempt_id, scorer=scorer, input_digest=input_digest, ordinal=ordinal)
    key = store.create_record("grade", grade, state={"execution": "running", "scoring": "running", "evidence": "partial"})
    (work / "grade-run.json").write_text(grade.model_dump_json(indent=2))
    return key


def finish(key, *, success):
    if key is None:
        return
    from ..operations.worker import is_managed_worker

    if not is_managed_worker():
        raise ValueError("Grade completion has lost execution ownership")
    Store(os.environ["HARNESS_OPERATION_ROOT"]).transition("grade", key,
        {"execution": "completed" if success else "failed", "scoring": "complete" if success else "failed",
         "evidence": "complete", "resources": "retained" if success else "reclaim_pending"}, generation=0)
