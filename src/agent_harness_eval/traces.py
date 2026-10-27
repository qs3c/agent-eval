import json
from pathlib import Path

from harbor.models.trajectories.trajectory import Trajectory


def read_lines(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if path.exists()
        else []
    )


def summarize_usage(requests, responses):
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
    return usage, {
        "usage_complete": bool(paired),
        "request_response_matching": "request_id"
        if identified
        else "legacy_count_only",
    }


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
        "atif": "last observed request context plus terminal output; native logs remain authoritative",
    }
    requests = wire or [e["data"] for e in events if e["type"] == "model_request"]
    if not requests:
        return None, {}, coverage
    request = requests[-1]
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
    usage, usage_coverage = summarize_usage(identified or wire, responses)
    coverage.update(usage_coverage)
    return trajectory, usage, coverage
