"""Review model-visible fetch results without modifying native agent tools."""

import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict

if __package__:
    from . import fetch_review, search_review
else:
    import fetch_review
    import search_review

POLICY = "swe-web-model-input/v2"
FETCH_TOOLS = {"web_fetch", "webfetch", "web_extract"}
SEARCH_TOOLS = {"web_search", "websearch"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        and not (set(b) - {"type", "text", "cache_control"}) for b in content
    ):
        return "\n\n".join(b["text"] for b in content)
    raise ValueError("unsupported tool content")


class Gate:
    """Per-trial bounded cache; no credentials or source text in cache/audit."""

    def __init__(self, limit=2048):
        self.cache = OrderedDict()
        self.limit = limit
        self.lock = threading.Lock()

    def filter(self, body, config, key, task, request_id, audit):
        if not config or not config.get("web_fetch", True):
            return body
        with self.lock:
            return self._filter(body, config, key, task, request_id, audit)

    def _filter(self, body, config, key, task, request_id, audit):
        result = copy.deepcopy(body)
        calls = {}
        results = []
        for message in result.get("messages", []):
            role = message.get("role")
            declarations = []
            if role == "assistant":
                for call in message.get("tool_calls", []):
                    function = call.get("function", {})
                    declarations.append((call.get("id"), function.get("name"), function.get("arguments")))
                for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                    if block.get("type") == "tool_use":
                        declarations.append((block.get("id"), block.get("name"), block.get("input")))
            for identity, name, arguments in declarations:
                if not isinstance(identity, str) or not identity:
                    continue
                try:
                    args = json.loads(arguments) if isinstance(arguments, str) else arguments
                    url = args.get("url", "https://unknown.invalid/") if isinstance(args, dict) else "https://unknown.invalid/"
                except (TypeError, ValueError):
                    args = None
                    url = "https://unknown.invalid/"
                value = (name, url, json.dumps(args, sort_keys=True) if isinstance(args, dict) else "{}")
                # Conflicting identifiers are ambiguous, never assumed safe.
                calls[identity] = value if identity not in calls or calls[identity] == value else None
            if role == "tool":
                results.append((message.get("tool_call_id"), message, False))
            elif role == "user" and isinstance(message.get("content"), list):
                results.extend((b.get("tool_use_id"), b, True) for b in message["content"] if b.get("type") == "tool_result")
        for identity, target, anthropic in results:
            call = calls.get(identity) if isinstance(identity, str) else None
            if call and call[0] and call[0] not in FETCH_TOOLS | SEARCH_TOOLS:
                continue
            started = time.monotonic()
            record = {"request_id": request_id, "tool_call_id": identity, "policy": POLICY,
                      "model": config["model"], "deny_threshold": config["deny_threshold"],
                      "content_sha256": digest(target.get("content")), "cache_hit": False,
                      "tool_name": call[0] if call else None}
            outcome = "unavailable"
            try:
                if not call or call[0] not in FETCH_TOOLS | SEARCH_TOOLS:
                    raise ValueError("unresolved tool identity")
                content = text_content(target.get("content"))
                cache_key = digest([POLICY, fetch_review.POLICY, config, task, call, content])
                if cache_key in self.cache:
                    outcome = self.cache[cache_key]
                    self.cache.move_to_end(cache_key)
                    record["cache_hit"] = True
                else:
                    allowed = True
                    if call[0] in SEARCH_TOOLS:
                        args = json.loads(call[2])
                        query = args.get("query", args.get("queries"))
                        if not query:
                            raise ValueError("missing search query")
                        decision = search_review.decide(config, key, task,
                            query if isinstance(query, str) else json.dumps(query))
                        record["intent_check"] = decision
                        allowed = not decision["denied"]
                    if allowed:
                        allowed = fetch_review.review(config, key, task,
                            {"stage": "content", "url": call[1], "content": content}, record)
                    outcome = "allowed" if allowed else "denied"
                    self.cache[cache_key] = outcome
                    while len(self.cache) > self.limit:
                        self.cache.popitem(last=False)
            except Exception as error:
                record["error_type"] = type(error).__name__
            record.update(outcome=outcome, elapsed_ms=(time.monotonic() - started) * 1000)
            audit(record)
            if outcome != "allowed":
                target["content"] = fetch_review.DENIED if outcome == "denied" else fetch_review.UNAVAILABLE
                if anthropic:
                    target["is_error"] = True
        return result
