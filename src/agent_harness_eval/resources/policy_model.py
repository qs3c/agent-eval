"""Trusted entry point; legacy relay remains untouched and independently usable."""

import io
import hashlib
import json
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer

if __package__:
    from . import policy_runtime
else:
    import policy_runtime


def handler(upstream, policy, digest, logs):
    counter = policy_runtime.Counter(logs / "policy-reservations.jsonl", policy["settings"]["budget"]["max_requests"])
    credit = getattr(upstream, "_policy_open_context", None)
    if credit is None:
        credit = threading.local()
        upstream._policy_open_context = credit
    if hasattr(upstream, "urlopen") and not getattr(upstream, "_policy_open_wrapped", False):
        original_open = upstream.urlopen
        def budgeted_open(*args, **kwargs):
            # Initial opening is already reserved at ingress. Transport retries
            # must reserve again; a retry cannot bypass the role's request cap.
            reservation = getattr(credit, "reservation", None)
            if reservation is None:
                raise RuntimeError("Upstream opening has no trusted policy reservation")
            if reservation["initial"]:
                reservation["initial"] = False
            elif not reservation["counter"].reserve(reservation["role"], reservation["digest"]):
                raise RuntimeError("Frozen role request budget exhausted before upstream retry")
            return original_open(*args, **kwargs)
        upstream.urlopen = budgeted_open
        upstream._policy_open_wrapped = True
    class Handler(upstream.Handler):
        def do_POST(self):
            protocol = {"/v1/chat/completions": "openai-chat", "/v1/messages": "anthropic-messages"}.get(self.path)
            if protocol is None:
                # Search has its own explicit admission/review path in the legacy
                # relay, and cannot consume an unbounded auxiliary route here.
                self.send_error(403, "Endpoint not admitted by this model policy")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32 * 1024 * 1024:
                    raise ValueError("Invalid request length")
                native = json.loads(self.rfile.read(length))
                if not isinstance(native, dict):
                    raise ValueError("Expected object")
                effective = policy_runtime.normalize(native, policy, protocol)
            except (ValueError, TypeError):
                self.send_error(400, "Request incompatible with frozen model policy")
                return
            if not counter.reserve(policy["role"], digest):
                self.send_error(429, "Frozen role request budget exhausted")
                return
            with counter.lock, (logs / "policy-requests.jsonl").open("a") as stream:
                stream.write(json.dumps({"role": policy["role"], "policy_digest": digest,
                    "protocol": protocol, "native_body": native, "body": effective}) + "\n")
            payload = json.dumps(effective).encode()
            self.rfile = io.BytesIO(payload)
            self.headers.replace_header("Content-Length", str(len(payload)))
            credit.reservation = {"initial": True, "counter": counter, "role": policy["role"], "digest": digest}
            try:
                return super().do_POST()
            finally:
                credit.reservation = None
    return Handler


def serve():
    import os
    import policy_upstream as upstream

    logs = Path("/logs/agent")
    directory = Path(os.environ.get("HARNESS_POLICY_DIRECTORY", "/installed-agent"))
    record = json.loads((directory / "model-policy.json").read_text())
    policy, digest = record["policy"], record["digest"]
    actual = hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if actual != digest:
        raise ValueError("Frozen model policy digest mismatch")
    # The wrapper already pins effective parameters; do not apply provider-
    # specific legacy normalization a second time.
    if hasattr(upstream, "normalize"):
        upstream.normalize = lambda body, messages, config, **kwargs: body
    server = ThreadingHTTPServer(("127.0.0.1", 18765), handler(upstream, policy, digest, logs))
    server.model_config = policy["model"]
    server.purpose = policy["role"]
    if policy["model"]["mode"] == "live":
        key = directory / "live.key"
        server.api_key = key.read_text().strip()
        key.unlink()
    server.serve_forever()


if __name__ == "__main__":
    serve()
