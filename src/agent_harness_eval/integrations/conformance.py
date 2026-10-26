"""Evidence-based levels. A recipe declaration alone never earns a level."""

import json
from collections import Counter
from pathlib import Path

from ..provenance import digest, write_json
from ..workspace.models import identity
from .store import verify


def check(release_directory, experiment_directory):
    release_directory, experiment_directory = Path(release_directory), Path(experiment_directory)
    receipt = verify(release_directory)
    release_id = receipt["release_id"]
    manifest_path = experiment_directory / "manifest.json"
    def contained(path):
        if path.is_symlink() or not path.resolve().is_relative_to(experiment_directory.resolve()):
            raise ValueError("Conformance evidence escapes experiment")
        return path
    contained(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    agents = [agent for agent in manifest["experiment"]["agents"]
              if agent["implementation"] == "agent_harness_eval.agents.recipe:Recipe" and agent["version"] == release_id]
    if len(agents) != 1:
        raise ValueError("Conformance experiment must bind exactly one matching release")
    agent = agents[0]
    records = sorted((experiment_directory / "jobs" / agent["id"]).glob("*/result.json"))
    expected = len(manifest["experiment"]["benchmark"]["tasks"]) * manifest["experiment"]["attempts"]
    expected_tasks = {Path(task).name: manifest["experiment"]["attempts"] for task in manifest["experiment"]["benchmark"]["tasks"]}
    observed_tasks = Counter()
    issues, evidence, traces, calls = [], {"manifest.json": digest(manifest_path)}, [], []
    if len(records) != expected or expected < 2:
        issues.append("至少需要两次完整的独立隔离尝试")
    for path in records:
        contained(path)
        result = json.loads(path.read_text())
        from ..report import task_key

        observed_tasks[task_key(result)] += 1
        evidence[str(path.relative_to(experiment_directory))] = digest(path)
        if not result.get("finished_at") or result.get("exception_info"):
            issues.append("存在未完成或异常尝试：" + path.parent.name)
        provenance = contained(path.parent / "agent/provenance.json")
        response = contained(path.parent / "agent/agent-result.json")
        if not provenance.is_file() or digest(provenance) != digest(release_directory / "provenance.json"):
            issues.append("缺少匹配 Release 的尝试来源证据")
        if not response.is_file() or json.loads(response.read_text()).get("status") != "completed":
            issues.append("缺少正常结束的标准 Agent 响应")
        for file in (provenance, response):
            if file.is_file():
                evidence[str(file.relative_to(experiment_directory))] = digest(file)
        events = contained(path.parent / "agent/events.jsonl")
        wire = contained(path.parent / "agent/wire_requests.jsonl")
        native = [json.loads(line) for line in events.read_text().splitlines() if line.strip()] if events.is_file() else []
        types = {event.get("type") for event in native if isinstance(event, dict)}
        traces.append({"tool_call", "tool_result"} <= types and (
            "model_request" in types or wire.is_file() and bool(wire.read_text().strip())))
        calls.append(wire.is_file() and bool(wire.read_text().strip()))
        for file in (events, wire):
            if file.is_file():
                evidence[str(file.relative_to(experiment_directory))] = digest(file)
    if dict(observed_tasks) != expected_tasks:
        issues.append("任务与重复次数不匹配，不允许用重复题目补足记录数")
    runnable = not issues
    result = {"schema_version": 1, "release_id": release_id, "evidence": evidence,
        "evidence_root": str(experiment_directory.resolve()),
        "levels": {
            "runnable": {"passed": runnable, "reasons": sorted(set(issues))},
            "comparable": {"passed": False, "reasons": ["尚缺完整模型角色和网络策略覆盖验证；已记录调用不代表没有旁路调用"]},
            "diagnosable": {"passed": runnable and bool(traces) and all(traces),
                            "reasons": [] if runnable and traces and all(traces) else ["缺少模型请求、原生工具调用/结果或运行证据"]}},
        "observed_model_calls": bool(calls) and all(calls),
        "limitations": ["Runnable 仅证明本次任务和运行环境的协议执行；不保证任意 benchmark 可用",
                        "取消、超时和资源清理由平台契约控制，单次 smoke 不替代这些专项验收"]}
    result["check_id"] = identity(result)
    destination = release_directory / "checks" / (result["check_id"] + ".json")
    if destination.exists():
        if json.loads(destination.read_text()) != result:
            raise ValueError("Existing conformance evidence was modified")
    else:
        write_json(destination, result)
    return result
