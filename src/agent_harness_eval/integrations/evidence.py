"""Derived event views retain offsets into authoritative upstream output."""

import json


def normalize(logs, recipe):
    files = {("hermes", "cli-local-one-shot"): "hermes-stream.jsonl",
             ("opencode", "cli-standalone"): "opencode-stream.jsonl"}
    name = files.get((recipe.id, recipe.profile))
    if not name:
        return
    source = logs / name
    if not source.is_file() or (logs / "events.jsonl").exists():
        return
    events = []
    for number, line in enumerate(source.read_text().splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        kind = raw.get("type")
        origin = {"file": source.name, "line": number, "kind": "native-cli"}
        if recipe.id == "opencode":
            part = raw.get("part", {})
            if kind == "tool_use":
                state = part.get("state", {})
                data = {"name": part.get("tool"), "tool_call_id": part.get("id"), "input": state.get("input")}
                events.append({"type": "tool_call", "data": data, "source": origin})
                if state.get("status") in {"completed", "error"}:
                    events.append({"type": "tool_result", "data": {**data, "output": state.get("output"),
                                   "error": state.get("error"), "status": state["status"]}, "source": origin})
            else:
                events.append({"type": kind or "unknown", "data": raw, "source": origin})
            continue
        if kind == "tool_use":
            kind = "tool_call"
        elif kind == "result":
            kind = "run_end"
        data = dict(raw)
        if kind == "run_end":
            data["final_response"] = raw.get("text")
        events.append({"type": kind or "unknown", "data": data,
                       "source": origin})
    (logs / "events.jsonl").write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events))
