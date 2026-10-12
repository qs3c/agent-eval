"""Progress observations; a quiet interval is suspicion, not a deadlock proof."""

import json
from datetime import datetime


class Monitor:
    def __init__(self, config):
        self.config = config
        self.signature = None
        self.last_progress = None
        self.pending = {}
        self.seen = set()
        self.suspicions = 0

    def observe(self, snapshot, clock, agent_id):
        logs = snapshot.get("logs", {})
        # Fixed probe output does not count as Agent progress. CPU alone is not progress.
        signature = tuple(
            (name, value.get("size"))
            for name, value in sorted(logs.items())
            if name != "stderr.log" and "size" in value
        )
        changed = signature != self.signature and bool(signature)
        if self.last_progress is None or changed:
            self.last_progress = clock
        self.signature = signature
        for line in logs.get("events.jsonl", {}).get("text", "").splitlines():
            try:
                e = json.loads(line)
                if not isinstance(e, dict) or not isinstance(e.get("data"), dict):
                    continue
                key = (e.get("seq"), e.get("time"), e.get("type"))
                if key in self.seen:
                    continue
                self.seen.add(key)
                d = e["data"]
                if e.get("type") == "tool_call" and d.get("name") == "exec":
                    args = json.loads(d.get("arguments", "{}"))
                    if args.get("run_in_background"):
                        continue
                    timeout = args.get("timeout", 120 if agent_id == "bkcrab" else None)
                    if type(timeout) not in (int, float) or timeout <= 0:
                        continue
                    self.pending[d["id"]] = (
                        datetime.fromisoformat(e["time"]).timestamp(),
                        timeout,
                    )
                elif e.get("type") == "tool_result":
                    self.pending.pop(d.get("id"), None)
            except (ValueError, TypeError, KeyError):
                continue
        if len(self.seen) > 20000:
            self.seen.clear()
        overdue = [
            key
            for key, (start, timeout) in self.pending.items()
            if snapshot.get("time_unix", 0) - start
            > timeout + self.config.tool_grace_seconds
        ]
        reason = (
            "tool_overdue"
            if overdue
            else (
                "no_observed_progress"
                if clock - self.last_progress >= self.config.idle_seconds
                else None
            )
        )
        self.suspicions = self.suspicions + 1 if reason else 0
        confirmed = reason if self.suspicions >= self.config.confirmations else None
        return confirmed, changed
