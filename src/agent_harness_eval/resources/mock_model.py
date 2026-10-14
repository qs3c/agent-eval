"""Deterministic OpenAI and Messages transport fixture; NOT a model measurement."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def fixture(body):
    anthropic = any("input_schema" in t for t in body.get("tools", []))
    tools = (
        {t["name"]: t for t in body.get("tools", [])}
        if anthropic
        else {t["function"]["name"]: t["function"] for t in body.get("tools", [])}
    )
    done = any(
        m.get("role") == "tool"
        or any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in (m.get("content") if isinstance(m.get("content"), list) else [])
        )
        for m in body.get("messages", [])
    )
    name = None
    args = {}
    if not done:
        name = next(
            (n for n in ("write_file", "write", "bash", "exec") if n in tools), None
        )
        if name is None:
            raise ValueError("fixture requires a supported write or shell tool")
        schema = tools[name].get("input_schema", tools[name].get("parameters", {}))
        for key in schema.get("properties", {}):
            if key in ("path", "file_path", "filename"):
                args[key] = "result.txt"
            if key in ("content", "text"):
                args[key] = "ready\n"
            if key in ("command", "cmd"):
                args[key] = "printf 'ready\\n' > result.txt"
            if key == "description":
                args[key] = "Write the requested file"
    if anthropic:
        content = (
            [{"type": "text", "text": "Created result.txt."}]
            if done
            else [
                {"type": "tool_use", "id": "fixture-call", "name": name, "input": args}
            ]
        )
        return True, {
            "id": "fixture-response",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": content,
            "stop_reason": "end_turn" if done else "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
    message = {"role": "assistant", "content": "Created result.txt." if done else None}
    if not done:
        message["tool_calls"] = [
            {
                "id": "fixture-call",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ]
    return False, {
        "id": "fixture-response",
        "object": "chat.completion",
        "model": body["model"],
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop" if done else "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def stream_events(anthropic, response):
    if anthropic:
        yield (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    **response,
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 100, "output_tokens": 0},
                },
            },
        )
        for i, block in enumerate(response["content"]):
            tool = block["type"] == "tool_use"
            yield (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": i,
                    "content_block": {**block, "input": {}}
                    if tool
                    else {"type": "text", "text": ""},
                },
            )
            delta = (
                {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
                if tool
                else {"type": "text_delta", "text": block["text"]}
            )
            yield (
                "content_block_delta",
                {"type": "content_block_delta", "index": i, "delta": delta},
            )
            yield "content_block_stop", {"type": "content_block_stop", "index": i}
        yield (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": response["stop_reason"],
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": 20},
            },
        )
        yield "message_stop", {"type": "message_stop"}
    else:
        choice = response["choices"][0]
        message = choice["message"]
        for i, call in enumerate(message.get("tool_calls", [])):
            call["index"] = i
        yield (
            None,
            {
                "id": response["id"],
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": message, "finish_reason": None}],
            },
        )
        yield (
            None,
            {
                "id": response["id"],
                "object": "chat.completion.chunk",
                "usage": response["usage"],
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
                ],
            },
        )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path == "/search/v1/messages":
            self.send_error(501, "Offline model fixture does not provide web search")
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with Path("/logs/agent/wire_requests.jsonl").open("a") as f:
            f.write(json.dumps(body) + "\n")
        try:
            anthropic, response = fixture(body)
        except ValueError as error:
            self.send_error(400, str(error))
            return
        with Path("/logs/agent/wire_responses.jsonl").open("a") as f:
            f.write(json.dumps(response) + "\n")
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/event-stream" if body.get("stream") else "application/json",
        )
        self.end_headers()
        if body.get("stream"):
            for event, value in stream_events(anthropic, response):
                self.wfile.write(
                    (
                        (f"event: {event}\n" if event else "")
                        + "data: "
                        + json.dumps(value)
                        + "\n\n"
                    ).encode()
                )
            if not anthropic:
                self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.wfile.write(json.dumps(response).encode())


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18765), Handler).serve_forever()
