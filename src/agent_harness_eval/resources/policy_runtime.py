"""Dependency-free enforcement, copied beside the isolated model relay."""

import json
import threading
from pathlib import Path

PARAMETERS = {"temperature", "top_p", "seed", "reasoning_effort", "thinking", "output_config"}


def normalize(body, policy, protocol):
    if protocol not in policy["settings"]["protocols"]:
        raise ValueError("Model protocol is not admitted")
    result = dict(body)
    # Caller-supplied labels never select a role or another credential.
    for key in ("purpose", "role", "policy", "policy_digest"):
        result.pop(key, None)
    for key in PARAMETERS:
        result.pop(key, None)
    result.update(policy["settings"]["parameters"])
    result.update(model=policy["model"]["name"], max_tokens=policy["model"]["max_tokens"])
    return result


class Counter:
    def __init__(self, path, limit):
        self.path, self.limit = Path(path), limit
        self.lock = threading.Lock()
        self.count = len(self.path.read_text().splitlines()) if self.path.exists() else 0

    def reserve(self, role, policy_digest):
        with self.lock:
            if self.count >= self.limit:
                return False
            with self.path.open("a") as stream:
                stream.write(json.dumps({"ordinal": self.count + 1, "role": role, "policy_digest": policy_digest}) + "\n")
                stream.flush()
            self.count += 1
            return True
