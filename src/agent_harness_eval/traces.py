import json
from collections import Counter
from pathlib import Path

from harbor.models.trajectories.trajectory import Trajectory


def read_lines(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if path.exists()
        else []
    )


def _summarize_usage(requests, responses):
    """Totals require all required counters and, when available, exact ID pairing."""
    ids = [r.get("request_id") for r in requests]
    response_ids = [r.get("request_id") for r in responses]
    identified = any(i is not None for i in ids + response_ids)
    paired = bool(requests) and len(requests) == len(responses)
    if identified:
        paired = (
            paired
            and all(isinstance(i, str) and i for i in ids + response_ids)
            and len(set(ids)) == len(ids)
            and len(set(response_ids)) == len(response_ids)
            and set(ids) == set(response_ids)
        )
    values = []
    for r in responses:
        u = r.get("usage") or {}
        counters = (
            u.get("input_tokens", u.get("prompt_tokens")),
            u.get("output_tokens", u.get("completion_tokens")),
        )
        if not all(type(v) is int and v >= 0 for v in counters):
            paired = False
        if r.get("schema_version", 1) >= 2 and (
            r.get("outcome") != "completed" or r.get("stream_complete") is not True
        ):
            paired = False
        if r.get("status") is not None and r["status"] != 200:
            paired = False
        values.append(counters)
    usage = (
        {
            "input_tokens": sum(v[0] for v in values),
            "output_tokens": sum(v[1] for v in values),
        }
        if paired
        else {}
    )
    # A failed/missing response must not erase independently attributable usage.
    # Exclude ambiguous IDs instead of guessing which duplicate was billed.
    request_counts, response_counts = Counter(ids), Counter(response_ids)
    known = []
    reasons = Counter()
    for request in requests:
        rid = request.get("request_id")
        if identified and (not rid or request_counts[rid] != 1):
            reasons["请求 ID 缺失或重复"] += 1
        elif identified and response_counts[rid] == 0:
            reasons["缺少响应"] += 1
        elif identified and response_counts[rid] > 1:
            reasons["响应 ID 重复"] += 1
    for response, counters in zip(responses, values):
        rid = response.get("request_id")
        attributable = (request_counts[rid] == response_counts[rid] == 1 and bool(rid)) if identified else paired
        if not attributable:
            if identified and not request_counts[rid]:
                reasons["响应无法匹配请求"] += 1
            continue
        valid = all(type(v) is int and v >= 0 for v in counters)
        if valid:
            known.append(counters)
        else:
            reasons["响应缺少有效 usage"] += 1
        if response.get("schema_version", 1) >= 2 and (
            response.get("outcome") != "completed" or response.get("stream_complete") is not True
        ):
            reasons["响应未完整结束"] += 1
        if response.get("status") is not None and response["status"] != 200:
            reasons["响应状态异常"] += 1
    return usage, {
        "usage_complete": bool(paired),
        "known_usage": {"input_tokens": sum(v[0] for v in known),
                        "output_tokens": sum(v[1] for v in known)} if known else {},
        "known_request_count": len(known),
        "missing_usage_requests": max(0, len(requests) - len(known)),
        "usage_issues": dict(reasons),
        "request_response_matching": "request_id"
        if identified
        else "legacy_count_only",
    }


def summarize_usage(requests, responses):
    """Report main-agent usage independently of auxiliary provider operations.

    Old relay requests lack purpose; use their matching response when available.
    Untagged/unmatched legacy evidence stays in agent scope conservatively.
    """
    purposes = {}
    for row in [*requests, *responses]:
        if row.get("request_id") and row.get("purpose"):
            purposes[row["request_id"]] = row["purpose"]

    def purpose(row):
        return row.get("purpose") or purposes.get(row.get("request_id"), "agent")

    groups = {}
    for kind in {"agent", *(purpose(row) for row in [*requests, *responses])}:
        reqs = [r for r in requests if purpose(r) == kind]
        resps = [r for r in responses if purpose(r) == kind]
        usage, coverage = _summarize_usage(reqs, resps)
        groups[kind] = {"usage": usage, **coverage,
                        "request_count": len(reqs), "response_count": len(resps)}
    main = groups["agent"]
    return main["usage"], {
        "usage_complete": main["usage_complete"],
        "request_response_matching": main["request_response_matching"],
        "usage_scope": "agent",
        "usage_by_purpose": groups,
    }


def review_usage(logs):
    """Keep policy review accounting separate; incomplete checks stay unknown."""
    result = {}
    for name in ("search_review", "search_result_review", "fetch_review"):
        records = read_lines(logs / (name + ".jsonl"))
        billable_records = [record for record in records if record.get("cache_hit") is not True]
        checks = []
        for record in billable_records:
            if record.get("intent_check") is not None:
                checks.append(record["intent_check"])
            if "checks" in record:
                checks.extend(record["checks"])
            elif "intent_check" not in record:
                checks.append(record)
        usage, coverage = _summarize_usage([{} for _ in checks],
                                           [{"usage": c.get("usage")} for c in checks])
        if records and not billable_records:
            # A recorded cache hit performs no review model request.
            usage = {"input_tokens": 0, "output_tokens": 0}
            coverage["usage_complete"] = True
        if any(record.get("outcome") not in ("allowed", "denied") for record in records):
            usage, coverage["usage_complete"] = {}, False
        result[name] = {"usage": usage, **coverage, "record_count": len(records),
                        "check_count": len(checks),
                        "cache_hit_count": len(records) - len(billable_records)}
    return result


def conversation_request(requests):
    """Choose a tool-bearing conversation over later title/probe requests.

    This affects the partial ATIF reconstruction only, never usage accounting.
    """
    def has_tools(request):
        if request.get("tools"):
            return True
        for message in request.get("messages", []):
            if message.get("role") == "tool" or message.get("tool_calls"):
                return True
            content = message.get("content")
            if isinstance(content, list) and any(
                isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result")
                for block in content
            ):
                return True
        return False

    for index in range(len(requests) - 1, -1, -1):
        if has_tools(requests[index]):
            return index, requests[index], "last_tool_conversation"
    for index in range(len(requests) - 1, -1, -1):
        if requests[index].get("messages"):
            return index, requests[index], "last_messages_request"
    return len(requests) - 1, requests[-1], "last_request"


def convert(logs: Path, name: str, version: str):
    events, event_warnings = [], []
    event_path = logs / "events.jsonl"
    if event_path.exists():
        for number, line in enumerate(event_path.read_text(errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not isinstance(event.get("data"), dict):
                    raise ValueError("unsupported event shape")
            except (ValueError, TypeError):
                event_warnings.append(f"events.jsonl:{number} 无法转换为标准事件；原始记录已保留")
                continue
            events.append(event)
    wire = read_lines(logs / "wire_requests.jsonl")
    coverage = {
        "native_events": bool(events),
        "event_warnings": event_warnings,
        "wire_model_requests": bool(wire),
        "provider_requests": bool(wire)
        or any(e["type"] == "model_request" for e in events),
        "native_provider_hook": any(e["type"] == "model_request" for e in events),
        "request_timing": (logs / "wire_timing.jsonl").exists(),
        "compaction_events": "native events only; internal selection not guaranteed",
        "atif": "last tool conversation context (or last messages request) plus native final output; native logs remain authoritative",
    }
    requests = wire or [e["data"] for e in events if e["type"] == "model_request"]
    if not requests:
        return None, {}, coverage
    request_index, request, selection = conversation_request(requests)
    coverage["atif_request_selection"] = selection
    coverage["atif_request_index"] = request_index
    steps = []
    by_call = {}
    messages = list(request.get("messages", []))
    if request.get("system"):
        messages.insert(0, {"role": "system", "content": request["system"]})
    for msg in messages:
        msg = dict(msg)
        if isinstance(msg.get("content"), list):
            blocks = msg["content"]
            for block in blocks:
                if block.get("type") == "tool_result":
                    step = by_call.get(block["tool_use_id"])
                    if step is not None:
                        step.setdefault("observation", {"results": []})[
                            "results"
                        ].append(
                            {
                                "source_call_id": block["tool_use_id"],
                                "content": json.dumps(
                                    block.get("content", ""), ensure_ascii=False
                                ),
                            }
                        )
            calls = [b for b in blocks if b.get("type") == "tool_use"]
            if calls:
                msg["tool_calls"] = [
                    {
                        "id": b["id"],
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b["input"]),
                        },
                    }
                    for b in calls
                ]
            msg["content"] = "\n".join(
                b.get("text", "") for b in blocks if b.get("type") == "text"
            )
            if not msg["content"] and not calls:
                continue
        role = msg.get("role")
        if role == "tool":
            step = by_call.get(msg.get("tool_call_id"))
            if step is not None:
                step.setdefault("observation", {"results": []})["results"].append(
                    {
                        "source_call_id": msg["tool_call_id"],
                        "content": msg.get("content", ""),
                    }
                )
            continue
        if role not in ("system", "user", "assistant"):
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        step = {
            "step_id": len(steps) + 1,
            "source": "agent" if role == "assistant" else role,
            "message": content,
        }
        if msg.get("tool_calls"):
            step["tool_calls"] = [
                {
                    "tool_call_id": t["id"],
                    "function_name": t["function"]["name"],
                    "arguments": json.loads(t["function"]["arguments"]),
                }
                for t in msg["tool_calls"]
            ]
            for call in step["tool_calls"]:
                by_call[call["tool_call_id"]] = step
        steps.append(step)
    final = next(
        (
            e["data"].get("final_response")
            for e in reversed(events)
            if e["type"] == "run_end"
        ),
        None,
    )
    if name == "dsh-standard":
        for e in reversed(events):
            ev = e.get("data", {}).get("event", {})
            if ev.get("type") == "assistant/message":
                final = "".join(
                    b.get("text", "")
                    for b in ev["data"]["message"].get("content", [])
                    if b.get("type") == "text"
                )
                break
    if final:
        steps.append({"step_id": len(steps) + 1, "source": "agent", "message": final})
    trajectory = (
        Trajectory.model_validate(
            {
                "session_id": logs.parent.name,
                "agent": {
                    "name": name,
                    "version": version,
                    "model_name": request.get("model"),
                },
                "steps": steps,
                "notes": coverage["atif"],
            }
        )
        if steps
        else None
    )
    # The mock provider reports deterministic counts. Never infer absent real usage from requests.
    responses = read_lines(logs / "wire_responses.jsonl")
    identified = read_lines(logs / "native_wire_requests.jsonl")
    # Timing rows also identify legacy requests rejected before forwarding.
    purposes = {r["request_id"]: r["purpose"] for r in read_lines(logs / "wire_timing.jsonl")
                if r.get("request_id") and r.get("purpose")}
    usage_requests = [{**r, **({"purpose": purposes[r["request_id"]]}
                              if not r.get("purpose") and r.get("request_id") in purposes else {})}
                      for r in (identified or wire)]
    usage, usage_coverage = summarize_usage(usage_requests, responses)
    usage_coverage["review_usage"] = review_usage(logs)
    coverage.update(usage_coverage)
    return trajectory, usage, coverage
