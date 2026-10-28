"""Import pinned upstream Terminal-Bench tasks without rewriting their verifier."""

import copy
import importlib.metadata
import json
import random
import re
import shutil
import tempfile
from pathlib import Path

from harbor.models.task.config import TaskConfig
from harbor.models.task.id import GitTaskId, PackageTaskId
from harbor.models.task.verifier_mode import task_has_any_separate_verifier
from harbor.registry.client.factory import RegistryClientFactory
from harbor.registry.client.package import PackageDatasetClient
from harbor.tasks.client import TaskClient

from ..config import Experiment, load
from ..provenance import tree_manifest, write_json


def pinned_reference(reference: str) -> tuple[str, str]:
    name, separator, version = reference.rpartition("@")
    if (
        not separator
        or name not in {"terminal-bench", "terminal-bench/terminal-bench"}
        or not re.fullmatch(
            r"(?:[0-9]+\.[0-9]+(?:\.[0-9]+)?|sha256:[a-f0-9]{64})", version
        )
    ):
        raise ValueError(
            "需固定官方 Terminal-Bench 版本，例如 terminal-bench@2.0 或 terminal-bench/terminal-bench@4.0.0；不接受 latest/head"
        )
    return name, version


def task_name(identity) -> str:
    name = identity.name if isinstance(identity, PackageTaskId) else identity.path.name
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError(f"无效任务目录名：{name}")
    return name


def immutable_task(identity):
    if isinstance(identity, GitTaskId):
        valid = re.fullmatch(
            r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", identity.git_commit_id or ""
        )
    else:
        valid = isinstance(identity, PackageTaskId) and re.fullmatch(
            r"sha256:[a-f0-9]{64}", identity.ref or ""
        )
    if not valid:
        raise ValueError(
            "上游任务必须固定到 Git commit 或内容哈希，不能使用可变分支或本地路径"
        )


def inspect_task(path: Path) -> tuple[TaskConfig, dict]:
    for name in ("task.toml", "instruction.md", "tests/test.sh"):
        if not (path / name).is_file():
            raise ValueError(f"Terminal-Bench 任务缺少 {name}：{path.name}")
    config = TaskConfig.model_validate_toml((path / "task.toml").read_text())
    # Also rejects symlinks, matching the runner's immutable task contract.
    inventory = tree_manifest(path)
    return config, inventory


def experiment(
    template: Experiment,
    paths: list[Path],
    reference: str,
    revision: str,
    *,
    direct_job=False,
) -> Experiment:
    result = template.model_dump(mode="json")
    result["name"] = "terminal-bench-" + re.sub(
        r"[^a-z0-9_-]", "-", reference.split("@", 1)[1]
    )
    result["benchmark"] = {
        "id": "terminal-bench",
        "tasks": [str(path) for path in paths],
        "requires": ["shell", "filesystem"],
        "primary_metric": "reward",
        "score_kind": "resolved",
        "dataset": {
            "name": reference.split("@", 1)[0],
            "revision": revision,
            "split": "test",
        },
        "evaluation": {
            "name": "Terminal-Bench upstream task verifier",
            "version": reference.split("@", 1)[1],
            "environment": "Harbor Docker; original task.toml resource and timeout limits; task files hashed in selection.json",
        },
    }
    # Null means let Harbor use the per-task values, rather than SWE defaults.
    result["cpus"] = result["memory_mb"] = result["timeout_seconds"] = None
    if result["batch"] is not None:
        result["batch"]["prepare_swe"] = False
        result["batch"]["official_regrade"] = False
    if result["guardian"] is not None:
        from ..guardian.config import GuardianConfig

        result["guardian"] = GuardianConfig(mode="observe").model_dump(mode="json")
    # Harbor supports isolated verifiers; our single-container Guardian does not.
    if direct_job:
        result["batch"] = result["guardian"] = None
    # SWE anti-answer-search policy is not a validated Terminal-Bench policy.
    result["model"]["search_review"] = None
    return Experiment.model_validate(result)


async def prepare(
    reference: str,
    destination: Path,
    template: Experiment,
    *,
    config_output: Path | None = None,
    names: list[str] | None = None,
    count: int | None = None,
    seed: int = 20261017,
    cpu_only: bool = False,
) -> dict:
    """Download official immutable task IDs, validate, then publish a frozen selection."""
    registry_name, _ = pinned_reference(reference)
    destination = destination.resolve()
    config_output = (config_output or destination / "experiment.json").resolve()
    if destination.exists() or config_output.exists():
        raise FileExistsError(
            "目标已存在；请选择新目录和配置路径，禁止覆盖已有实验输入"
        )
    if (
        config_output.is_relative_to(destination)
        and config_output != destination / "experiment.json"
    ):
        raise ValueError("目录内配置路径必须为 experiment.json")
    if count is not None and count < 1:
        raise ValueError("count 必须为正整数")
    if names and count is not None:
        raise ValueError("--task 和 --count 不能同时使用")
    if names and len(names) != len(set(names)):
        raise ValueError("--task 不可重复")
    client = (
        PackageDatasetClient()
        if "/" in registry_name
        else RegistryClientFactory.create()
    )
    metadata = await client.get_dataset_metadata(reference)
    if metadata.metrics or metadata.files:
        raise ValueError(
            "该版本包含数据集级评分文件，当前仅支持原生逐题 reward；不能静默忽略评分文件"
        )
    identities = {}
    for identity in metadata.task_ids:
        immutable_task(identity)
        name = task_name(identity)
        if name in identities:
            raise ValueError(f"任务目录名重复：{name}")
        identities[name] = identity
    if not identities:
        raise ValueError("上游数据集为空")
    if names:
        missing = set(names) - identities.keys()
        if missing:
            raise ValueError("上游不存在这些任务：" + ", ".join(sorted(missing)))
        candidates = sorted(names)
    else:
        candidates = sorted(identities)
    # CPU filtering happens before sampling, so a requested count stays exact.
    if not cpu_only and count is not None:
        if count > len(candidates):
            raise ValueError("count 超过上游可选任务数")
        candidates = sorted(random.Random(seed).sample(candidates, count))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".terminal-bench-", dir=destination.parent
    ) as temporary:
        temporary = Path(temporary)
        downloaded = await TaskClient().download_tasks(
            task_ids=[identities[name] for name in candidates],
            output_dir=temporary / "download",
            export=True,
        )
        if len(downloaded.results) != len(candidates):
            raise ValueError("Harbor 下载返回的任务数与请求不一致")
        available, excluded = {}, {}
        for name, result in zip(candidates, downloaded.results, strict=True):
            config, inventory = inspect_task(result.path)
            environments = [config.environment]
            if config.verifier.environment is not None:
                environments.append(config.verifier.environment)
            if any(env.gpus or env.tpu is not None for env in environments):
                if not cpu_only:
                    raise ValueError(
                        f"{name} 需要 GPU/TPU；当前工作台仅支持本地 Docker CPU。显式 --cpu-only 可生成标记过的 CPU 子集"
                    )
                excluded[name] = "任务需要 GPU/TPU；显式 CPU 子集筛选"
                continue
            if config.environment.os.value != "linux":
                raise ValueError(f"{name} 需要非 Linux 环境，当前工作台不支持")
            if config.steps:
                raise ValueError(
                    f"{name} 使用多阶段任务，当前工作台接入不支持；未修改上游任务"
                )
            available[name] = (result.path, config, inventory)
        chosen = sorted(available)
        if cpu_only and count is not None:
            if count > len(chosen):
                raise ValueError("count 超过 CPU 可选任务数")
            chosen = sorted(random.Random(seed).sample(chosen, count))
        if not chosen:
            raise ValueError("筛选后没有可运行的 Terminal-Bench 任务")
        direct_job = any(
            task_has_any_separate_verifier(available[name][1]) for name in chosen
        )
        stage = temporary / "dataset"
        stage.mkdir()
        records = {}
        for name in chosen:
            source, config, inventory = available[name]
            shutil.copytree(source, stage / name)
            if tree_manifest(stage / name) != inventory:
                raise ValueError("复制过程中上游任务发生变化：" + name)
            records[name] = {
                "upstream": identities[name].model_dump(mode="json"),
                "sha256": inventory["sha256"],
                "files": inventory["files"],
                "environment": config.environment.model_dump(mode="json"),
                "agent_timeout_sec": config.agent.timeout_sec,
                "verifier_timeout_sec": config.verifier.timeout_sec,
            }
        selection = {
            "schema_version": 1,
            "benchmark": "terminal-bench",
            "requested": reference,
            "upstream": metadata.model_dump(mode="json"),
            "harbor_version": importlib.metadata.version("harbor"),
            "seed": seed,
            "cpu_only": cpu_only,
            "upstream_task_count": len(identities),
            "selected_task_count": len(chosen),
            "full_dataset": len(chosen) == len(identities),
            "excluded": excluded,
            "tasks": records,
            "execution_mode": "harbor-job-separate-verifier"
            if direct_job
            else "template",
            "limitations": [
                "本记录只证明下载与配置准备；不代表 Agent 已运行或得到成绩",
                "子集成绩不可直接与完整榜单比较",
                "Git/包任务标识及任务文件已固定；镜像标签与外部下载依赖可能变化",
                "SWE 搜索审查策略未沿用；本实验不据此声称消除了答案泄漏",
            ],
        }
        write_json(stage / "selection.json", selection)
        revision = metadata.version or reference.split("@", 1)[1]
        exp = experiment(
            template,
            [destination / name for name in chosen],
            reference,
            revision,
            direct_job=direct_job,
        )
        config = exp.model_dump(mode="json")
        # Validate with the real task files before publishing, without executing models.
        validation = copy.deepcopy(config)
        validation["benchmark"]["tasks"] = [str(stage / name) for name in chosen]
        write_json(temporary / "validate.json", validation)
        load(temporary / "validate.json")
        if destination.exists() or config_output.exists():
            raise FileExistsError("下载期间目标已被创建；未覆盖")
        config_output.parent.mkdir(parents=True, exist_ok=True)
        if config_output == destination / "experiment.json":
            write_json(stage / "experiment.json", config)
            stage.rename(destination)
        else:
            # Reserve the external template path before publishing either output.
            with config_output.open("x") as output:
                try:
                    stage.rename(destination)
                    output.write(
                        json.dumps(config, ensure_ascii=False, indent=2) + "\n"
                    )
                except BaseException:
                    config_output.unlink()
                    raise
    return {
        "dataset": str(destination),
        "config": str(config_output),
        "selected": len(chosen),
        "upstream": len(identities),
        "full_dataset": selection["full_dataset"],
    }
