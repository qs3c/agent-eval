import io
import json
import socket
import ssl
import threading
from contextlib import contextmanager
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from agent_harness_eval.resources import live_model as relay


@contextmanager
def retry_fixture(tmp_path, monkeypatch, upstream):
    monkeypatch.setattr(relay, "LOGS", tmp_path)
    monkeypatch.setattr(relay, "urlopen", upstream)
    monkeypatch.setattr(relay.random, "uniform", lambda *_: 0)
    sleeps = []
    clock = [0.0]

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(relay.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(relay.time, "sleep", sleep)
    server = relay.ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
    server.api_key = "retry-secret"
    server.model_config = {"name": "test", "max_tokens": 100}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", sleeps
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class RetryResponse(io.BytesIO):
    status = 200
    headers: ClassVar = {"Content-Type": "text/event-stream"}


@pytest.mark.parametrize(
    "path", ["/v1/chat/completions", "/v1/messages", "/search/v1/messages"]
)
def test_relay_retries_transport_then_http_failure(tmp_path, monkeypatch, path):
    seen = []
    stream = b'data: {"usage":{"input_tokens":3,"output_tokens":1}}\n\ndata: [DONE]\n\n'
    failed_response = io.BytesIO(b"retry-secret")

    def upstream(request, timeout):
        seen.append((request.data, timeout))
        if len(seen) == 1:
            raise URLError(ConnectionResetError("retry-secret"))
        if len(seen) == 2:
            raise HTTPError(request.full_url, 503, "retry-secret", {}, failed_response)
        return RetryResponse(stream)

    with (
        retry_fixture(tmp_path, monkeypatch, upstream) as (base, sleeps),
        urlopen(Request(base + path, b'{"stream":true,"messages":[]}')) as response,
    ):
        assert response.read() == stream
    assert sleeps == [1, 2]
    assert failed_response.closed
    assert len({body for body, _ in seen}) == 1
    assert seen[0][1] > seen[-1][1]
    attempts = [
        json.loads(s)
        for s in (tmp_path / "wire_attempts.jsonl").read_text().splitlines()
    ]
    assert [a["outcome"] for a in attempts] == ["retry", "retry", "headers_received"]
    result = json.loads((tmp_path / "wire_responses.jsonl").read_text())
    assert result["outcome"] == "completed"
    assert result["usage"]["input_tokens"] == 3
    assert {a["request_id"] for a in attempts} == {result["request_id"]}
    assert result["retry_policy"] == relay.RETRY_POLICY
    assert all("retry-secret" not in p.read_text() for p in tmp_path.iterdir())


@pytest.mark.parametrize("status", [429, 502, 503, 504, 400, 401, 403])
def test_relay_retry_exhaustion_and_permanent_http_errors(
    tmp_path, monkeypatch, status
):
    calls = []

    def upstream(request, timeout):
        calls.append(timeout)
        raise HTTPError(request.full_url, status, "fixture", {}, io.BytesIO(b"failure"))

    with retry_fixture(tmp_path, monkeypatch, upstream) as (base, sleeps):
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(base + "/v1/chat/completions", b'{"messages":[]}'))
        assert exc.value.code == status
        assert exc.value.read() == b"failure"
        exc.value.close()
    assert len(calls) == (3 if status in {429, 502, 503, 504} else 1)
    assert len(sleeps) == len(calls) - 1


@pytest.mark.parametrize(
    "reason,expected",
    [
        (socket.gaierror(socket.EAI_AGAIN, "temporary"), True),
        (socket.gaierror(socket.EAI_NONAME, "invalid host"), False),
        (ssl.SSLCertVerificationError("certificate"), False),
        (TimeoutError(), True),
        (ConnectionRefusedError(), True),
        ("unknown transport error", False),
    ],
)
def test_transport_retry_classification(reason, expected):
    assert relay.retryable(URLError(reason)) is expected


@pytest.mark.parametrize("permanent", [False, True])
def test_relay_transport_failure_returns_bounded_error(
    tmp_path, monkeypatch, permanent
):
    calls = []

    def upstream(request, timeout):
        calls.append(timeout)
        reason = (
            ssl.SSLCertVerificationError("retry-secret")
            if permanent
            else ConnectionResetError("retry-secret")
        )
        raise URLError(reason)

    with retry_fixture(tmp_path, monkeypatch, upstream) as (base, sleeps):
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(base + "/v1/messages", b'{"messages":[]}'))
        assert exc.value.code == 502
        assert b"retry-secret" not in exc.value.read()
        exc.value.close()
    assert len(calls) == (1 if permanent else 3)
    assert len(sleeps) == len(calls) - 1
    assert all("retry-secret" not in p.read_text() for p in tmp_path.iterdir())


def test_relay_does_not_replay_broken_stream(tmp_path, monkeypatch):
    calls = []
    chunk = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'

    class BrokenResponse(RetryResponse):
        def __next__(self):
            if self.tell():
                raise ConnectionResetError("retry-secret")
            return super().__next__()

    def upstream(request, timeout):
        calls.append(request)
        return BrokenResponse(chunk)

    with (
        retry_fixture(tmp_path, monkeypatch, upstream) as (base, sleeps),
        urlopen(
            Request(base + "/v1/chat/completions", b'{"stream":true,"messages":[]}')
        ) as response,
    ):
        assert response.read() == chunk
    assert len(calls) == 1 and sleeps == []
    result = json.loads((tmp_path / "wire_responses.jsonl").read_text())
    assert not result["stream_complete"]
    assert result["outcome"] == "transport_or_client_error"


def test_retry_opening_budget_is_not_reset(tmp_path, monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(relay, "LOGS", tmp_path)
    monkeypatch.setattr(relay.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(relay.random, "uniform", lambda *_: 0)

    def upstream(request, timeout):
        calls.append(timeout)
        clock[0] += timeout
        raise URLError(TimeoutError())

    monkeypatch.setattr(relay, "urlopen", upstream)
    with pytest.raises(URLError):
        relay.open_upstream(Request("https://upstream.test"), 60, "budget", "agent")
    assert calls == [60]


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
