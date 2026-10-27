"""Reviewable local comparison conditions, without execution or model calls."""

import json
import shutil
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from .provenance import digest, tree_manifest
from .workspace.models import identity
from .policies.experiment import fingerprint


def validate_agents(config):
    artifacts, warnings = {}, []
    for agent in config["agents"]:
        artifacts[agent["id"]] = {}
        for key in ("binary", "bundle", "provenance"):
            if key in agent.get("kwargs", {}):
                path = Path(agent["kwargs"][key])
                if path.is_symlink() or not path.is_file():
                    raise ValueError("Agent 产物不存在或为符号链接：" + key)
                artifacts[agent["id"]][key] = digest(path)
        if agent["implementation"] == "agent_harness_eval.agents.recipe:Recipe":
            record = json.loads(Path(agent["kwargs"]["provenance"]).read_text())
            recipe = record["recipe"]
            if config["model"]["mode"] not in recipe["model_modes"]:
                raise ValueError(agent["id"] + " 不支持所选模型模式")
            if config["model"].get("policy") and recipe["model_protocol"] not in {"none", *config["model"]["policy"]["protocols"]}:
                raise ValueError(agent["id"] + " 的模型协议不被所选策略支持")
            warnings.extend(recipe.get("limitations", []))
    return artifacts, warnings


def preflight(console, config, selection=None):
    artifacts, warnings = validate_agents(config)
    cache = {}
    tasks = {Path(path).name: tree_manifest(Path(path), hash_cache=cache)["sha256"] for path in config["benchmark"]["tasks"]}
    policies = fingerprint(config)
    conditions = {"experiment": config, "selection": selection, "tasks": tasks, "artifacts": artifacts, "policies": policies}
    paid = config["model"]["mode"] == "live" or bool(config.get("guardian", {}).get("model") if config.get("guardian") else False)
    planned = len(tasks) * len(config["agents"]) * config["attempts"]
    free = shutil.disk_usage(console.root).free
    if config["model"]["mode"] == "live":
        blocked = [row["task"] for row in policies["network"]["tasks"] if row["mode"] == "no-network"]
        if blocked and not (config.get("batch") or {}).get("prepare_swe"):
            raise ValueError("所选任务禁止网络访问，无法调用真实模型：" + "、".join(blocked) + "。controls 用于离线/模拟模型检查；请选择已在 agent.allowed_hosts 放行模型服务的任务模板。")
        for endpoint in (config["model"].get("api_base"), config["model"].get("messages_base")):
            hostname = urlsplit(endpoint or "").hostname
            try:
                loopback = ipaddress.ip_address(hostname or "").is_loopback
            except ValueError:
                loopback = hostname == "localhost"
            if loopback:
                warnings.append("模型地址使用 localhost/回环地址：任务容器内它指向容器自身。宿主机模型服务需使用容器可达的宿主机地址，并监听对应接口；预检不会验证网络连接。")
    warnings.append("完整子模型与网络覆盖尚未认证；结果作为描述性对照，不自动授予 Comparable")
    if not config["model"].get("policy"):
        warnings.append("未启用固定模型参数策略；配置同一模型 ID 不代表全部调用已核验")
    if free < 2 * 1024**3:
        warnings.append("本机可用磁盘低于 2 GiB，实际执行可能被资源预算阻止")
    return {"digest": identity(conditions), **conditions, "planned_attempts": planned,
            "paid": paid, "estimated_cost_usd": None, "free_disk_bytes": free,
            "downloads": "任务环境镜像与依赖按 benchmark 配置准备；大小未确认时不估造下载量",
            "warnings": sorted(set(warnings)), "model_calls_made": 0}
