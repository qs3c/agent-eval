"""Derive paired evaluation from immutable case evidence, including old candidates."""

import hashlib
import re
from pathlib import Path

from ..config import Experiment, load
from ..guardian.config import GuardianConfig
from ..guardian.evidence import read
from .executors import runtime
from .models import Command, Profile


def recipe_profile(profile):
    from .recipe_build import verify_recipe

    verify_recipe(profile.integration_recipe)
    profile.build = Command(argv=["{python}", str(Path(__file__).with_name("recipe_build.py")),
        "--recipe", str(profile.integration_recipe), "--workspace", "{workspace}", "--output", "{output}"], timeout_seconds=7200)
    profile.artifacts = {"bundle": "release/agent.tar", "provenance": "release/provenance.json"}
    return profile


def configuration(store, record):
    """Read only: return effective profile, experiment and historical task hashes."""
    profile = Profile.model_validate(record["profile"])
    if profile.evaluation:
        if profile.integration_recipe:
            recipe_profile(profile)
        return profile, load(profile.evaluation, validate_agent_artifacts=False), {}
    if record["profile_id"] not in {"builtin-bkcrab", "builtin-dsh"}:
        raise ValueError("执行配置中未指定复评模板")
    case = store.get("cases", record["case"])
    if case["agent"] != "bkcrab" and not profile.integration_recipe:
        raise ValueError("该目标 Agent 尚未配置源码构建方式，请使用自定义复评配置")
    evidence = store.path("cases", case["id"]) / "evidence"
    identity_path = evidence / "experiment.json"
    if (
        hashlib.sha256(identity_path.read_bytes()).hexdigest()
        != case["identity_sha256"]
    ):
        raise ValueError("冻结的原测试配置已变化，无法恢复复评配置")
    identity = read(identity_path)
    exp = Experiment.model_validate(identity["experiment"])
    exp.agents = [a for a in exp.agents if a.id == case["agent"]]
    if len(exp.agents) != 1:
        raise ValueError("原测试配置中找不到案例 Agent")
    required = set(exp.agents[0].kwargs) & {"binary", "bundle", "provenance"}
    if "bundle" in required and not profile.integration_recipe:
        raise ValueError("BKCrab 原测试包含不支持的 bundle 产物")
    # Select the case's recorded tasks, not every task in a larger source run.
    selected = set()
    known_tasks = {p.name for p in exp.benchmark.tasks}
    for trial in case["trials"]:
        source = f"jobs/{case['agent']}/{trial}/result.json"
        item = next((e for e in case["evidence"] if e.get("source") == source), {})
        if not item.get("file") or item.get("truncated"):
            raise ValueError("所选测试的原始结果缺失或被截断，无法确定复评题目")
        path = evidence / item["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("冻结的原始测试结果已变化")
        result = read(path)
        # Harbor may qualify task_name with a dataset namespace. task_id.path
        # identifies the submitted directory; use only its name, never follow it.
        name = result["task_name"]
        task_path = (result.get("task_id") or {}).get("path")
        directory_name = Path(task_path).name if task_path else None
        if directory_name in known_tasks:
            if name in known_tasks and name != directory_name:
                raise ValueError("原测试的任务名称与目录标识不一致")
            name = directory_name
        selected.add(name)
    exp.benchmark.tasks = [p for p in exp.benchmark.tasks if p.name in selected]
    if {p.name for p in exp.benchmark.tasks} != selected or not selected:
        raise ValueError("原测试配置缺少所选题目的路径")
    if any(not p.is_absolute() for p in exp.benchmark.tasks):
        raise ValueError("原测试配置缺少题目的绝对路径，无法可靠恢复")
    hashes = {
        p.name: identity["tasks"].get(p.name, {}).get("sha256")
        for p in exp.benchmark.tasks
    }
    if not all(hashes.values()):
        raise ValueError("原测试缺少题目哈希，无法确认复评输入一致")
    # Prefer the source run's retained task copies over mutable dataset inputs.
    exp.benchmark.tasks = [
        snapshot if (snapshot := Path(case["run"]) / "tasks" / p.name).is_dir() else p
        for p in exp.benchmark.tasks
    ]
    # Reevaluate both versions without model assistance, retaining deadline policy.
    guardian = exp.guardian.model_dump() if exp.guardian else {}
    guardian.update(mode="observe", command=[], version=None, pass_env=[], model=None)
    exp.guardian = GuardianConfig.model_validate(guardian)
    if profile.integration_recipe:
        profile.evaluation = store.path("candidates", record["id"]) / "evaluation-template.json"
        return recipe_profile(profile), exp, hashes
    argv = profile.adapter.argv
    image = runtime(store.project).get("image") or (
        argv[argv.index("--image") + 1] if "--image" in argv else ""
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("候选缺少固定的 Docker 构建镜像")
    profile.build = Command(
        argv=[
            "{python}",
            str(Path(__file__).with_name("native_build.py")),
            "--image",
            image,
            "--workspace",
            "{workspace}",
            "--output",
            "{output}",
        ],
        timeout_seconds=1860,
    )
    profile.artifacts = {"binary": "bkcrab-eval", "provenance": "provenance.json"}
    if record.get("paired_build"):
        profile.build = Command.model_validate(record["paired_build"]["build"])
        profile.artifacts = record["paired_build"]["artifacts"]
    profile.evaluation = (
        store.path("candidates", record["id"]) / "evaluation-template.json"
    )
    return profile, exp, hashes
