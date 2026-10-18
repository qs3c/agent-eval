"""Read-only, bounded views of native evidence. Never expose reasoning blocks."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .console import read_json, redact, safe_file
from .report import duration, task_key
from .resources.live_model import tool_ids
from .traces import summarize_usage

LIMIT = 48 * 1024 * 1024
GLOSSARY = {
    "bkcrab": "BKCrab 原生 Agent",
    "dsh-standard": "DeepSeek Harness Standard（Agent，不是模型名）",
    "positive": "固定正确操作的正对照，不调用模型",
    "negative": "不执行修复的负对照，不调用模型",
    "oracle": "使用参考修复的正对照，不调用模型",
    "nop": "不执行修复的负对照，不调用模型",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assessment(root, run, agent):
    registry = read_json(root, root / "console_reviews.json") or {}
    entry = registry.get(run, {}).get(agent)
    if not isinstance(entry, dict):
        return {"status": "unreviewed", "reason": "未复核；执行完成不等于有效评测"}
    try:
        checks = entry.get("evidence", {})
        actual_results = {
            str(p.relative_to(root))
            for p in (root / "artifacts" / run / "jobs" / agent).glob("*/result.json")
        }
        valid = (
            bool(checks)
            and actual_results <= set(checks)
            and all(
                safe_file(root, root / p).stat().st_size <= LIMIT
                and digest(safe_file(root, root / p)) == h
                for p, h in checks.items()
            )
        )
    except (OSError, ValueError):
        valid = False
    if not valid:
        return {"status": "unreviewed", "reason": "复核依据已缺失或改变，请重新复核"}
    return {
        **{k: entry[k] for k in ("status", "reason")},
        "findings": entry.get("findings", []),
    }


def decorate(root, detail):
    reviews = {a: assessment(root, detail["directory"], a) for a in detail["agents"]}
    accepted = [a for a, r in reviews.items() if r["status"] == "accepted"]
    diagnostic = all(r["status"] == "diagnostic" for r in reviews.values())
    category = (
        "debug" if diagnostic else "real" if detail["mode"] == "live" else "selftest"
    )
    detail.update(
        category=category,
        accepted_agents=accepted,
        reviews=reviews,
        agent_descriptions={a: GLOSSARY.get(a, a) for a in detail["agents"]},
    )
    return detail


def trial_paths(console, run_id):
    row = console.row(run_id)
    root = safe_file(console.artifacts, Path(row["path"]))
    return {
        hashlib.sha256(str(p.relative_to(root)).encode()).hexdigest()[:20]: p.parent
        for p in sorted(root.glob("jobs/*/*/result.json"))
        if read_json(root, p)
    }


def trace_list(console, run_id):
    detail = console.detail(run_id)
    return [
        {
            "id": key,
            "agent": path.parent.name,
            "trial": path.name,
            "task": task_key(read_json(path, path / "result.json") or {}),
            "review": detail["reviews"].get(path.parent.name),
        }
        for key, path in trial_paths(console, run_id).items()
    ]


def read_lines(root, path, warnings):
    try:
        path = safe_file(root, path)
        if path.stat().st_size > LIMIT:
            warnings.append(path.name + " 超出 48 MiB 读取上限")
            return []
        rows = []
        with path.open() as stream:
            for line, text in enumerate(stream, 1):
                if line > 100000:
                    warnings.append(path.name + " 超出行数上限")
                    break
                try:
                    value = json.loads(text)
                    if not isinstance(value, dict):
                        raise TypeError()

                    # Validate structural containers before interpreting untrusted logs.
                    def containers(obj):
                        for key in ("data", "event", "message", "usage", "body"):
                            child = obj.get(key)
                            if child is not None:
                                if not isinstance(child, dict):
                                    raise TypeError()
                                containers(child)

                    containers(value)
                    if (
                        path.name.startswith("wire_")
                        or path.name == "native_wire_requests.jsonl"
                    ) and not isinstance(value.get("request_id"), str):
                        raise TypeError()
                    rows.append((line, value))
                except (ValueError, TypeError):
                    warnings.append(f"{path.name}:{line} 无法解析")
        return rows
    except (OSError, ValueError, RecursionError):
        warnings.append(path.name + " 缺失或不可读取")
        return []


def clipped(value, limit=12000):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return redact(text[:limit]) + (
        "\n…内容已截断，完整内容见源文件" if len(text) > limit else ""
    )


def text_blocks(message):
    return "\n".join(
        str(b.get("text", ""))
        for b in (message.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "text"
    )


def trace(console, run_id, trial_id):
    paths = trial_paths(console, run_id)
    if trial_id not in paths:
        raise KeyError(trial_id)
    path = safe_file(console.artifacts, paths[trial_id])
    agent = path.parent.name
    root = path / "agent"
    warnings = []
    requests = read_lines(path, root / "native_wire_requests.jsonl", warnings)
    responses = read_lines(path, root / "wire_responses.jsonl", warnings)
    events = read_lines(path, root / "events.jsonl", warnings)
    chunks = read_lines(path, root / "wire_chunks.jsonl", warnings)
    _, usage_coverage = summarize_usage(
        [r for _, r in requests], [r for _, r in responses]
    )
    ids = [r.get("request_id") for _, r in requests]
    response_ids = [r.get("request_id") for _, r in responses]
    complete = (
        bool(ids)
        and all(isinstance(i, str) for i in ids)
        and len(set(ids)) == len(ids)
        and len(set(response_ids)) == len(response_ids)
        and set(ids) == set(response_ids)
        and not warnings
        and usage_coverage["usage_complete"]
    )
    usage = {r.get("request_id"): r.get("usage", {}) for _, r in responses}
    response_map = {r.get("request_id"): r for _, r in responses}
    request_map = {r.get("request_id"): (line, r) for line, r in requests}
    call_requests = {}
    caches = {}
    for _, row in chunks:
        event = row.get("event") or {}
        for call_id in tool_ids(event):
            call_requests.setdefault(call_id, set()).add(row["request_id"])
        u = event.get("usage") or (event.get("message") or {}).get("usage")
        if not isinstance(u, dict):
            continue
        merged = caches.setdefault(row.get("request_id"), {})
        merged.update(u)
    rounds, calls = [], {}
    for line, row in events:
        event = (row.get("data") or {}).get("event", row)
        typ, data = event.get("type"), event.get("data") or {}
        if typ in ("model_request", "step/start"):
            rounds.append(
                {
                    "index": len(rounds) + 1,
                    "source": f"events.jsonl:{line}",
                    "started_at": row.get("time"),
                    "tools": [],
                    "assistant": "",
                }
            )
        if not rounds:
            continue
        current = rounds[-1]
        if typ in ("tool_call", "tool/call"):
            key = data.get("id") or data.get("callId")
            tool = {
                "name": str(data.get("name", "unknown")),
                "call_id": key,
                "arguments": clipped(data.get("arguments", "")),
                "argument_hash": hashlib.sha256(
                    json.dumps(data.get("arguments", "")).encode()
                ).hexdigest(),
                "source": f"events.jsonl:{line}",
                "result": "尚未记录返回",
                "result_source": None,
                "started_at": row.get("time"),
                "returned_at": None,
                "error_signal": False,
                "tests": [],
            }
            current["tools"].append(tool)
            if not isinstance(key, str) or key in calls:
                warnings.append(f"events.jsonl:{line} 工具 ID 缺失或重复")
                continue
            calls[key] = tool
        elif typ in ("tool_result", "tool/result"):
            message = data.get("message") or {}
            key = data.get("id") or message.get("toolCallId")
            if not isinstance(key, str) or key not in calls:
                warnings.append(f"events.jsonl:{line} 工具返回无法配对")
                continue
            result = (
                data.get("result", "") if typ == "tool_result" else text_blocks(message)
            )
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False)
            tool = calls[key]
            tool.update(
                result=clipped(result),
                result_source=f"events.jsonl:{line}",
                returned_at=row.get("time"),
                error_signal=bool(message.get("isError"))
                or bool(
                    re.search(
                        r"(?m)^ERROR[: ]|FAILED \(|ModuleNotFoundError|command not found|No module named|ImproperlyConfigured",
                        result,
                    )
                ),
                tests=re.findall(
                    r"(?m)^.*(?:Ran \d+ tests?|FAILED \(errors=\d+\)|^OK$).*$", result
                )[:12],
            )
        elif typ == "assistant/message":
            current["assistant"] = clipped(text_blocks(data.get("message") or {}))
        elif typ == "run_end" and data.get("final_response"):
            current["assistant"] = clipped(data["final_response"])
    aligned = len(rounds) == len(requests) and complete
    if not aligned:
        warnings.append(
            "请求/响应/轮次数量不完整或不唯一；不推定逐轮用量。轮次与请求的对应基于原生日志顺序，并非统一 span ID。"
        )
    totals = {}
    for field in ("input_tokens", "output_tokens"):
        values = [usage.get(i, {}).get(field) for i in ids]
        totals[field] = (
            sum(values)
            if complete and all(isinstance(v, int) and v >= 0 for v in values)
            else None
        )
    for i, round_ in enumerate(rounds):
        candidates = [call_requests.get(t["call_id"], set()) for t in round_["tools"]]
        exact = (
            bool(candidates)
            and all(len(c) == 1 for c in candidates)
            and len(set.union(*candidates)) == 1
        )
        rid = next(iter(candidates[0])) if exact else ids[i] if aligned else None
        if rid not in request_map or ids.count(rid) != 1 or response_ids.count(rid) > 1:
            rid = None
        round_["association"] = (
            "tool_call_id" if exact and rid else "sequence_only" if rid else "unknown"
        )
        response = response_map.get(rid, {})
        round_["timing_ms"] = response.get("timing_ms")
        round_["outcome"] = response.get("outcome", "legacy_unknown")
        round_["response_complete"] = response.get("stream_complete")
        u = usage.get(rid, {})
        raw = caches.get(rid, {})
        hit = raw.get("prompt_cache_hit_tokens", raw.get("cache_read_input_tokens"))
        round_.update(
            request_id=rid,
            input_tokens=u.get("input_tokens"),
            output_tokens=u.get("output_tokens"),
            cached_tokens=hit,
            request_source=f"native_wire_requests.jsonl:{request_map[rid][0]}"
            if rid
            else None,
        )
        if rid:
            body = request_map[rid][1].get("body") or {}
            round_["request"] = {
                "model": body.get("model"),
                "messages": len(body.get("messages", [])),
                "tools": len(body.get("tools", [])),
                "max_tokens": body.get("max_tokens"),
            }
    cache_values = [
        caches.get(rid, {}).get(
            "prompt_cache_hit_tokens",
            caches.get(rid, {}).get("cache_read_input_tokens"),
        )
        for rid in ids
    ]
    cache_total = (
        sum(cache_values)
        if complete
        and all(
            type(x) is int and 0 <= x <= usage[rid]["input_tokens"]
            for rid, x in zip(ids, cache_values)
        )
        else None
    )
    totals["cached_tokens"] = cache_total
    total_input = totals["input_tokens"]
    totals["uncached_tokens"] = (
        total_input - cache_total
        if total_input is not None
        and cache_total is not None
        and cache_total <= total_input
        else None
    )
    all_tools = [t for r in rounds for t in r["tools"]]
    repeats = Counter((t["name"], t["argument_hash"]) for t in all_tools)
    for tool in all_tools:
        tool["repeated"] = repeats[(tool["name"], tool["argument_hash"])] > 1
    patch = path / "verifier" / "model.patch"
    try:
        patch = safe_file(path, patch)
        patch_text = patch.read_text() if patch.stat().st_size <= LIMIT else ""
    except (ValueError, OSError):
        patch_text = ""
    result = read_json(path, path / "result.json") or {}
    return {
        "agent": agent,
        "trial": path.name,
        "task": task_key(result),
        "review": assessment(console.root, path.parents[2].name, agent),
        "evidence_path": str(path.relative_to(console.root)),
        "rounds": rounds,
        "warnings": list(dict.fromkeys(warnings)),
        "metrics": {
            **totals,
            "agent_seconds": duration(result.get("agent_execution") or {}),
            "requests": len(requests),
            "tools": len(all_tools),
            "multi_tool_rounds": sum(len(r["tools"]) > 1 for r in rounds),
            "error_signals": sum(t["error_signal"] for t in all_tools),
        },
        "usage_complete": complete,
        "usage_evidence": usage_coverage["usage_by_purpose"]["agent"],
        "tool_counts": dict(Counter(t["name"] for t in all_tools)),
        "patch": clipped(patch_text, 24000),
        "patch_files": re.findall(r"^diff --git a/(.*?) b/", patch_text, re.MULTILINE),
        "rewards": (result.get("verifier_result") or {}).get("rewards"),
        "limitations": [
            "请求摘要仅展示模型、上下文消息数和工具定义数；不展示模型内部推理。",
            "关联优先使用模型响应与原生工具事件共有的 tool_call_id；无工具轮次可能仅按顺序关联。",
            "v2 代理使用同进程单调时钟记录阶段耗时；首输出增量可能是推理或工具参数，不等于用户可见首 token。旧日志没有这些计时。",
            "bkcrab 的工具返回可能按批交付；多工具轮次不等于已证实并行执行。",
            "错误信号来自工具返回文本，可能包含引用内容；重复调用可能是合理复验，均需结合原文判断。",
            "Agent 最终陈述不是验证结果；请与工具测试输出和评分证据交叉核对。",
        ],
    }
