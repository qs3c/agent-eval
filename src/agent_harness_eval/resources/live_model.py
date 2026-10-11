"""Official DeepSeek relay. Records bodies/usage, never authentication headers.

Native agent loops and tool schemas are unchanged. Sampling parameters are
explicitly normalized across the OpenAI and Messages transports.
"""

import json
import threading
import time
import uuid
from datetime import UTC, datetime
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
        if "completion_tokens" in source:
            usage["output_tokens"] = source["completion_tokens"]
        cached = source.get(
            "prompt_cache_hit_tokens",
            (source.get("prompt_tokens_details") or {}).get("cached_tokens"),
        )
        if cached is not None:
            usage["cached_input_tokens"] = cached
            usage["uncached_input_tokens"] = source["prompt_tokens"] - cached
    else:
        # Messages input_tokens excludes cache; preserve partial stream updates.
        for key in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            if key in source:
                usage["raw_" + key] = source[key]
        if "raw_input_tokens" in usage:
            usage["input_tokens"] = sum(
                usage.get("raw_" + key, 0)
                for key in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
        if "cache_read_input_tokens" in source:
            usage["cached_input_tokens"] = source["cache_read_input_tokens"]
        if "raw_input_tokens" in usage:
            usage["uncached_input_tokens"] = usage["raw_input_tokens"] + usage.get(
                "raw_cache_creation_input_tokens", 0
            )
        if "output_tokens" in source:
            usage["output_tokens"] = source["output_tokens"]


def tool_ids(event):
    """Provider-issued tool IDs bridge relay responses and native tool events."""
    ids = []
    for choice in event.get("choices", []):
        for call in (choice.get("delta") or choice.get("message") or {}).get(
            "tool_calls", []
        ):
            if call.get("id"):
                ids.append(call["id"])
    for block in [event.get("content_block") or {}, *event.get("content", [])]:
        if block.get("type") == "tool_use" and block.get("id"):
            ids.append(block["id"])
    return ids


def has_output(event):
    # Includes reasoning or tool-argument deltas; this is NOT user-visible TTFT.
    if event.get("type") == "content_block_delta":
        return any(
            event.get("delta", {}).get(k) for k in ("text", "thinking", "partial_json")
        )
    return any(
        any(
            (c.get("delta") or {}).get(k)
            for k in ("content", "reasoning_content", "tool_calls")
        )
        for c in event.get("choices", [])
    )


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
        started = time.monotonic_ns()
        marks = {}
        protocol = "messages" if self.path == "/v1/messages" else "chat_completions"

        def stamp(phase):
            elapsed = (time.monotonic_ns() - started) / 1e6
            marks[phase] = elapsed
            value = {
                "schema_version": 2,
                "request_id": request_id,
                "phase": phase,
                "time": datetime.now(UTC).isoformat(),
                "elapsed_ms": elapsed,
                "clock": "relay_monotonic",
                "protocol": protocol,
            }
            append("wire_timing.jsonl", value)
            return value

        stamp("received")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        append("native_wire_requests.jsonl", {"request_id": request_id, "body": body})
        messages = self.path == "/v1/messages"
        body = normalize(body, messages, self.server.model_config)
        append("wire_requests.jsonl", body)
        append(
            "effective_wire_requests.jsonl", {"request_id": request_id, "body": body}
        )
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
        outcome = "incomplete"
        stream_complete = False
        call_ids = set()
        stream_error = False

        def observe(event):
            nonlocal stream_error
            if event.get("error") or event.get("type") == "error":
                stream_error = True
            if "first_event" not in marks:
                stamp("first_event")
            if has_output(event) and "first_output_delta" not in marks:
                stamp("first_output_delta")
            collect_usage(event, usage)
            call_ids.update(tool_ids(event))
            append(
                "wire_chunks.jsonl",
                {
                    "request_id": request_id,
                    "event": event,
                    "elapsed_ms": (time.monotonic_ns() - started) / 1e6,
                },
            )

        try:
            stamp("upstream_start")
            with urlopen(request, timeout=300) as response:
                status = response.status
                stamp("headers")
                self.send_response(status)
                self.send_header(
                    "Content-Type",
                    response.headers.get("Content-Type", "application/json"),
                )
                self.end_headers()
                if body.get("stream"):
                    for line in response:
                        if line.startswith(b"data:"):
                            data = line[5:].strip()
                            if data == b"[DONE]":
                                stream_complete = True
                            elif data:
                                event = json.loads(data)
                                observe(event)
                                if event.get("type") == "message_stop":
                                    stream_complete = True
                        self.wfile.write(line)
                        self.wfile.flush()
                    outcome = (
                        "provider_error"
                        if stream_error
                        else "completed"
                        if stream_complete
                        else "truncated"
                    )
                else:
                    data = response.read()
                    event = json.loads(data)
                    observe(event)
                    self.wfile.write(data)
                    stream_complete = True
                    outcome = "provider_error" if stream_error else "completed"
                stamp("upstream_end")
        except HTTPError as error:
            status = error.code
            outcome = "http_error"
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
            outcome = "transport_or_client_error"
            append(
                "wire_errors.jsonl",
                {"request_id": request_id, "error": type(error).__name__},
            )
        finally:
            stamp("finished")
            append(
                "wire_responses.jsonl",
                {
                    "schema_version": 2,
                    "request_id": request_id,
                    "status": status,
                    "usage": usage or None,
                    "outcome": outcome,
                    "stream_complete": stream_complete,
                    "tool_call_ids": sorted(call_ids),
                    "protocol": protocol,
                    "timing_ms": marks,
                },
            )


if __name__ == "__main__":
    key_path = Path("/installed-agent/live.key")
    key = key_path.read_text().strip()
    key_path.unlink()
    server = ThreadingHTTPServer(("127.0.0.1", 18765), Handler)
    server.api_key = key
    server.model_config = json.loads(Path("/installed-agent/model.json").read_text())
    server.serve_forever()
