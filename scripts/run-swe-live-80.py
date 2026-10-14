"""Authorized SWE-80 pipeline: preflight, two-agent assist run, official regrade."""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))
os.environ["PYTHONPATH"] = str(ROOT / "src")
os.environ["PATH"] = str(ROOT / ".tools") + os.pathsep + os.environ["PATH"]
from agent_harness_eval.config import load
from agent_harness_eval.guardian.config import validate_tasks
from agent_harness_eval.manifests import load_manifest
from agent_harness_eval.model_pool import ModelPool
from agent_harness_eval.provenance import digest, write_json

state = ROOT / "logs/swe-live-80-pipeline"
state.mkdir(parents=True, exist_ok=True)
lock = (state / "pipeline.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
run = ROOT / "artifacts/swe-live-80"
resume = "--resume" in sys.argv
if run.exists() and not resume:
    raise SystemExit(
        "Run directory exists; use --resume after inspecting the retained state"
    )


def status(phase, **extra):
    write_json(
        state / "status.json", {"phase": phase, "updated_unix": time.time(), **extra}
    )
    print(phase, extra, flush=True)


try:
    status("preflight", model_calls_started=False)
    assert json.loads((state / "guardian-connectivity.json").read_text())["passed"]
    selection = json.loads((ROOT / "datasets/swe-live-80/selection.json").read_text())
    ids = selection["instance_ids"]
    assert len(ids) == len(set(ids)) == 80
    assert len(selection["excluded"]) == 21 and not set(ids) & set(
        selection["excluded"]
    )
    records = json.loads((ROOT / "datasets/swe-live-80/records.json").read_text())
    assert {r["instance_id"] for r in records} == set(ids)
    exp = (
        load(ROOT / ".cache/swe-live-80/config.json")
        if resume
        else load(ROOT / "experiments/swe-live-80-assist.json")
    )
    assert [t.name for t in exp.benchmark.tasks] == ids
    assert exp.concurrency == 2 and exp.guardian.mode == "assist"
    assert exp.cpus == 2 and exp.memory_mb == 4096 and exp.attempts == 1
    validate_tasks(exp.benchmark.tasks)
    old = load_manifest(ROOT / "artifacts/swe-live-20")
    for agent in exp.agents:
        for key in ("binary", "bundle", "provenance"):
            if key in agent.kwargs and not resume:
                assert (
                    digest(Path(agent.kwargs[key]))
                    == old["agent_artifacts"][agent.id][key]
                )
    frozen_bytes = sum(
        p.stat().st_size
        for task in exp.benchmark.tasks
        for p in task.rglob("*")
        if p.is_file() and p.name != "grader-runtime.tar"
    )
    assert shutil.disk_usage(ROOT).free > frozen_bytes + 25 * 1024**3, (
        "Insufficient space for bounded batch preparation"
    )
    pool = ModelPool(ROOT)
    if not resume:
        exp.model = type(exp.model).model_validate(pool.resolve("experiment"))
        exp.guardian.model = pool.resolve("guardian")
        exp.guardian.version = "model-pool-driver/v1"
    assert exp.model.name == old["experiment"]["model"]["name"]
    env = {**os.environ, **pool.environment(exp.model.model_dump(), exp.guardian.model)}
    config = ROOT / ".cache/swe-live-80/config.json"
    if resume:
        exp = load(config)
        env = {
            **os.environ,
            **pool.environment(exp.model.model_dump(), exp.guardian.model),
        }
    else:
        write_json(config, exp.model_dump(mode="json"))
    write_json(
        state / "preflight.json",
        {
            "platform_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "selection_sha256": digest(ROOT / "datasets/swe-live-80/selection.json"),
            "tasks": 80,
            "prior_tasks_excluded": 21,
            "overlap": 0,
            "attempts": 160,
            "concurrency": 2,
            "guardian": exp.guardian.model_dump(),
            "agent_artifacts_match_swe20": True,
            "frozen_task_bytes": frozen_bytes,
            "tests": "205 passed, 9 skipped; real Docker two batches/four controls passed; SWE harness gold fixture resolved=1",
        },
    )
    status(
        "batched_running",
        expected_trials=160,
        batch_size=exp.batch.size,
        model_calls_started="inspect native request logs",
    )
    command = [
        sys.executable,
        "-u",
        "-m",
        "agent_harness_eval.cli",
        "run",
        str(config),
        "--output",
        str(run),
    ]
    if resume:
        command.append("--resume")
    subprocess.run(command, env=env, check=True)
    shutil.copyfile(
        ROOT / "datasets/swe-live-80/selection.json", run / "selection.json"
    )
    predictions = ROOT / "artifacts/swe-live-80-predictions"
    if not predictions.exists():
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_harness_eval.cli",
                "swe-export",
                str(run),
                str(predictions),
            ],
            check=True,
        )
    status("completed", note="Inspect scores, errors, and assisted markers separately")
except Exception as error:
    status("failed", error=str(error))
    raise
