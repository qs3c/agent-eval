import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agent_harness_eval.policies.model import freeze
from agent_harness_eval.resources.policy_model import handler
from test_model_policy import profile


@pytest.mark.parametrize("role", ["agent", "guardian", "integration", "diagnosis", "improvement", "user_simulator", "official_grading"])
def test_trusted_entrypoint_pins_all_internal_model_calls_and_keeps_budgets_separate(tmp_path, role):
    captured = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')
    config = profile()
    config["name"] = "trusted-" + role
    config["policy"]["budget"] = {"max_requests": 4}
    policy = freeze(config, role)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(SimpleNamespace(Handler=Upstream), policy.model_dump(mode="json"), policy.digest, tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def call(body, endpoint):
        return urlopen(Request(f"http://127.0.0.1:{server.server_port}" + endpoint, json.dumps(body).encode()), timeout=5)
    try:
        for purpose in ("primary", "subagent", "compression", "fallback"):
            with call({"model": "harness-choice", "purpose": "official_grading", "role": "improvement",
                       "temperature": 2, "internal_call": purpose, "messages": []},
                      "/v1/messages" if purpose == "fallback" else "/v1/chat/completions") as response:
                response.read()
        with pytest.raises(HTTPError) as error:
            call({"model": "escape", "messages": []}, "/v1/chat/completions")
        assert error.value.code == 429
    finally:
        server.shutdown(); server.server_close(); thread.join()
    assert len(captured) == 4
    assert all(body["model"] == "trusted-" + role and body["temperature"] == 0 and body["max_tokens"] == 100 for body in captured)
    assert all("purpose" not in body and "role" not in body for body in captured)
    records = [json.loads(line) for line in (tmp_path / "policy-requests.jsonl").read_text().splitlines()]
    assert {row["role"] for row in records} == {role}
    assert {row["policy_digest"] for row in records} == {policy.digest}
