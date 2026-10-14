"""Chat Completions driver for the supervisor's bounded JSONL tool protocol."""

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ..resources.live_model import upstream_url
from .decisions import parse_decision
from .evidence import append, read

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

FINAL_PARAMETERS = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["continue", "terminate"],
        },
        "summary": {
            "type": "string",
            "description": "中文：现场事实、判断、实际操作、残余不确定性",
        },
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decision", "summary", "evidence"],
}
TOOLS[-1]["function"]["parameters"] = FINAL_PARAMETERS
TOOLS[-1]["function"]["description"] = "Compatibility alias of final"
TOOLS.extend(
    [
        {
            "type": "function",
            "function": {
                "name": "final",
                "description": "提交诊断结论，由监督进程执行决策",
                "parameters": FINAL_PARAMETERS,
            },
        },
        {
            "type": "function",
            "function": {
                "name": "evidence",
                "description": "读取本次 incident 的宿主机证据；exec 无法访问这些路径",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": ["incident.json", "initial.json", "host.json"],
                        },
                        "offset": {"type": "integer", "minimum": 0},
                    },
                    "required": ["name"],
                },
            },
        },
    ]
)


def request_message(model, messages, audit=None):
    body = {
        "model": model["name"],
        "max_tokens": model["max_tokens"],
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "required",
        "parallel_tool_calls": False,
    }
    # DeepSeek defaults to thinking, which rejects required tool choice.
    # This driver requires one protocol action per turn.
    if (
        model.get("api_base", "https://api.deepseek.com")
        .rstrip("/")
        .removesuffix("/v1")
        == "https://api.deepseek.com"
    ):
        body["thinking"] = {"type": "disabled"}
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
            raw = response.read(4 * 1024 * 1024 + 1)
        secret = os.environ[model["api_key_env"]]
        safe = raw.decode("utf-8", "replace").replace(secret, "[REDACTED]")
        # Store protocol facts separately so a long reasoning body cannot hide
        # the call count or finish reason in the bounded raw-response excerpt.
        try:
            payload = json.loads(raw) if len(raw) <= 4 * 1024 * 1024 else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        choices = payload.get("choices") or []
        if not isinstance(choices, list):
            choices = []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            message = {}
        if audit:
            append(
                audit,
                {
                    "kind": "model_response",
                    "finish_reason": choice.get("finish_reason"),
                    "tool_call_count": len(message.get("tool_calls") or [])
                    if isinstance(message.get("tool_calls") or [], list)
                    else None,
                    "usage": payload.get("usage"),
                    "response": safe[:65536],
                    "truncated": len(safe) > 65536,
                },
            )
        if not message:
            raise ValueError("missing_message_or_oversized_response")
        return message
    except HTTPError as error:
        if audit:
            append(audit, {"kind": "http_error", "status": error.code})
        raise RuntimeError(f"守护模型请求失败，HTTP {error.code}") from None


def compact_evidence(root):
    initial = read(root / "initial.json", {}) or {}
    # Valid structured summaries instead of slicing a JSON file mid-string.
    return {
        "incident": read(root / "incident.json", {}),
        "initial": {
            "captured_at": initial.get("captured_at"),
            "container": initial.get("container"),
            "error": initial.get("error"),
            "processes": initial.get("processes", [])[:24],
            "processes_truncated": len(initial.get("processes", [])) > 24,
            "logs": {
                name: {
                    **{k: v for k, v in value.items() if k != "text"},
                    "text": value.get("text", "")[-4000:],
                    "excerpt_truncated": len(value.get("text", "")) > 4000,
                }
                for name, value in initial.get("logs", {}).items()
            },
        },
        "evidence_files": ["incident.json", "initial.json", "host.json"],
    }


def validate_calls(message, remaining):
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list) or not calls:
        raise ValueError("no_native_tool_calls")
    if len(calls) > remaining:
        raise ValueError("tool_calls_exceed_remaining_budget")
    seen, validated = set(), []
    for index, call in enumerate(calls):
        key = call.get("id")
        if not isinstance(key, str) or key in seen:
            raise ValueError("invalid_or_duplicate_tool_call_id")
        seen.add(key)
        function = call.get("function") or {}
        name = function.get("name")
        if name not in {t["function"]["name"] for t in TOOLS}:
            raise ValueError("unknown_tool")
        args = json.loads(function.get("arguments", "{}"))
        if not isinstance(args, dict):
            raise TypeError("arguments_must_be_an_object")
        if name == "exec" and (
            not isinstance(args.get("command"), str)
            or not 0 < len(args["command"]) <= 16384
        ):
            raise ValueError("invalid_exec_command")
        if name == "evidence" and (
            args.get("name") not in ("incident.json", "initial.json", "host.json")
            or type(args.get("offset", 0)) is not int
            or args.get("offset", 0) < 0
        ):
            raise ValueError("invalid_evidence_request")
        if name in ("final", "finish"):
            if name == "final" and not all(
                k in args for k in ("decision", "summary", "evidence")
            ):
                raise ValueError("final_requires_decision_summary_evidence")
            parse_decision(args)
            if index != len(calls) - 1:
                raise ValueError("final_must_be_last")
        validated.append((call, name, args))
    if len(calls) == remaining and validated[-1][1] not in ("final", "finish"):
        raise ValueError("reserve_last_action_for_final")
    return validated


def main():
    context = json.loads(sys.stdin.readline())
    root = Path(context["incident_directory"])
    evidence = compact_evidence(root)
    audit = root / "model-responses.jsonl"
    messages = [
        {"role": "system", "content": context["instructions"]},
        {
            "role": "user",
            "content": json.dumps({"context": context, "evidence": evidence}),
        },
    ]
    used, errors = 0, 0
    while used < context["max_actions"]:
        try:
            message = request_message(context["model"], messages, audit)
            calls = validate_calls(message, context["max_actions"] - used)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            errors += 1
            append(
                audit,
                {
                    "kind": "protocol_error",
                    "error": str(error)[:1000],
                    "attempt": errors,
                },
            )
            if errors > 2:
                raise RuntimeError("guardian_protocol_retries_exhausted") from None
            # Do not append an invalid assistant/tool sequence to provider history.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"上次响应未执行：{str(error)[:500]}。请使用 API 原生函数工具调用，不能只输出文本或 JSONL。"
                        f"剩余 {context['max_actions'] - used} 次工具预算，最后一次必须 final；证据不足可 final(decision=continue)，在 summary 记录局限。"
                    ),
                }
            )
            continue
        errors = 0
        messages.append(message)  # Retain provider reasoning_content / replay data.
        for call, tool, args in calls:
            print(
                json.dumps({"id": f"model-{used}", "tool": tool, "arguments": args}),
                flush=True,
            )
            used += 1
            if tool in ("finish", "final"):
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
