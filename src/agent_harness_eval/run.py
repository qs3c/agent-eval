import asyncio
import importlib.metadata
import os
import shutil
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from harbor.job import Job
from harbor.models.job.config import JobConfig, RetryConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig

from .config import Experiment
from .provenance import digest, tree_manifest, write_json
from .report import report


async def run_experiment(exp: Experiment, output: Path):
    if exp.model.mode == "live":
        if exp.model.spending != "unbounded":
            raise ValueError("live mode requires explicit spending policy: unbounded")
        if exp.model.api_base != "https://api.deepseek.com":
            raise ValueError("live relay currently supports only official DeepSeek")
        if not os.environ.get(exp.model.api_key_env):
            raise ValueError("live API key environment variable is missing")
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
    output.mkdir(parents=True, exist_ok=False)
    copied_tasks = []
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
    for task in exp.benchmark.tasks:
        target = output / "tasks" / task.name
        shutil.copytree(task, target, symlinks=True)
        manifest["tasks"][task.name] = tree_manifest(target)
        copied_tasks.append(TaskConfig(path=target))
    artifact_paths = {}
    for agent in exp.agents:
        artifact_paths[agent.id] = {}
        manifest["agent_artifacts"][agent.id] = {}
        for key in ("bundle", "binary", "provenance"):
            if key in agent.kwargs:
                source = Path(agent.kwargs[key])
                target = output / "agent-artifacts" / agent.id / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                artifact_paths[agent.id][key] = str(target)
                manifest["agent_artifacts"][agent.id][key] = digest(target)
    write_json(output / "manifest.json", manifest)
    # Separate named jobs preserve agent identity even when two configs use the same adapter.
    try:
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
            job = await Job.create(cfg)
            await job.run()
    finally:
        report(output)


def execute(exp, output):
    asyncio.run(run_experiment(exp, output.resolve()))
