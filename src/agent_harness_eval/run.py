import asyncio
import fcntl
import importlib.metadata
import json
import os
import shutil
import signal
import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from harbor.job import Job
from harbor.models.job.config import JobConfig, RetryConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig

from .config import Experiment
from .freezing import freeze_tree
from .manifests import compact_manifest, verify_manifest
from .provenance import digest, tree_manifest, write_json
from .report import report


def verify_workbench_repair(previous, current, approval):
    """Allow only an explicitly inventoried infrastructure repair during resume."""
    if approval is None:
        raise ValueError("Resume workbench code differs from the frozen experiment")
    repair = json.loads(Path(approval).read_text())
    changed = {name for name in previous.keys() | current.keys() if previous.get(name) != current.get(name)}
    allowed = {"batching.py", "benchmarks/swe.py", "run.py", "cli.py"}
    if (
        repair.get("schema_version") != 1
        or not isinstance(repair.get("reason"), str)
        or not repair["reason"].strip()
        or repair.get("previous") != previous
        or repair.get("current") != current
        or not changed.issubset(allowed)
        or previous.keys() != current.keys()
    ):
        raise ValueError("Resume repair inventory mismatch or non-infrastructure code changed")
    return repair


def resume_configuration(value):
    """Normalize only fields declared as sets, retaining every other value."""
    return {
        **value,
        "agents": [{**agent, "capabilities": sorted(agent["capabilities"])} for agent in value["agents"]],
        "benchmark": {**value["benchmark"], "requires": sorted(value["benchmark"]["requires"])},
    }


async def run_experiment(
    exp: Experiment, output: Path, *, manage_signals=True, resume=False,
    workbench_repair: Path | None = None,
):
    from .search import validate_search_credentials
    from .benchmarks.registry import resolve

    resolve(exp.benchmark, batch=exp.batch).validate(exp.benchmark)

    if workbench_repair is not None and not resume:
        raise ValueError("Workbench repair requires --resume")
    validate_search_credentials(exp)
    if exp.guardian is not None:
        from .guardian.config import validate_tasks

        if sys.platform != "linux":
            raise ValueError("Experiment guardian execution requires Linux and Docker")
        validate_tasks(exp.benchmark.tasks)
    if exp.model.mode == "live":
        if exp.model.spending != "unbounded":
            raise ValueError("live mode requires explicit spending policy: unbounded")
        from .model_pool import validate_endpoint

        validate_endpoint(exp.model.api_base)
        if any(a.id in {"dsh", "dsh-standard"} for a in exp.agents) and not (
            exp.model.messages_base or exp.model.api_base == "https://api.deepseek.com"
        ):
            raise ValueError("DSH 实验需要 Messages 接口地址")
        if not os.environ.get(exp.model.api_key_env):
            raise ValueError("live API key environment variable is missing")
        if exp.model.search_review and not os.environ.get(exp.model.search_review.api_key_env):
            raise ValueError("search review API key environment variable is missing")
    adapters = {}
    for agent in exp.agents:
        if ":" in agent.implementation:
            module, name = agent.implementation.split(":", 1)
            adapter = getattr(import_module(module), name)
            if exp.model.mode not in getattr(adapter, "supported_model_modes", ()):
                raise ValueError(
                    f"{agent.id}: adapter must declare supported_model_modes including {exp.model.mode}"
                )
            adapters[agent.id] = adapter
        elif (
            agent.implementation not in ("nop", "oracle") or exp.model.mode != "offline"
        ):
            raise ValueError(
                "built-in model agents need an adapter declaring the experiment model contract"
            )
    if resume:
        if exp.batch is None:
            raise ValueError("Resume requires an explicitly batched experiment")
        existing = verify_manifest(output)
        # Capabilities/requires are sets. Their JSON list order can change across
        # Python processes; normalize only sets without weakening ordered tasks.
        if resume_configuration(existing["experiment"]) != resume_configuration(exp.model_dump(mode="json")):
            raise ValueError("Resume configuration differs from the frozen experiment")
        current_code = {
            str(p.relative_to(Path(__file__).parent)): digest(p)
            for p in sorted(Path(__file__).parent.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".mjs"}
        }
        previous_code = json.loads(
            (output / existing["workbench"]["inventory"]["path"]).read_text()
        )
        if current_code != previous_code or workbench_repair is not None:
            repair = verify_workbench_repair(previous_code, current_code, workbench_repair)
            # Append evidence separately; never rewrite the frozen inventory.
            recovery = output / "recovery" / ("workbench-" + digest(Path(workbench_repair)))
            write_json(recovery / "repair.json", repair)
            for name, sha in current_code.items():
                if sha != previous_code[name]:
                    destination = recovery / "code" / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(Path(__file__).parent / name, destination)
        for name, sha in existing["workbench"].get("preparation_scripts", {}).items():
            if digest(Path(__file__).resolve().parents[2] / "scripts" / name) != sha:
                raise ValueError("Resume preparation script changed: " + name)
        for name, entry in existing["tasks"].items():
            backup = output / ".preparation-backups" / name
            if backup.exists():
                target = output / "tasks" / name
                if tree_manifest(target)["sha256"] != entry["sha256"]:
                    if tree_manifest(backup)["sha256"] != entry["sha256"]:
                        raise ValueError(
                            "Preparation rollback does not match frozen input"
                        )
                    if target.exists():
                        shutil.rmtree(target)
                    freeze_tree(backup, target, output / "shared-runtime")
                shutil.rmtree(backup)
    else:
        output.mkdir(parents=True, exist_ok=False)
    copied_tasks = []
    task_hash_cache = {}
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "harbor": importlib.metadata.version("harbor"),
        "workbench": {
            "version": importlib.metadata.version("agent-harness-eval"),
            "files": {
                str(path.relative_to(Path(__file__).parent)): digest(path)
                for path in sorted(Path(__file__).parent.rglob("*"))
                if path.is_file() and path.suffix in {".py", ".mjs"}
            },
        },
        "experiment": exp.model_dump(mode="json"),
        "tasks": {},
        "agent_artifacts": {},
        "evidence_kind": "deterministic control"
        if exp.model.mode == "offline"
        else (
            "live official DeepSeek; unbounded spending"
            if exp.model.mode == "live"
            else "mock provider; not model performance"
        ),
    }
    if resume:
        manifest = existing
    elif exp.batch and exp.batch.prepare_swe:
        manifest["workbench"]["preparation_scripts"] = {
            name: digest(Path(__file__).resolve().parents[2] / "scripts" / name)
            for name in ["pin-swe-runtime.py", "materialize-grader.py"]
        }
    if not resume:
        from .policies.experiment import fingerprint

        manifest["policies"] = fingerprint(manifest["experiment"])
    for task in exp.benchmark.tasks:
        target = output / "tasks" / task.name
        if resume:
            inventory = tree_manifest(target, hash_cache=task_hash_cache)
            if inventory["sha256"] != manifest["tasks"][task.name]["sha256"]:
                raise ValueError("Frozen task changed before resume: " + task.name)
            copied_tasks.append(TaskConfig(path=target))
            continue
        # A cache scoped to this freeze reuses hashes only for the same unchanged
        # inode (e.g. shared prepared runtime archives), never by name or version.
        inventory = tree_manifest(task, hash_cache=task_hash_cache)
        freeze_tree(task, target, output / "shared-runtime")
        # Detect source changes during copying, including additions/deletions.
        if inventory != tree_manifest(task, hash_cache=task_hash_cache):
            raise ValueError(f"Task changed while freezing: {task}")
        runtime = target / "runtime-provenance.json"
        if runtime.is_file():
            identity = json.loads(runtime.read_text())
            inventory["environment"] = {"base_image": identity.get("image")}
            grader = target / "grader-provenance.json"
            if grader.is_file():
                inventory["environment"]["runtime_archive_sha256"] = json.loads(
                    grader.read_text()
                ).get("archive_sha256")
        manifest["tasks"][task.name] = inventory
        copied_tasks.append(TaskConfig(path=target))
    if not resume and exp.batch:
        source_selection = exp.benchmark.tasks[0].parent / "selection.json"
        if source_selection.is_file():
            shutil.copyfile(source_selection, output / "selection.json")
            manifest["selection"] = {
                "path": "selection.json",
                "sha256": digest(output / "selection.json"),
            }
    artifact_paths = {}
    for agent in exp.agents:
        artifact_paths[agent.id] = {}
        if not resume:
            manifest["agent_artifacts"][agent.id] = {}
        for key in ("bundle", "binary", "provenance"):
            if key in agent.kwargs:
                source = Path(agent.kwargs[key])
                target = output / "agent-artifacts" / agent.id / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if resume:
                    if (
                        not target.is_file()
                        or digest(target) != manifest["agent_artifacts"][agent.id][key]
                    ):
                        raise ValueError("Frozen Agent artifact changed before resume")
                else:
                    shutil.copyfile(source, target)
                if key == "provenance" and not resume:
                    identity = json.loads(source.read_text())
                    if identity.get("changes_directory"):
                        changes = source.parent / identity["changes_directory"]
                        if (
                            changes.is_symlink()
                            or changes.parent.resolve() != source.parent.resolve()
                        ):
                            raise ValueError(
                                "Source evidence directory escapes provenance"
                            )
                        for name, expected in identity["changes"].items():
                            evidence = changes / name
                            if (
                                evidence.is_symlink()
                                or evidence.parent.resolve() != changes.resolve()
                                or digest(evidence) != expected
                            ):
                                raise ValueError("Source change evidence hash mismatch")
                        shutil.copytree(changes, target.parent / changes.name)
                artifact_paths[agent.id][key] = str(target)
                manifest["agent_artifacts"][agent.id][key] = digest(target)
    if not resume:
        write_json(output / "manifest.json", compact_manifest(output, manifest))
    # Separate named jobs preserve agent identity even when two configs use the same adapter.
    try:
        agent_configs = {}
        for agent in exp.agents:
            kw = {**agent.kwargs, **artifact_paths[agent.id], "version": agent.version}
            if getattr(adapters.get(agent.id), "accepts_model_config", False):
                kw.update(
                    model_config=exp.model.model_dump(),
                    timeout_seconds=exp.timeout_seconds,
                )
            selector = (
                {"import_path": agent.implementation}
                if ":" in agent.implementation
                else {"name": agent.implementation}
            )
            cfg = JobConfig(
                job_name=agent.id,
                jobs_dir=output / "jobs",
                tasks=copied_tasks,
                n_attempts=exp.attempts,
                n_concurrent_trials=exp.concurrency,
                retry=RetryConfig(max_retries=0),
                quiet=True,
                environment=EnvironmentConfig(
                    type="docker",
                    delete=True,
                    override_cpus=exp.cpus,
                    override_memory_mb=exp.memory_mb,
                ),
                agents=[
                    AgentConfig(
                        **selector,
                        model_name=exp.model.name,
                        kwargs=kw,
                        override_timeout_sec=exp.timeout_seconds,
                        override_setup_timeout_sec=exp.setup_timeout_seconds,
                    )
                ],
            )
            agent_configs[agent.id] = cfg.agents[0]
            if exp.batch is not None:
                continue
            if exp.guardian is not None:
                from .guardian.supervisor import run_guarded

                await run_guarded(
                    exp,
                    output,
                    cfg.agents[0],
                    copied_tasks,
                    agent.id,
                    manage_signals=manage_signals,
                )
                continue
            job = await Job.create(cfg)
            admission = None
            if os.environ.get("HARNESS_OPERATION_ID"):
                from .operations.worker import Admission

                admission = Admission()
                job.on_trial_started(admission.started)
                job.on_trial_ended(admission.ended)
            try:
                await job.run()
            finally:
                if admission is not None:
                    await admission.cleanup_native()
        if exp.batch is not None:
            from .batching import run_batches

            await run_batches(
                exp,
                output,
                copied_tasks,
                agent_configs,
                resume=resume,
                manage_signals=manage_signals,
            )
    finally:
        report(output)


def execute(exp, output, *, resume=False, workbench_repair=None):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with (output.parent / ("." + output.name + ".run.lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        async def owned_run():
            loop, owner = asyncio.get_running_loop(), asyncio.current_task()
            previous = signal.getsignal(signal.SIGTERM)
            loop.add_signal_handler(signal.SIGTERM, owner.cancel)
            try:
                await run_experiment(exp, output, resume=resume, workbench_repair=workbench_repair,
                                     manage_signals=False)
            finally:
                loop.remove_signal_handler(signal.SIGTERM)
                signal.signal(signal.SIGTERM, previous)

        asyncio.run(owned_run())
