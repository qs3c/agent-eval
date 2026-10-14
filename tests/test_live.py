import io
import json
import threading
from typing import ClassVar
from urllib.request import Request, urlopen

from agent_harness_eval.resources import live_model as relay


def test_protocol_sampling_and_cached_usage():
    config = {"name": "deepseek-flash", "max_tokens": 32768}
    for messages in (False, True):
        body = relay.normalize({"model": "other", "stream": True}, messages, config)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "enabled"}
        assert body["temperature"] == 1 and body["top_p"] == 0.95
        assert (
            body.get("reasoning_effort", body.get("output_config", {}).get("effort"))
            == "high"
        )
    usage = {}
    relay.collect_usage(
        {
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 90,
                    "output_tokens": 1,
                }
            }
        },
        usage,
    )
    relay.collect_usage({"usage": {"output_tokens": 20}}, usage)
    assert usage["input_tokens"] == 100 and usage["output_tokens"] == 20
    assert usage["cached_input_tokens"] == 90 and usage["uncached_input_tokens"] == 10


def test_search_relay_preserves_auxiliary_contract_and_replaces_auth(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(relay, "LOGS", tmp_path)
    seen = []
    payload = {
        "model": "search-model",
        "max_tokens": 4096,
        "messages": [],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }

    class Response(io.BytesIO):
        status = 200
        headers: ClassVar = {"Content-Type": "application/json"}

    def upstream(request, timeout):
        seen.append(request)
        return Response(b'{"content":[],"usage":{"input_tokens":3,"output_tokens":1}}')

    monkeypatch.setattr(relay, "urlopen", upstream)
    server = relay.ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
    server.api_key = "actual-secret-fixture"
    server.model_config = {
        "name": "main-model",
        "max_tokens": 32768,
        "messages_base": "https://upstream.test/anthropic",
    }
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            Request(
                f"http://127.0.0.1:{server.server_port}/search/v1/messages",
                json.dumps(payload).encode(),
                {"x-api-key": "relay-only"},
            )
        ) as response:
            assert json.load(response)["content"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert seen[0].full_url == "https://upstream.test/anthropic/v1/messages"
    assert json.loads(seen[0].data) == payload
    assert seen[0].get_header("X-api-key") == "actual-secret-fixture"
    record = json.loads((tmp_path / "wire_responses.jsonl").read_text())
    assert record["purpose"] == "web_search" and record["usage"]["input_tokens"] == 3
    assert all("actual-secret-fixture" not in p.read_text() for p in tmp_path.iterdir())


def test_relay_stream_forwarding_and_secret_exclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(relay, "LOGS", tmp_path)
    seen = []
    stream = b'data: {"choices":[],"usage":{"prompt_tokens":123,"completion_tokens":7}}\n\ndata: [DONE]\n\n'

    class Response(io.BytesIO):
        status = 200
        headers: ClassVar = {"Content-Type": "text/event-stream"}

    def upstream(request, timeout):
        seen.append(request)
        return Response(stream)

    monkeypatch.setattr(relay, "urlopen", upstream)
    server = relay.ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
    server.api_key = "secret-for-test-only"
    server.model_config = {"name": "deepseek-flash", "max_tokens": 32768}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                json.dumps({"stream": True, "messages": []}).encode(),
            )
        ) as response:
            assert response.read() == stream
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert seen[0].full_url == "https://api.deepseek.com/chat/completions"
    assert seen[0].get_header("Authorization") == "Bearer secret-for-test-only"
    assert (
        json.loads((tmp_path / "wire_responses.jsonl").read_text())["usage"][
            "input_tokens"
        ]
        == 123
    )
    assert all("secret-for-test-only" not in p.read_text() for p in tmp_path.iterdir())


def test_v2_ids_and_monotonic_timing(tmp_path, monkeypatch):
    # Exercise the actual HTTP handler for both protocols; no external model call.
    monkeypatch.setattr(relay, "LOGS", tmp_path)
    streams = [
        b'data: {"choices":[{"delta":{"tool_calls":[{"id":"tool-A"}]}}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2}}\n\ndata: [DONE]\n\n',
        b'data: {"type":"content_block_start","content_block":{"type":"tool_use","id":"tool-B"}}\n\ndata: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{}"}}\n\ndata: {"type":"message_delta","usage":{"input_tokens":3,"cache_read_input_tokens":7,"output_tokens":2}}\n\ndata: {"type":"message_stop"}\n\n',
        b'data: {"usage":{"prompt_tokens":10,"completion_tokens":2}}\n\n',
        b'data: {"type":"error","error":{"type":"overloaded"}}\n\ndata: {"type":"message_stop"}\n\n',
    ]

    class Response(io.BytesIO):
        status = 200
        headers: ClassVar = {"Content-Type": "text/event-stream"}

    monkeypatch.setattr(
        relay, "urlopen", lambda *_args, **_kwargs: Response(streams.pop(0))
    )
    server = relay.ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
    server.api_key = "not-a-real-secret"
    server.model_config = {"name": "test", "max_tokens": 100}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path in (
            "/v1/chat/completions",
            "/v1/messages",
            "/v1/chat/completions",
            "/v1/messages",
        ):
            with urlopen(
                Request(
                    f"http://127.0.0.1:{server.server_port}{path}",
                    json.dumps({"stream": True, "messages": []}).encode(),
                )
            ) as response:
                response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    rows = [
        json.loads(line)
        for line in (tmp_path / "wire_responses.jsonl").read_text().splitlines()
    ]
    assert [r["outcome"] for r in rows] == [
        "completed",
        "completed",
        "truncated",
        "provider_error",
    ]
    assert rows[0]["tool_call_ids"] == ["tool-A"]
    assert rows[1]["tool_call_ids"] == ["tool-B"]
    for r in rows[:2]:
        m = r["timing_ms"]
        assert (
            m["received"]
            <= m["upstream_start"]
            <= m["headers"]
            <= m["first_event"]
            <= m["first_output_delta"]
            <= m["finished"]
        )
        assert r["stream_complete"] is True
    effective = [
        json.loads(line)
        for line in (tmp_path / "effective_wire_requests.jsonl")
        .read_text()
        .splitlines()
    ]
    assert {r["request_id"] for r in rows} == {r["request_id"] for r in effective}
    from agent_harness_eval.traces import summarize_usage

    assert summarize_usage(effective, rows)[1]["usage_complete"] is False
