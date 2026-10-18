"""Project native CLI evidence into the platform event format without altering it."""
import json
import sqlite3
from pathlib import Path


def collect_events(logs: Path, name: str):
    events = []
    def emit(kind, data, timestamp=None):
        row = {"type": kind, "data": data, "source": "native-cli-evidence"}
        if timestamp is not None:
            row["time"] = timestamp
        events.append(row)
    final = None
    if name == "opencode":
        path = logs / "opencode.jsonl"
        for line in path.read_text().splitlines() if path.exists() else []:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            part = row.get("part", {})
            if row.get("type") == "step_start":
                emit("step/start", {"native_message_id": part.get("messageID")})
            elif row.get("type") == "text":
                final = part.get("text")
            elif row.get("type") == "tool_use":
                state = part.get("state", {})
                emit("tool_call", {"id": part.get("callID"), "name": part.get("tool"), "arguments": state.get("input", {})})
                emit("tool_result", {"id": part.get("callID"), "result": state.get("output", state.get("error", ""))})
            emit("native_event", row)
    elif name == "hermes":
        path = logs / "hermes-home/state.db"
        if path.exists():
            # Read-only, including the retained WAL when present. Never mutate the native database.
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                primary = {r[0] for r in db.execute("SELECT id FROM sessions WHERE source IN ('oneshot','cli') AND parent_session_id IS NULL")}
                for raw in db.execute("SELECT id,session_id,role,content,tool_calls,tool_call_id,tool_name,timestamp FROM messages ORDER BY id"):
                    row = dict(raw)
                    if row["role"] == "assistant":
                        emit("step/start", {"native_message_id": row["id"], "native_session_id": row["session_id"]})
                    if row["role"] == "assistant" and row["tool_calls"]:
                        for call in json.loads(row["tool_calls"]):
                            function = call.get("function", {})
                            arguments = function.get("arguments", {})
                            if isinstance(arguments, str):
                                try:
                                    arguments = json.loads(arguments)
                                except json.JSONDecodeError:
                                    pass
                            emit("tool_call", {"id": call.get("id"), "name": function.get("name"), "arguments": arguments})
                    elif row["role"] == "tool":
                        emit("tool_result", {"id": row["tool_call_id"], "result": row["content"]})
                    elif row["role"] == "assistant" and row["content"] and row["session_id"] in primary:
                        final = row["content"]
                    emit("native_event", row)
    if final:
        emit("run_end", {"final_response": final})
    if events:
        (logs / "events.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in events))
    return events
