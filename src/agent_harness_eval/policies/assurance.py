import json
from pathlib import Path

from .model import freeze


def assess(output, experiment):
    root = Path(output).resolve()
    expected = len(experiment["benchmark"]["tasks"]) * experiment["attempts"]
    model = experiment.get("model", {})
    policy = freeze(model) if model.get("policy") else None
    agents = {}
    for agent in experiment["agents"]:
        reasons, reports = set(), []
        for path in (root / "jobs" / agent["id"]).glob("*/agent/model-policy-report.json"):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                reasons.add("策略证据路径越界")
                continue
            try:
                report = json.loads(path.read_text())
                if policy is None or report["policy_digest"] != policy.digest:
                    raise ValueError("Policy mismatch")
                reports.append(report)
                reasons.update(report["reasons"])
            except (OSError, ValueError, KeyError):
                reasons.add("策略证据缺失或不匹配")
        if not policy:
            reasons.add("历史或兼容路径未启用冻结模型策略")
        if len(reports) != expected:
            reasons.add("模型策略证据尚未覆盖全部计划尝试")
        # A report from a normal attempt does not prove that no other model
        # route exists. No current native profile earns this coverage claim.
        reasons.add("完整子模型路径与网络旁路覆盖尚未认证")
        agents[agent["id"]] = {"comparable": False, "observed_consistent": len(reports) == expected and all(
            report.get("observed_consistent") is True for report in reports), "expected": expected,
            "recorded": len(reports), "reasons": sorted(reasons)}
    return {"comparable": False, "policy_digest": policy.digest if policy else None, "agents": agents,
            "scope": "成绩可作描述性对照；配置一致和已观察调用一致不等于完整公平性认证"}
