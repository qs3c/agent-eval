"""Explicit validation levels; a targeted pass is not benchmark improvement."""
from ..workspace.models import identity

LEVELS = ("build", "targeted", "benchmark-replay", "holdout")


def report(*, parent_commit, candidate_commit, recipe_digest, model_policy_digest, levels, required=("build", "targeted", "benchmark-replay")):
    if set(levels) - set(LEVELS) or set(required) - set(LEVELS):
        raise ValueError("Unknown validation level")
    issues = []
    for name in required:
        value = levels.get(name, {})
        if value.get("status") != "passed" or value.get("issues"):
            issues.append(name + " 未通过或证据不足")
        if value.get("parent_commit") != parent_commit or value.get("candidate_commit") != candidate_commit:
            issues.append(name + " 源码版本与当前候选不一致")
        if not value.get("evidence"):
            issues.append(name + " 缺少证据引用")
        if name == "holdout" and value.get("assessment") not in {"improved", "equal"}:
            issues.append("holdout 退化或无法比较")
    replay = levels.get("benchmark-replay", {})
    if "holdout" in levels and levels["holdout"].get("assessment") not in {"improved", "equal"}:
        issues.append("已执行 holdout 退化或无法比较")
    improved = not issues and replay.get("assessment") == "improved"
    value = {"parent_commit": parent_commit, "candidate_commit": candidate_commit,
             "recipe_digest": recipe_digest, "model_policy_digest": model_policy_digest,
             "levels": levels, "required": list(required), "issues": issues,
             "promotable": improved, "assessment": "improved" if improved else "inconclusive"}
    return {**value, "validation_id": identity(value)}


def current(record, value):
    return value.get("parent_commit") == record["parent_commit"] and value.get("candidate_commit") == record["commit"]


async def targeted_side(profile, workspace, output):
    from .process import run
    from ..guardian.evidence import read, write
    from ..operations.resources import Request, async_reservation
    import asyncio

    if not profile.targeted_reproducer:
        return {"status": "not_run", "reason": "未配置同条件正负对照"}
    result = {}
    for name, command in (("reproducer", profile.targeted_reproducer), ("negative", profile.targeted_negative_control)):
        directory = output / name
        directory.mkdir(parents=True)
        async with asyncio.timeout(command.timeout_seconds), async_reservation(Request("build", 1, 512)):
            executed = await run(command, workspace, directory / "process.json", {"workspace": workspace, "output": directory})
        value = read(directory / "assertion.json", {})
        if value.get("status") not in {"passed", "assertion_failed"} or not value.get("assertion") or executed.get("truncated") or executed["returncode"] != (0 if value.get("status") == "passed" else 1):
            value = {"status": "error", "reason": "缺少匹配退出码与明确断言的证据；构建失败不能证明复现"}
        result[name] = value
    write(output / "results.json", result)
    return result


def targeted_passed(results):
    return (results.get("parent", {}).get("reproducer", {}).get("status") == "assertion_failed"
            and results.get("candidate", {}).get("reproducer", {}).get("status") == "passed"
            and all(results.get(side, {}).get("negative", {}).get("status") == "passed" for side in ("parent", "candidate")))
