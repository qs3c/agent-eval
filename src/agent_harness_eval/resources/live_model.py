"""Official DeepSeek relay. Records bodies/usage, never authentication headers.

Native agent loops and tool schemas are unchanged. Sampling parameters are
explicitly normalized across the OpenAI and Messages transports.
"""

import errno
import json
import random
import socket
import ssl
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOCK = threading.Lock()
LOGS = Path("/logs/agent")
RETRY_POLICY = "pre-response-transient/v1"


def retryable(error):
    """Classify transport failures without logging potentially secret URLs."""
    if isinstance(error, HTTPError):
        return error.code in {429, 502, 503, 504}
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ssl.SSLError):
        return False
    if isinstance(reason, socket.gaierror):
        return reason.errno == socket.EAI_AGAIN
    return isinstance(reason, (TimeoutError, ConnectionError)) or (
        isinstance(reason, OSError)
        and reason.errno in {errno.ETIMEDOUT, errno.ENETUNREACH, errno.EHOSTUNREACH}
    )


def open_upstream(request, timeout, request_id, purpose):
    """Retry only opening the response, never replay an exposed response body.

    All attempts share the original opening budget. The trial supervisor's
    independent hard deadline still applies to the agent and its relay.
    """
    deadline = time.monotonic() + timeout
    for attempt in range(1, 4):
        started = time.monotonic()
        record = {
            "request_id": request_id,
            "purpose": purpose,
            "retry_policy": RETRY_POLICY,
            "attempt": attempt,
            "time": datetime.now(UTC).isoformat(),
        }
        try:
            remaining = deadline - started
            if remaining <= 0:
                raise TimeoutError("Upstream opening budget exhausted")
            response = urlopen(request, timeout=remaining)
        except Exception as error:
            reason = error.reason if isinstance(error, URLError) else error
            delay = 2 ** (attempt - 1) + random.uniform(0, 0.25)
            again = (
                attempt < 3 and retryable(error)
                and time.monotonic() + delay < deadline
            )
            record.update(
                outcome="retry" if again else "failed",
                error=type(error).__name__,
                reason_type=type(reason).__name__,
                status=error.code if isinstance(error, HTTPError) else None,
                delay_seconds=delay if again else 0,
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
            append("wire_attempts.jsonl", record)
            if not again:
                raise
            if isinstance(error, HTTPError):
                error.close()
            time.sleep(delay)
        else:
            record.update(
                outcome="headers_received", status=response.status,
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
            append("wire_attempts.jsonl", record)
            return response


def upstream_url(config, messages=False):
    base = config.get("api_base", "https://api.deepseek.com").rstrip("/")
    if messages:
        base = config.get("messages_base") or ("https://api.deepseek.com/anthropic" if base.removesuffix("/v1") == "https://api.deepseek.com" else "")
        if not base:
            raise ValueError("模型未配置 Messages 接口")
        return base.rstrip("/").removesuffix("/v1") + "/v1/messages"
    if base == "https://api.deepseek.com":
        return base + "/chat/completions"
    return base.removesuffix("/v1") + "/v1/chat/completions"


def append(name, value):
    with LOCK, (LOGS / name).open("a") as f:
        f.write(json.dumps(value, ensure_ascii=False) + "\n")


def normalize(body, messages, config):
    result = dict(body)
    if config.get("api_base", "https://api.deepseek.com").rstrip("/").removesuffix("/v1") != "https://api.deepseek.com":
        result.update(model=config["name"], max_tokens=config["max_tokens"])
        return result
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
        if self.path == "/search/exa":
            self.exa_search()
            return
        paths = {
            "/v1/chat/completions": "/chat/completions",
            "/chat/completions": "/chat/completions",
            "/v1/messages": "/anthropic/v1/messages",
            "/search/v1/messages": "/anthropic/v1/messages",
        }
        if self.path not in paths:
            self.send_error(404)
            return
        request_id = str(uuid.uuid4())
        started = time.monotonic_ns()
        marks = {}
        search = self.path == "/search/v1/messages"
        messages = self.path in ("/v1/messages", "/search/v1/messages")
        protocol = "messages" if messages else "chat_completions"
        purpose = "web_search" if search else "agent"

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
                "purpose": purpose,
            }
            append("wire_timing.jsonl", value)
            return value

        stamp("received")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        append("native_wire_requests.jsonl", {"request_id": request_id, "body": body})
        # Auxiliary search is a separate provider operation: retain its model,
        # token budget and server tools. Main-agent sampling does not apply.
        if not search:
            body = normalize(body, messages, self.server.model_config)
        append("wire_requests.jsonl", body)
        append(
            "effective_wire_requests.jsonl", {"request_id": request_id, "purpose": purpose, "body": body}
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.server.api_key,
        }
        if messages:
            headers["x-api-key"] = self.server.api_key
            headers["anthropic-version"] = "2023-06-01"
        request = Request(
            upstream_url(self.server.model_config, messages),
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
            with open_upstream(request, 60 if search else 300, request_id, purpose) as response:
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
            try:
                data = error.read().replace(self.server.api_key.encode(), b"[REDACTED]")
            finally:
                error.close()
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
            if status is None:
                # Fail the tool promptly instead of leaving the client without an
                # HTTP response; preserve a bounded upstream wait for search.
                self.send_error(504 if isinstance(error, TimeoutError) else 502,
                                "Upstream request failed")
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
                    "purpose": purpose,
                    "timing_ms": marks,
                    "retry_policy": RETRY_POLICY,
                },
            )


    def exa_search(self):
        """Relay the native Exa contract, with independent search evidence."""
        key = getattr(self.server, "search_key", "")
        if not key:
            self.send_error(503, "Exa relay is not configured")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or not isinstance(body.get("query"), str) or not body["query"].strip():
                raise ValueError("missing query")
            if set(body) - {"query", "type", "contents", "numResults"}:
                raise ValueError("unsupported fields")
            if body.get("type", "auto") != "auto" or body.get("contents") != {"highlights": True}:
                raise ValueError("unsupported extraction")
            if "numResults" in body and (type(body["numResults"]) is not int or not 1 <= body["numResults"] <= 20):
                raise ValueError("invalid count")
        except (ValueError, TypeError):
            self.send_error(400, "Invalid Exa search request")
            return
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        status, outcome = None, "incomplete"
        record = {"request_id": request_id, "purpose": "web_search", "provider": "exa"}
        # Only the platform-owned relay holds the real key. Never log headers.
        def clean(value):
            return json.loads(json.dumps(value).replace(key, "[REDACTED]"))
        append("search_requests.jsonl", {**record, "body": clean(body), "time": datetime.now(UTC).isoformat()})
        request = Request("https://api.exa.ai/search", json.dumps(body).encode(),
                          {"Content-Type": "application/json", "x-api-key": key})
        try:
            with open_upstream(request, 10, request_id, "web_search") as response:
                status = response.status
                data = response.read().replace(key.encode(), b"[REDACTED]")
                result = json.loads(data)
                append("search_results.jsonl", {**record, "body": result})
            outcome = "completed"
        except HTTPError as error:
            status, outcome = error.code, "http_error"
            error.close()
            data = json.dumps({"error": "Exa upstream HTTP error", "status": status}).encode()
        except Exception as error:  # noqa: BLE001 - no sensitive exception text
            status, outcome = 502, "transport_error"
            data = b'{"error":"Exa upstream request failed"}'
            append("wire_errors.jsonl", {**record, "error": type(error).__name__})
        finally:
            append("search_responses.jsonl", {**record, "schema_version": 2,
                   "status": status, "outcome": outcome, "protocol": "exa_search",
                   "usage": None, "stream_complete": outcome == "completed",
                   "timing_ms": {"finished": (time.monotonic() - started) * 1000},
                   "retry_policy": RETRY_POLICY})
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    key_path = Path("/installed-agent/live.key")
    key = key_path.read_text().strip()
    key_path.unlink()
    server = ThreadingHTTPServer(("127.0.0.1", 18765), Handler)
    server.api_key = key
    server.model_config = json.loads(Path("/installed-agent/model.json").read_text())
    if server.model_config.get("web_search", {}).get("transport") == "platform-relay":
        search_path = Path("/installed-agent/search.key")
        server.search_key = search_path.read_text().strip()
        search_path.unlink()
    server.serve_forever()
