"""Create a new paired experiment from incidents; never resume or rewrite its source."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from agent_harness_eval.config import load
from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.manifests import load_manifest
from agent_harness_eval.model_pool import ModelPool
from agent_harness_eval.provenance import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    exp = load(args.source_config)
    exp.name = args.name
    # Validate before using a supplied experiment name as a directory component.
    exp = type(exp).model_validate(exp.model_dump())
    output = args.root / "artifacts" / args.name
    work = args.root / ".cache" / args.name
    if output.exists() or work.exists():
        raise ValueError(
            "New experiment already exists; no implicit rerun or overwrite"
        )
    manifest = load_manifest(args.source_run)
    candidates = []
    for trial in sorted((args.source_run / "jobs").glob("*/*")):
        if not trial.is_dir() or not list(
            (trial / "incidents").glob("*/incident.json")
        ):
            continue
        task = trial.name.rsplit("__", 1)[0]
        if not (args.source_run / "tasks" / task).is_dir():
            raise ValueError("Cannot resolve incident task: " + task)
        candidates.append(
            {"agent": trial.parent.name, "trial": trial.name, "task": task}
        )
    ids = sorted({r["task"] for r in candidates})
    if not ids:
        raise ValueError("No incident candidates")
    exp.benchmark.tasks = [args.source_run / "tasks" / task for task in ids]
    if {a.id for a in exp.agents} != {"bkcrab", "dsh-standard"}:
        raise ValueError("Expected the original BKCrab and DSH pair")
    for agent in exp.agents:
        for key in ("binary", "bundle", "provenance"):
            if key in agent.kwargs and (
                digest(Path(agent.kwargs[key]))
                != manifest["agent_artifacts"][agent.id][key]
            ):
                raise ValueError("Agent artifact changed: " + agent.id + "/" + key)
    exp.guardian.idle_seconds = 600
    exp.guardian.diagnostic_seconds = 300
    exp.guardian.version = "model-pool-driver/v2"
    exp.attempts = 1
    if exp.batch is None or exp.batch.size != 20:
        raise ValueError("Expected the approved bounded batch configuration")
    work.mkdir(parents=True)
    config = work / "config.json"
    write(config, exp.model_dump(mode="json"))
    selection = {
        "source_run": str(args.source_run),
        "source_platform_commit": manifest.get("platform"),
        "platform_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=args.root, text=True
        ).strip(),
        "source_incidents": candidates,
        "instance_ids": ids,
        "tasks": len(ids),
        "expected_trials": len(ids) * len(exp.agents),
        "policy": "两种 Agent 都执行历史异常题目的并集；原 harness 产物和模型配置保持一致；新实验独立保存。",
    }
    write(work / "selection.json", selection)
    if not args.execute:
        print(
            json.dumps(
                {
                    "config": str(config),
                    "tasks": len(ids),
                    "expected_trials": selection["expected_trials"],
                }
            )
        )
        return
    env = {
        **os.environ,
        **ModelPool(args.root).environment(exp.model.model_dump(), exp.guardian.model),
    }
    env["PYTHONPATH"] = str(args.root / "src")
    env["PATH"] = str(args.root / ".tools") + os.pathsep + env.get("PATH", "")
    with (work / "pipeline.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "agent_harness_eval.cli",
                "run",
                str(config),
                "--output",
                str(output),
            ],
            cwd=args.root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    write(
        work / "launch.json",
        {
            "pid": process.pid,
            "output": str(output),
            "tasks": len(ids),
            "expected_trials": selection["expected_trials"],
        },
    )
    print(json.dumps(read(work / "launch.json")))


if __name__ == "__main__":
    main()
