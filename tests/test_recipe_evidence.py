import json
from types import SimpleNamespace

from agent_harness_eval.integrations.evidence import normalize


def test_hermes_native_events_remain_authoritative_and_do_not_invent_model_hooks(tmp_path):
    raw = '\n'.join(json.dumps(event) for event in [
        {"type": "system", "subtype": "init"},
        {"type": "tool_use", "name": "write_file", "input": {"path": "result.txt"}},
        {"type": "tool_result", "name": "write_file", "output": "ok"},
        {"type": "result", "text": "done"}])
    source = tmp_path / "hermes-stream.jsonl"
    source.write_text(raw)
    recipe = SimpleNamespace(id="hermes", profile="cli-local-one-shot")
    normalize(tmp_path, recipe)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [event["type"] for event in events] == ["system", "tool_call", "tool_result", "run_end"]
    assert events[1]["source"] == {"file": source.name, "line": 2, "kind": "native-cli"}
    assert events[-1]["data"]["final_response"] == "done"
    assert source.read_text() == raw
    normalize(tmp_path, recipe)
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 4


def test_opencode_completed_tool_part_has_both_call_and_result_without_fabricated_timing(tmp_path):
    raw = {"type": "tool_use", "part": {"tool": "write", "id": "call-1",
           "state": {"status": "completed", "input": {"path": "a"}, "output": "created"}}}
    (tmp_path / "opencode-stream.jsonl").write_text(json.dumps(raw))
    normalize(tmp_path, SimpleNamespace(id="opencode", profile="cli-standalone"))
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [event["type"] for event in events] == ["tool_call", "tool_result"]
    assert events[0]["source"] == events[1]["source"]
    assert events[1]["data"]["output"] == "created"
    assert all("time" not in event for event in events)
