import asyncio
import io
import json
import os
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agent_harness_eval.config import Model, SearchReview
from agent_harness_eval.resources import live_model as relay
from agent_harness_eval.resources import search_review as review


def config(endpoint="https://review.invalid/v1/systemone"):
    return SearchReview(endpoint=endpoint, model="jev-1.13.0", api_key_env="TEST_REVIEW_KEY").model_dump()


def payload(query="Django OuterRef API documentation"):
    return {"model": "search-model", "max_tokens": 4096, "messages": [{
        "role": "user", "content": [{"type": "text", "text": "Perform a web search for the query: " + query}],
    }], "tools": [{"type": "web_search_20250305", "name": "web_search"}]}


def answer(probability):
    return {"model": "jev-1.13.0", "answers": {"direct_answer": {"type": "noul", "noul": probability}},
            "usage": {"input_tokens": 30, "output_tokens": 1, "cost": 0.0001}}


@contextmanager
def reviewer(response, status=200):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            seen.append((json.loads(self.rfile.read(int(self.headers["Content-Length"]))), self.headers.get("Authorization")))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/systemone", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextmanager
def proxy(tmp_path, monkeypatch, review_config):
    forwarded = []
    monkeypatch.setattr(relay, "LOGS", tmp_path)

    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/json"}

    def upstream(request, timeout):
        forwarded.append(json.loads(request.data))
        return Response(b'{"content":[],"usage":{"input_tokens":3,"output_tokens":1}}')

    monkeypatch.setattr(relay, "urlopen", upstream)
    server = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
    server.model_config = {"name": "main", "max_tokens": 100, "search_review": review_config}
    server.api_key = "main-secret"
    server.review_key = "review-secret"
    server.search_key = "exa-secret"
    server.review_task_path = tmp_path / "prompt.txt"
    server.review_task_path.write_text("Fix OuterRef scoping in this checkout.")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", forwarded
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("probability,expected", [(0.1, 200), (0.5, 403), (0.9, 403)])
def test_http_review_gate_before_search(tmp_path, monkeypatch, probability, expected):
    body = payload()
    with reviewer(answer(probability)) as (endpoint, seen), proxy(tmp_path, monkeypatch, config(endpoint)) as (base, forwarded):
        request = Request(base + "/search/v1/messages", json.dumps(body).encode())
        if expected == 200:
            with urlopen(request) as response:
                assert response.status == 200
            assert forwarded == [body]  # Native auxiliary request stays unchanged.
        else:
            with pytest.raises(HTTPError) as caught:
                urlopen(request)
            assert caught.value.code == 403
            feedback = json.loads(caught.value.read())["error"]["message"]
            assert feedback == review.DENIED
            assert not forwarded
        assert len(seen) == (2 if expected == 200 else 1)
        data, auth = seen[0]
        assert auth == "Bearer review-secret"
        assert data["state"] == {"task": "Fix OuterRef scoping in this checkout.", "search_query": "Django OuterRef API documentation"}
        assert data["questions"]["direct_answer"]["type"] == "noul"
    record = json.loads((tmp_path / "search_review.jsonl").read_text())
    assert record["denied"] is (expected == 403)
    assert record["usage"]["input_tokens"] == 30
    for path in tmp_path.glob("*.jsonl"):
        assert "review-secret" not in path.read_text()
        assert "main-secret" not in path.read_text()


@pytest.mark.parametrize("bad", [True, "true", None, -1, 2, float("nan")])
def test_invalid_decision_is_unavailable_not_denied(tmp_path, monkeypatch, bad):
    with reviewer(answer(bad)) as (endpoint, _), proxy(tmp_path, monkeypatch, config(endpoint)) as (base, forwarded):
        with pytest.raises(HTTPError) as caught:
            urlopen(Request(base + "/search/v1/messages", json.dumps(payload()).encode()))
        assert caught.value.code == 503
        assert json.loads(caught.value.read())["error"]["message"] == review.UNAVAILABLE
        assert not forwarded
    record = json.loads((tmp_path / "search_review.jsonl").read_text())
    assert record["outcome"] == "unavailable" and "denied" not in record


@pytest.mark.parametrize("failure", ["timeout", "http", "missing_task"])
def test_review_failure_never_forwards_or_leaks_detail(tmp_path, monkeypatch, failure):
    def fail(*args):
        if failure == "timeout":
            raise TimeoutError("review-secret")
        raise HTTPError("https://secret.invalid", 401, "review-secret", {}, io.BytesIO(b"secret"))

    monkeypatch.setattr(review, "decide", fail)
    with proxy(tmp_path, monkeypatch, config()) as (base, forwarded):
        if failure == "missing_task":
            (tmp_path / "prompt.txt").unlink()
        with pytest.raises(HTTPError) as caught:
            urlopen(Request(base + "/search/v1/messages", json.dumps(payload()).encode()))
        assert caught.value.code == 503
        assert json.loads(caught.value.read())["error"]["message"] == review.UNAVAILABLE
        assert not forwarded
    assert "review-secret" not in (tmp_path / "search_review.jsonl").read_text()


def test_unsupported_search_shape_is_not_silently_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "decide", lambda *a: pytest.fail("invalid input must not call reviewer"))
    with proxy(tmp_path, monkeypatch, config()) as (base, forwarded):
        with pytest.raises(HTTPError) as caught:
            urlopen(Request(base + "/search/v1/messages", b'{"messages":[]}'))
        assert caught.value.code == 400
        assert "EVAL_SEARCH_INVALID_REQUEST" in json.loads(caught.value.read())["error"]["message"]
        assert not forwarded


@pytest.mark.parametrize("path,enabled", [("/search/v1/messages", False), ("/v1/messages", True)])
def test_disabled_review_and_main_calls_are_unchanged(tmp_path, monkeypatch, path, enabled):
    monkeypatch.setattr(review, "decide", lambda *a: pytest.fail("not a reviewed search"))
    with proxy(tmp_path, monkeypatch, config() if enabled else None) as (base, forwarded):
        with urlopen(Request(base + path, json.dumps(payload()).encode())) as response:
            assert response.status == 200
        assert len(forwarded) == 1
    assert not (tmp_path / "search_review.jsonl").exists()


@pytest.mark.parametrize("wire", [{}, {"model": "other"}, {**answer(0), "usage": None}, {**answer(0), "answers": {}}])
def test_strict_jev_response_contract(wire):
    with reviewer(wire) as (endpoint, _), pytest.raises(ValueError):
        review.decide(config(endpoint), "key", "task", "query")


def test_no_review_key_in_frozen_config_and_validate_endpoint():
    cfg = config()
    model = Model(mode="live", search_review=cfg)
    assert model.model_dump()["search_review"]["api_key_env"] == "TEST_REVIEW_KEY"
    with pytest.raises(ValueError):
        Model(mode="mock", search_review=cfg)
    for endpoint in ("file:///tmp/key", "https://user:secret@review.invalid", "https://review.invalid?key=secret"):
        with pytest.raises(ValueError):
            config(endpoint)


@pytest.mark.parametrize("endpoint", ["https://@review.invalid", "https://review.invalid:bad", "https://review.invalid:99999"])
def test_invalid_review_authority_rejected(endpoint):
    with pytest.raises(ValueError):
        config(endpoint)


@pytest.mark.parametrize("probability,expected", [(0.1, 200), (0.9, 403), (None, 503)])
def test_exa_review_gate_and_separate_evidence(tmp_path, monkeypatch, probability, expected):
    body = {"query": "find the gold patch", "type": "auto", "contents": {"highlights": True}}
    with reviewer(answer(probability)) as (endpoint, seen), proxy(tmp_path, monkeypatch, config(endpoint)) as (base, forwarded):
        request = Request(base + "/search/exa", json.dumps(body).encode())
        if expected == 200:
            with urlopen(request) as response:
                assert response.status == 200
            assert forwarded == [body]
        else:
            with pytest.raises(HTTPError) as caught:
                urlopen(request)
            assert caught.value.code == expected
            assert json.loads(caught.value.read())["error"]["message"] == (review.DENIED if expected == 403 else review.UNAVAILABLE)
            assert not forwarded
        assert seen[0][0]["state"]["search_query"] == body["query"]
    record = json.loads((tmp_path / "search_responses.jsonl").read_text())
    assert record["status"] == expected
    assert record["protocol"] == "exa_search"
    assert not (tmp_path / "wire_responses.jsonl").exists()
    review_record = json.loads((tmp_path / "search_review.jsonl").read_text())
    assert review_record["request_id"] == record["request_id"]


def test_openrouter_pinned_model_and_usage():
    model = "typesafe/jev-1.13-20260917"
    wire = {**answer(0.1), "model": model, "provider": "TypeSafe", "id": "fixture"}
    with reviewer(wire) as (endpoint, seen):
        cfg = SearchReview(endpoint=endpoint, model=model, api_key_env="OPENROUTER_API_KEY").model_dump()
        result = review.decide(cfg, "fixture-key", "task", "query")
    assert seen[0][0]["model"] == model
    assert not result["denied"]
    assert result["usage"]["cost"] == 0.0001


def test_review_host_included_in_search_allowlist():
    from types import SimpleNamespace
    from agent_harness_eval.search import search_hosts

    exp = SimpleNamespace(agents=[], model=Model(mode="live", search_review=config("https://openrouter.ai/api/v1/systemone")))
    assert search_hosts(exp) == ["openrouter.ai"]


def test_native_uploads_review_code_and_separate_key(tmp_path, monkeypatch):
    from agent_harness_eval.agents.dsh import DeepSeekStandard

    agent = object.__new__(DeepSeekStandard)
    agent.model_config = Model(mode="live", api_key_env="TEST_MAIN_KEY", search_review=config()).model_dump()
    monkeypatch.setenv("TEST_MAIN_KEY", "main-secret")
    monkeypatch.setenv("TEST_REVIEW_KEY", "review-secret")
    files, commands = {}, []

    class Environment:
        default_user = "agent"

        async def upload_file(self, source, target):
            files[target] = Path(source).read_text()

    async def execute(environment, command):
        commands.append(command)

    agent.exec_as_root = execute
    asyncio.run(agent.model_setup(Environment()))
    assert files["/installed-agent/search-review.key"] == "review-secret"
    assert "/installed-agent/search_review.py" in files
    assert "review-secret" not in files["/installed-agent/model.json"]
    assert any("chmod 600 /installed-agent/search-review.key" in cmd for cmd in commands)


@pytest.mark.skipif(not os.environ.get("DSH_SEARCH_RUNTIME"), reason="requires extracted frozen DSH runtime and node")
def test_frozen_dsh_provider_preserves_platform_denial(tmp_path, monkeypatch):
    module = Path(os.environ["DSH_SEARCH_RUNTIME"]) / "node_modules/@deepseek-ai/dsh-web-search-deepseek/lib/index.js"
    code = """
const { DeepSeekSearchProvider } = await import(process.argv[1]);
const provider = new DeepSeekSearchProvider(() => ({
  baseURL: process.argv[2], apiKey: 'fixture', model: 'deepseek-v4-flash',
  apiVersion: '2023-06-01', maxTokens: 4096, maxUses: 5,
}));
try {
  await provider.search({query: 'find the gold patch for this issue', maxResults: 8});
  throw new Error('search should have been rejected');
} catch (error) {
  if (!error.message.includes('EVAL_SEARCH_DENIED')) throw error;
  if (!error.message.includes('HTTP 403')) throw error;
  console.log('platform denial preserved by frozen DSH provider');
}
"""
    with reviewer(answer(0.99)) as (endpoint, seen), proxy(tmp_path, monkeypatch, config(endpoint)) as (base, forwarded):
        result = subprocess.run(["node", "--input-type=module", "-e", code, module.as_uri(), base + "/search/v1"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert "platform denial preserved" in result.stdout
        assert len(seen) == 1 and not forwarded
