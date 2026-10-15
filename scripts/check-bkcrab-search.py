"""Offline end-to-end search wiring check; no real model/search quality claim."""

import argparse
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path, help="new evidence directory")
    parser.add_argument("--live-exa", action="store_true", help="call real Exa using EXA_API_KEY; model remains a fixture")
    parser.add_argument("--platform-relay", action="store_true", help="route live Exa through the platform relay")
    args = parser.parse_args()
    if args.platform_relay and not args.live_exa:
        parser.error("--platform-relay requires --live-exa")
    if args.live_exa and not os.environ.get("EXA_API_KEY"):
        parser.error("EXA_API_KEY is required for --live-exa")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "work").mkdir()
    (root / "prompt.txt").write_text("Search for Python documentation using web_search and report the result.")
    observed = {"search_requests": 0, "model_requests": 0, "tool_exposed": False, "result_returned": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit

            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if url.path != "/search" or query.get("q") != ["Python documentation"]:
                self.send_error(400)
                return
            observed["search_requests"] += 1
            body = json.dumps({"results": [{"title": "Python documentation", "url": "https://docs.python.org/3/", "content": "OFFLINE_SEARCH_FIXTURE"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed["model_requests"] += 1
            names = {t["function"]["name"] for t in request.get("tools", [])}
            observed["tool_exposed"] |= "web_search" in names
            marker = "Search results for: Python documentation" if args.live_exa else "OFFLINE_SEARCH_FIXTURE"
            returned = any(m.get("role") == "tool" and marker in str(m.get("content")) for m in request["messages"])
            observed["result_returned"] |= returned
            message = {"role": "assistant", "content": "Verified search fixture." if returned else ""}
            if not returned:
                message["tool_calls"] = [{"index": 0, "id": "fixture-search", "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": "Python documentation"})}}]
            finish = "stop" if returned else "tool_calls"
            chunks = [
                {"id": "search-fixture", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": message, "finish_reason": None}]},
                {"id": "search-fixture", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}},
            ]
            body = ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    relay_server = None
    key_directory = tempfile.TemporaryDirectory(prefix="exa-smoke-")
    search_args = ["--web-search-provider", "searxng", "--web-search-endpoint", base]
    if args.platform_relay:
        from agent_harness_eval.resources import live_model as relay

        relay.LOGS = root / "relay"
        relay.LOGS.mkdir()
        relay_server = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        relay_server.search_key = os.environ["EXA_API_KEY"]
        relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
        relay_thread.start()
        search_args = ["--web-search-provider", "exa", "--web-search-endpoint", f"http://127.0.0.1:{relay_server.server_port}/search/exa"]
    elif args.live_exa:
        key_file = Path(key_directory.name) / "key"
        key_file.touch(mode=0o600)
        key_file.write_text(os.environ["EXA_API_KEY"])
        search_args = ["--web-search-provider", "exa", "--web-search-key-file", str(key_file)]
    try:
        with (root / "stdout.log").open("w") as stdout, (root / "stderr.log").open("w") as stderr:
            result = subprocess.run([
                str(args.binary.resolve()), "--workspace", str(root / "work"),
                "--home", str(root / "home"), "--output", str(root / "agent"),
                "--prompt-file", str(root / "prompt.txt"), "--model", "offline-fixture",
                "--api-base", base + "/v1", "--max-iterations", "3", "--timeout", "30s",
                *search_args,
            ], check=False, env={**{k: v for k, v in os.environ.items() if k != "EXA_API_KEY"}, "EVAL_MODEL_API_KEY": "offline-fixture-key", "NO_PROXY": "127.0.0.1"}, stdout=stdout, stderr=stderr, timeout=45)
        if args.live_exa:
            events = [json.loads(line) for line in (root / "agent/events.jsonl").read_text().splitlines()]
            observed["search_requests"] = sum(e.get("type") == "tool_call" and e.get("data", {}).get("name") == "web_search" for e in events)
        observed["returncode"] = result.returncode
        observed["passed"] = result.returncode == 0 and observed["tool_exposed"] and observed["result_returned"] and observed["search_requests"] == 1
        if args.platform_relay:
            requests = [json.loads(line) for line in (root / "relay/search_requests.jsonl").read_text().splitlines()]
            responses = [json.loads(line) for line in (root / "relay/search_responses.jsonl").read_text().splitlines()]
            observed["platform_relay_verified"] = len(requests) == len(responses) == 1 and responses[0]["outcome"] == "completed" and requests[0]["request_id"] == responses[0]["request_id"]
            observed["passed"] &= observed["platform_relay_verified"]
        observed["evidence_kind"] = ("live Exa via real eval binary; fixture model; not a search quality or model capability evaluation" if args.live_exa else "offline fixture; real eval binary and native SearxNG provider; no external search or model calls")
        (root / "validation.json").write_text(json.dumps(observed, indent=2) + "\n")
        print(json.dumps(observed))
        if not observed["passed"]:
            raise SystemExit(1)
    finally:
        key_directory.cleanup()
        if relay_server:
            relay_server.shutdown()
            relay_server.server_close()
            relay_thread.join()
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
