import json

from agent_harness_eval.guardian.config import GuardianConfig
from agent_harness_eval.guardian.monitor import Monitor
from agent_harness_eval.guardian.probe import read_events


def event(kind, data, seconds=0):
    return {"type": kind, "time": f"2026-01-01T00:00:{seconds:02d}Z", "data": data}


def write_lines(path, *events):
    with path.open("a", encoding="utf-8") as stream:
        for row in events:
            stream.write(json.dumps(row) + "\n")


def snapshot(path, cursor=None):
    return {
        "telemetry": read_events(path, cursor),
        "time_unix": 1767226000,
        "logs": {"events.jsonl": {"size": path.stat().st_size}},
    }


def test_tool_return_before_large_model_request_is_not_lost(tmp_path):
    path = tmp_path / "events.jsonl"
    write_lines(
        path,
        event("tool_call", {"name": "exec", "id": "a", "arguments": '{"timeout":120}'}),
    )
    monitor = Monitor(GuardianConfig(confirmations=1))
    first = snapshot(path)
    monitor.observe(first, 0, "sample_agent")
    write_lines(
        path,
        event("tool_result", {"id": "a"}, 1),
        event("model_request", {"messages": "x" * 200_000}, 2),
    )
    second = snapshot(path, first["telemetry"]["cursor"])
    assert monitor.observe(second, 1, "sample_agent")[0] is None
    assert not monitor.pending


def test_partial_line_and_read_budget_resume_without_loss(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"tool_result",', encoding="utf-8")
    first = read_events(path)
    assert not first["complete"] and first["cursor"]["offset"] == 0
    with path.open("a") as stream:
        stream.write('"data":{"id":"a"}}\n')
    write_lines(
        path,
        event("model_request", {"x": "z" * 1000}),
        event("tool_result", {"id": "b"}),
    )
    cursor = first["cursor"]
    got = []
    for _ in range(5):
        batch = read_events(path, cursor, byte_budget=100)
        got.extend(e["id"] for e in batch["events"])
        cursor = batch["cursor"]
        if batch["complete"]:
            break
    assert got == ["a", "b"] and batch["complete"]


def test_rotation_or_corrupt_line_drops_untrusted_pending(tmp_path):
    path = tmp_path / "events.jsonl"
    write_lines(
        path, event("tool_call", {"name": "exec", "id": "a", "arguments": "{}"})
    )
    first = snapshot(path)
    monitor = Monitor(GuardianConfig(confirmations=1))
    monitor.observe(first, 0, "sample_agent")
    path.write_text("not-json\n", encoding="utf-8")
    changed = snapshot(path, first["telemetry"]["cursor"])
    assert changed["telemetry"]["gap"]
    assert monitor.observe(changed, 1000, "sample_agent")[0] is None
    assert not monitor.pending


def test_dsh_wait_suppresses_idle_until_its_own_deadline(tmp_path):
    path = tmp_path / "events.jsonl"
    write_lines(
        path,
        {
            "type": "session_event",
            "data": {
                "event": {
                    "type": "tool/call",
                    "time": 1767225600000,
                    "data": {
                        "name": "job_output",
                        "callId": "wait-1",
                        "arguments": '{"wait":true,"timeout_ms":600000,"job_id":"bash-1"}',
                    },
                }
            },
        },
    )
    monitor = Monitor(
        GuardianConfig(idle_seconds=300, confirmations=1, tool_grace_seconds=45)
    )
    snap = snapshot(path)
    snap["time_unix"] = 1767225600
    monitor.observe(snap, 0, "dsh-standard")
    snap = snapshot(path, snap["telemetry"]["cursor"])
    snap["time_unix"] = 1767225935
    assert monitor.observe(snap, 335, "dsh-standard")[0] is None
    snap["time_unix"] = 1767226246
    assert monitor.observe(snap, 646, "dsh-standard")[0] == "tool_overdue"
