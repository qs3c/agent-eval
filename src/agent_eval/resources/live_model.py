"""Official DeepSeek relay. Records bodies/usage, never authentication headers.

Native agent loops and tool schemas are unchanged. Sampling parameters are
explicitly normalized across the OpenAI and Messages transports.
"""

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LOCK = threading.Lock()
LOGS = Path("/logs/agent")


def append(name, value):
    with LOCK, (LOGS / name).open("a") as f:
        f.write(json.dumps(value, ensure_ascii=False) + "\n")


def normalize(body, messages, config):
    result = dict(body)
    result.update(
        model=config["name"],
        max_tokens=config["max_tokens"],
        temperature=1,
        top_p=0.95,
        thinking={"type": "enabled"},
    )
    result.pop("reasoning", None)
    if messages:
        result.pop("reasoning_effort", None)
        result["output_config"] = {"effort": "high"}
    else:
        result["reasoning_effort"] = "high"
        if result.get("stream"):
            result["stream_options"] = {"include_usage": True}
    return result


def collect_usage(event, usage):
    source = event.get("message", {}).get("usage") or event.get("usage")
    if not source:
        return
    if "prompt_tokens" in source:
        usage["input_tokens"] = source["prompt_tokens"]
        usage["output_tokens"] = source.get("completion_tokens", 0)
    else:
        if "input_tokens" in source:
            usage["input_tokens"] = (
                source["input_tokens"]
                + source.get("cache_read_input_tokens", 0)
                + source.get("cache_creation_input_tokens", 0)
            )
        if "output_tokens" in source:
            usage["output_tokens"] = source["output_tokens"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        paths = {
            "/v1/chat/completions": "/chat/completions",
            "/chat/completions": "/chat/completions",
            "/v1/messages": "/anthropic/v1/messages",
        }
        if self.path not in paths:
            self.send_error(404)
            return
        request_id = str(uuid.uuid4())
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        append("native_wire_requests.jsonl", {"request_id": request_id, "body": body})
        messages = self.path == "/v1/messages"
        body = normalize(body, messages, self.server.model_config)
        append("wire_requests.jsonl", body)
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.server.api_key,
        }
        if messages:
            headers["x-api-key"] = self.server.api_key
            headers["anthropic-version"] = "2023-06-01"
        request = Request(
            "https://api.deepseek.com" + paths[self.path],
            json.dumps(body).encode(),
            headers,
        )
        usage = {}
        status = None
        try:
            with urlopen(request, timeout=300) as response:
                status = response.status
                self.send_response(status)
                self.send_header(
                    "Content-Type",
                    response.headers.get("Content-Type", "application/json"),
                )
                self.end_headers()
                if body.get("stream"):
                    for line in response:
                        self.wfile.write(line)
                        self.wfile.flush()
                        if line.startswith(b"data:"):
                            data = line[5:].strip()
                            if data and data != b"[DONE]":
                                event = json.loads(data)
                                collect_usage(event, usage)
                                append(
                                    "wire_chunks.jsonl",
                                    {"request_id": request_id, "event": event},
                                )
                else:
                    data = response.read()
                    event = json.loads(data)
                    collect_usage(event, usage)
                    append(
                        "wire_chunks.jsonl", {"request_id": request_id, "event": event}
                    )
                    self.wfile.write(data)
        except HTTPError as error:
            status = error.code
            data = error.read().replace(self.server.api_key.encode(), b"[REDACTED]")
            append(
                "wire_errors.jsonl",
                {
                    "request_id": request_id,
                    "status": status,
                    "body": data.decode(errors="replace"),
                },
            )
            self.send_response(status)
            self.end_headers()
            self.wfile.write(data)
        except Exception as error:  # noqa: BLE001 - log transport failure without sensitive exception text
            append(
                "wire_errors.jsonl",
                {"request_id": request_id, "error": type(error).__name__},
            )
        finally:
            append(
                "wire_responses.jsonl",
                {"request_id": request_id, "status": status, "usage": usage or None},
            )


if __name__ == "__main__":
    key_path = Path("/installed-agent/live.key")
    key = key_path.read_text().strip()
    key_path.unlink()
    server = ThreadingHTTPServer(("127.0.0.1", 18765), Handler)
    server.api_key = key
    server.model_config = json.loads(Path("/installed-agent/model.json").read_text())
    server.serve_forever()
