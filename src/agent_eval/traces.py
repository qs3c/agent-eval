import json
from pathlib import Path

from harbor.models.trajectories.trajectory import Trajectory


def read_lines(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if path.exists()
        else []
    )


def convert(logs: Path, name: str, version: str):
    events = read_lines(logs / "events.jsonl")
    wire = read_lines(logs / "wire_requests.jsonl")
    coverage = {
        "native_events": bool(events),
        "wire_model_requests": bool(wire),
        "provider_requests": any(e["type"] == "model_request" for e in events),
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
    usage = {}
    coverage["usage_complete"] = (
        bool(responses)
        and len(responses) == len(wire)
        and all(r.get("usage") for r in responses)
    )
    if coverage["usage_complete"]:
        usage = {
            "input_tokens": sum(
                r["usage"].get("input_tokens", r["usage"].get("prompt_tokens", 0))
                for r in responses
            ),
            "output_tokens": sum(
                r["usage"].get("output_tokens", r["usage"].get("completion_tokens", 0))
                for r in responses
            ),
        }
    return trajectory, usage, coverage
