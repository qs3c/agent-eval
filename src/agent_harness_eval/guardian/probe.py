"""Executed inside a task container; never reads process environment or memory."""

import json
import os
import time
from pathlib import Path


def collect():
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
    return {
        "time_unix": time.time(),
        "processes": processes,
        "logs": logs,
        "limits": {"processes": 512, "fds_per_process": 64, "log_tail_bytes": 65536},
    }


if __name__ == "__main__":
    print(json.dumps(collect()))
