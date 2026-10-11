import json

from agent_eval.resources.mock_model import fixture, stream_events
from agent_eval.traces import convert


def test_both_protocols_tool_roundtrip_and_atif(tmp_path):
    for anthropic in (False, True):
        logs = tmp_path / str(anthropic)
        logs.mkdir()
        function = {
            "name": "write",
            "parameters": {"properties": {"file_path": {}, "content": {}}},
        }
        tool = (
            {"name": "write", "input_schema": function["parameters"]}
            if anthropic
            else {"function": function}
        )
        request = {
            "model": "fixture",
            "messages": [{"role": "user", "content": "write the file"}],
            "tools": [tool],
        }
        initial_request = json.dumps(request)
        mode, response = fixture(request)
        assert mode == anthropic
        if anthropic:
            request["messages"] += [
                {"role": "assistant", "content": response["content"]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "fixture-call",
                            "content": "ok",
                        }
                    ],
                },
            ]
        else:
            request["messages"] += [
                response["choices"][0]["message"],
                {"role": "tool", "tool_call_id": "fixture-call", "content": "ok"},
            ]
        _, final = fixture(request)
        assert len(list(stream_events(mode, response))) >= 2
        (logs / "wire_requests.jsonl").write_text(
            initial_request + "\n" + json.dumps(request) + "\n"
        )
        (logs / "wire_responses.jsonl").write_text(
            json.dumps(response) + "\n" + json.dumps(final) + "\n"
        )
        (logs / "events.jsonl").write_text(
            json.dumps({"type": "run_end", "data": {"final_response": "done"}}) + "\n"
        )
        trajectory, usage, _coverage = convert(logs, "fixture", "1")
        assert usage == {"input_tokens": 200, "output_tokens": 40}
        value = trajectory.model_dump()
        calls = [s for s in value["steps"] if s.get("tool_calls")]
        assert (
            len(calls) == 1
            and calls[0]["observation"]["results"][0]["source_call_id"]
            == "fixture-call"
        )


def test_absent_evidence_is_unknown(tmp_path):
    trajectory, usage, _coverage = convert(tmp_path, "fixture", "1")
    assert trajectory is None and usage == {} and not _coverage["native_events"]


def test_inflight_request_does_not_report_partial_total(tmp_path):
    (tmp_path / "wire_requests.jsonl").write_text(
        '{"model":"m","messages":[{"role":"user","content":"task"}]}\n' * 2
    )
    (tmp_path / "wire_responses.jsonl").write_text(
        '{"usage":{"input_tokens":100,"output_tokens":20}}\n'
    )
    _, usage, coverage = convert(tmp_path, "fixture", "1")
    assert usage == {} and coverage["usage_complete"] is False
