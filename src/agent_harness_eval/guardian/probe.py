"""Executed inside a task container; never reads process environment or memory."""

import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


def project_event(event):
    """Keep lifecycle facts, not model prompts or tool output, in the monitor stream."""
    if event.get("type") == "session_event":
        event = event.get("data", {}).get("event", {})
    kind, data = event.get("type"), event.get("data") or {}
    if kind in ("tool_result", "tool/result"):
        key = (
            data.get("id")
            if kind == "tool_result"
            else data.get("message", {}).get("toolCallId")
        )
        return {"kind": "return", "id": key} if key else None
    if kind not in ("tool_call", "tool/call"):
        return None
    name = data.get("name")
    if name not in ("exec", "bash", "job_output"):
        return None
    args = data.get("arguments", {})
    if isinstance(args, str):
        args = json.loads(args)
    if not isinstance(args, dict) or args.get("run_in_background"):
        return None
    if name == "job_output" and not args.get("wait"):
        return None
    timeout = args.get("timeout", 120) if name == "exec" else args.get("timeoutMs")
    if name == "job_output":
        timeout = min(args.get("timeout_ms", 30000), 600000)
    if name != "exec" and timeout is not None:
        timeout /= 1000
    stamp = event.get("time")
    start = (
        datetime.fromisoformat(stamp).timestamp()
        if isinstance(stamp, str)
        else stamp / 1000
    )
    return {
        "kind": "call",
        "id": data.get("id") or data.get("callId"),
        "name": name,
        "start": start,
        "timeout": timeout,
        "command": str(args.get("command", args.get("job_id", "")))[:2000],
    }


def read_events(
    path, cursor=None, byte_budget=2 * 1024 * 1024, line_limit=16 * 1024 * 1024
):
    """Read complete lines from a durable byte cursor; gaps invalidate pending state.

    Each call is bounded. Oversized records are drained in bounded chunks and
    explicitly reported as gaps, never silently treated as a complete stream.
    """
    previous = cursor or {}
    rows, gap = [], False
    try:
        with Path(path).open("rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = [stat.st_dev, stat.st_ino]
            offset = previous.get("offset", 0)
            if previous:
                stream.seek(max(0, offset - 64))
                anchor = hashlib.sha256(stream.read(min(offset, 64))).hexdigest()
                if (
                    previous.get("identity") != identity
                    or offset > stat.st_size
                    or anchor != previous.get("anchor")
                ):
                    offset, gap = 0, True
            dropping = bool(previous.get("dropping")) and not gap
            stream.seek(offset)
            initial = offset
            while (
                stream.tell() < stat.st_size
                and stream.tell() - initial < byte_budget
                and len(rows) < 256
            ):
                start = stream.tell()
                line = stream.readline(line_limit + 1)
                if dropping or len(line) > line_limit:
                    gap = True
                    dropping = not line.endswith(b"\n")
                    continue
                if not line.endswith(b"\n"):
                    stream.seek(start)
                    break
                try:
                    item = project_event(json.loads(line))
                    if item:
                        rows.append(item)
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    gap = True
            offset = stream.tell()
            stream.seek(max(0, offset - 64))
            anchor = hashlib.sha256(stream.read(min(offset, 64))).hexdigest()
        return {
            "events": rows,
            "gap": gap,
            "complete": offset >= stat.st_size and not dropping,
            "observed_size": stat.st_size,
            "cursor": {
                "identity": identity,
                "offset": offset,
                "anchor": anchor,
                "dropping": dropping,
            },
        }
    except OSError as error:
        return {
            "events": [],
            "gap": True,
            "complete": False,
            "error": str(error),
            "cursor": previous,
        }


def collect(event_cursor=None, monitor=False):
    processes = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit() or len(processes) >= 512:
            continue
        try:
            fields = (p / "stat").read_text().rsplit(")", 1)[1].split()
            fds = {}
            for f in list((p / "fd").iterdir())[:64]:
                try:
                    fds[f.name] = os.readlink(f)
                except OSError:
                    pass
            processes.append(
                {
                    "pid": int(p.name),
                    "ppid": int(fields[1]),
                    "state": fields[0],
                    "start_ticks": fields[19],
                    "cpu_ticks": int(fields[11]) + int(fields[12]),
                    "comm": (p / "comm").read_text().strip(),
                    "fds": fds,
                }
            )
        except (OSError, ValueError, IndexError):
            continue
    logs = {}
    for name in [
        "events.jsonl",
        "stderr.log",
        "wire_timing.jsonl",
        "wire_responses.jsonl",
    ]:
        try:
            p = Path("/logs/agent") / name
            with p.open("rb") as f:
                size = os.fstat(f.fileno()).st_size
                f.seek(max(0, size - 65536))
                logs[name] = {
                    "size": size,
                    "text": f.read(65536).decode("utf-8", "replace"),
                }
        except OSError as error:
            logs[name] = {"error": str(error)}
    result = {
        "time_unix": time.time(),
        "processes": processes,
        "logs": logs,
        "limits": {"processes": 512, "fds_per_process": 64, "log_tail_bytes": 65536},
    }
    if monitor:
        result["telemetry"] = read_events(
            Path("/logs/agent/events.jsonl"), event_cursor
        )
    return result


if __name__ == "__main__":
    options = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(collect(options.get("cursor"), options.get("monitor", False))))
