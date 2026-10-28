"""Auxiliary model requests must retain their caller's sampling contract."""

import io
import json
import threading
from urllib.request import Request, urlopen

import pytest

from agent_harness_eval.resources import live_model as relay


@pytest.mark.parametrize("purpose", ["user_simulator", "official_grading", "agent"])
@pytest.mark.parametrize("messages", [False, True])
def test_server_role_controls_sampling_on_the_wire(tmp_path, monkeypatch, purpose, messages):
    captured = []

    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/json"}

    def upstream(request, timeout):
        captured.append(json.loads(request.data))
        return Response(b'{"usage":{"prompt_tokens":2,"completion_tokens":1}}')

    monkeypatch.setattr(relay, "LOGS", tmp_path)
    monkeypatch.setattr(relay, "urlopen", upstream)
    server = relay.ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
    server.purpose = purpose
    server.api_key = "fixture-key"
    server.model_config = {"name": "deepseek-flash", "max_tokens": 32768}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = {"model": "requested", "messages": [], "temperature": 0, "top_p": 0.8,
            "max_tokens": 512, "thinking": {"type": "disabled"}, "seed": 17,
            "purpose": "official_grading"}
    path = "/v1/messages" if messages else "/v1/chat/completions"
    try:
        with urlopen(Request(f"http://127.0.0.1:{server.server_port}{path}",
                             json.dumps(body).encode()), timeout=5) as response:
            response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    effective = captured[0]
    assert effective["model"] == "deepseek-flash"
    assert effective["seed"] == 17
    if purpose == "agent":
        assert effective["temperature"] == 1
        assert effective["top_p"] == 0.95
        assert effective["max_tokens"] == 32768
        assert effective["thinking"] == {"type": "enabled"}
    else:
        assert effective == {**body, "model": "deepseek-flash"}
    native = json.loads((tmp_path / "native_wire_requests.jsonl").read_text())
    recorded = json.loads((tmp_path / "effective_wire_requests.jsonl").read_text())
    assert native["body"] == body
    assert recorded["body"] == effective
    assert recorded["purpose"] == purpose


@pytest.mark.parametrize("purpose", ["user_simulator", "official_grading"])
def test_auxiliary_defaults_only_fill_missing_token_budget(purpose):
    body = {"temperature": 0, "messages": []}
    result = relay.normalize(body, False, {"name": "test", "max_tokens": 100}, purpose)
    assert result == {**body, "model": "test", "max_tokens": 100}
    assert body == {"temperature": 0, "messages": []}
