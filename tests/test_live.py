import io
import json
import threading
from typing import ClassVar
from urllib.request import Request, urlopen

from agent_eval.resources import live_model as relay


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
    assert usage == {"input_tokens": 100, "output_tokens": 20}


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
