import json

from agent_harness_eval.resources.mock_model import fixture, stream_events
from agent_harness_eval.traces import convert


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


def test_identity_and_missing_counter_validation():
    from agent_harness_eval.traces import summarize_usage

    requests = [{"request_id": "a"}, {"request_id": "b"}]
    responses = [
        {"request_id": "a", "usage": {"input_tokens": 1, "output_tokens": 2}}
    ] * 2
    assert summarize_usage(requests, responses)[0] == {}
    responses[1] = {"request_id": "b", "usage": {"input_tokens": 1}}
    assert summarize_usage(requests, responses)[0] == {}
    responses[1]["usage"]["output_tokens"] = 0
    assert summarize_usage(requests, responses)[0] == {
        "input_tokens": 2,
        "output_tokens": 2,
    }


def test_dsh_relay_coverage_is_not_native_provider_hook(tmp_path):
    (tmp_path / "wire_requests.jsonl").write_text(
        '{"messages":[{"role":"user","content":"hi"}]}\n'
    )
    _, _, coverage = convert(tmp_path, "dsh-standard", "test")
    assert coverage["provider_requests"] is True
    assert coverage["native_provider_hook"] is False


def test_auxiliary_failures_do_not_erase_main_usage():
    from agent_harness_eval.traces import summarize_usage
    requests = [{"request_id": "main"}, {"request_id": "search"},
                {"request_id": "missing", "purpose": "web_search"}]
    responses = [
        {"request_id": "main", "purpose": "agent", "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"request_id": "search", "purpose": "web_search", "status": 403, "usage": None},
    ]
    usage, coverage = summarize_usage(requests, responses)
    assert usage == {"input_tokens": 10, "output_tokens": 2}
    assert coverage["usage_scope"] == "agent" and coverage["usage_complete"]
    search = coverage["usage_by_purpose"]["web_search"]
    assert search["usage"] == {} and not search["usage_complete"]
    assert search["request_count"] == 2 and search["response_count"] == 1
    # Truly unknown main/legacy requests must still invalidate main totals.
    assert summarize_usage(requests + [{"request_id": "unknown"}], responses)[0] == {}


def test_convert_uses_timing_for_legacy_unmatched_search_and_separates_reviews(tmp_path):
    rows = {
        "wire_requests": [{"messages": [{"role": "user", "content": "task"}]}],
        "native_wire_requests": [{"request_id": "main"}, {"request_id": "search"}],
        "wire_responses": [{"request_id": "main", "purpose": "agent", "usage": {"input_tokens": 10, "output_tokens": 2}}],
        "wire_timing": [{"request_id": "search", "purpose": "web_search", "phase": "received"}],
        "search_review": [{"request_id": "search", "outcome": "denied", "usage": {"input_tokens": 30, "output_tokens": 1}}],
        "fetch_review": [{"outcome": "unavailable", "checks": [{"usage": {"input_tokens": 50, "output_tokens": 1}}]}],
    }
    for name, records in rows.items():
        (tmp_path / (name + ".jsonl")).write_text("\n".join(json.dumps(r) for r in records))
    _, usage, coverage = convert(tmp_path, "dsh-standard", "test")
    assert usage == {"input_tokens": 10, "output_tokens": 2}
    assert coverage["usage_by_purpose"]["web_search"]["response_count"] == 0
    assert coverage["review_usage"]["search_review"]["usage"] == {"input_tokens": 30, "output_tokens": 1}
    assert coverage["review_usage"]["fetch_review"]["usage"] == {}


def test_title_after_tool_conversation_does_not_replace_trajectory(tmp_path):
    conversation = {"model": "main", "tools": [{"type": "function"}], "messages": [
        {"role": "user", "content": "create file"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call", "function": {"name": "write", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call", "content": "written"},
    ]}
    title = {"model": "title", "messages": [{"role": "user", "content": "name session"}]}
    (tmp_path / "wire_requests.jsonl").write_text("\n".join(json.dumps(r) for r in [{"name": "probe"}, conversation, title]))
    (tmp_path / "wire_responses.jsonl").write_text('\n'.join(json.dumps({"usage": {"input_tokens": 10, "output_tokens": 1}}) for _ in range(3)))
    (tmp_path / "events.jsonl").write_text(json.dumps({"type": "run_end", "data": {"final_response": "done"}}))
    trajectory, usage, coverage = convert(tmp_path, "hermes", "test")
    value = trajectory.model_dump()
    assert value["agent"]["model_name"] == "main"
    assert value["steps"][-1]["message"] == "done"
    assert value["steps"][1]["observation"]["results"][0]["content"] == "written"
    assert usage == {"input_tokens": 30, "output_tokens": 3}  # no accounting exclusion
    assert coverage["atif_request_index"] == 1


def test_conversation_selection_recognizes_anthropic_history_without_tools():
    from agent_harness_eval.traces import conversation_request
    request = {"messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "a"}]}]}
    assert conversation_request([request, {"messages": [{"role": "user", "content": "title"}]}])[0] == 0
    assert conversation_request([{"name": "probe"}, {"messages": [{"role": "user", "content": "task"}]}, {"name": "probe"}])[0] == 1


def test_review_cache_is_not_billed_and_nested_intent_is_counted(tmp_path):
    from agent_harness_eval.traces import review_usage
    records = [
        {"outcome": "allowed", "cache_hit": False,
         "intent_check": {"usage": {"input_tokens": 10, "output_tokens": 1}},
         "checks": [{"usage": {"input_tokens": 20, "output_tokens": 2}}]},
        {"outcome": "allowed", "cache_hit": True},
        {"outcome": "denied", "cache_hit": False,
         "intent_check": {"usage": {"input_tokens": 30, "output_tokens": 3}}},
    ]
    path = tmp_path / "fetch_review.jsonl"
    path.write_text('\n'.join(json.dumps(r) for r in records))
    summary = review_usage(tmp_path)["fetch_review"]
    assert summary["usage"] == {"input_tokens": 60, "output_tokens": 6}
    assert summary["usage_complete"] and summary["check_count"] == 3
    assert summary["cache_hit_count"] == 1
    path.write_text(json.dumps(records[1]))
    assert review_usage(tmp_path)["fetch_review"]["usage"] == {"input_tokens": 0, "output_tokens": 0}
    records[0]["outcome"] = "unavailable"
    path.write_text('\n'.join(json.dumps(r) for r in records))
    assert review_usage(tmp_path)["fetch_review"]["usage"] == {}
