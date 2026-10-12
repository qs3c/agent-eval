"""Chat Completions driver for the supervisor's bounded JSONL tool protocol."""

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ..resources.live_model import upstream_url

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "snapshot",
            "description": "Inspect current trial state",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec",
            "description": "Execute a diagnostic or recovery command within this trial",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish with your diagnosis",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "assessment": {"type": "string"},
                },
                "required": ["summary", "assessment"],
            },
        },
    },
]


def request_message(model, messages):
    body = {
        "model": model["name"],
        "max_tokens": model["max_tokens"],
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "required",
        "parallel_tool_calls": False,
    }
    request = Request(
        upstream_url(model),
        json.dumps(body).encode(),
        {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ[model["api_key_env"]],
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)["choices"][0]["message"]
    except HTTPError as error:
        raise RuntimeError(f"守护模型请求失败，HTTP {error.code}") from None


def main():
    context = json.loads(sys.stdin.readline())
    evidence = {}
    for name in ("incident.json", "initial.json"):
        path = Path(context["incident_directory"]) / name
        if path.is_file() and not path.is_symlink():
            with path.open() as stream:
                evidence[name] = stream.read(65536)
    messages = [
        {"role": "system", "content": context["instructions"]},
        {
            "role": "user",
            "content": json.dumps({"context": context, "evidence": evidence}),
        },
    ]
    for index in range(context["max_actions"]):
        message = request_message(context["model"], messages)
        calls = message.get("tool_calls", [])
        if len(calls) != 1:
            raise ValueError("守护模型每次必须返回一个工具调用")
        call = calls[0]
        messages.append(message)
        tool = call["function"]["name"]
        args = json.loads(call["function"]["arguments"])
        print(
            json.dumps({"id": f"model-{index}", "tool": tool, "arguments": args}),
            flush=True,
        )
        if tool == "finish":
            return
        response = json.loads(sys.stdin.readline())
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(response["result"]),
            }
        )


if __name__ == "__main__":
    main()
