#!/usr/bin/env python3
"""Temporary, audited orphan-pipeline recovery for the existing SWE-20 run.

No retries, changes to the agent, or extensions to the task deadline.
Run under a user systemd service; --once is read-only unless --apply is given.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "artifacts/swe-live-20"
STATE = RUN / "watchdog"

# Executed inside the selected container, never on the host.
PROBE = r'''
import os, json, time
from pathlib import Path
def tail(path, limit):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2); size=f.tell(); f.seek(max(0,size-limit))
            data=f.read()
        return data.decode('utf-8', 'replace')
    except OSError: return ''
now=time.time()
boot=now-float(Path('/proc/uptime').read_text().split()[0])
hz=os.sysconf('SC_CLK_TCK')
procs=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        fields=(p/'stat').read_text().rsplit(')',1)[1].split()
        fds={}
        for f in (p/'fd').iterdir():
            try: fds[f.name]=os.readlink(str(f))
            except OSError: pass
        procs.append(dict(pid=int(p.name),ppid=int(fields[1]),state=fields[0],
            start_ticks=fields[19],started=boot+int(fields[19])/hz,
            comm=(p/'comm').read_text().strip(),
            command=(p/'cmdline').read_bytes().decode('utf-8','replace').replace('\0',' '),fds=fds))
    except (OSError,ValueError,IndexError): pass
print(json.dumps(dict(now=now,processes=procs,
    events=tail('/logs/agent/events.jsonl',2*1024*1024),
    stderr=tail('/logs/agent/stderr.log',16000))))
'''

KILL = r'''
import os,sys,json,signal,ctypes,platform
from pathlib import Path
def pidfd_open(pid):
    if hasattr(os,'pidfd_open'): return os.pidfd_open(pid)
    if platform.system()!='Linux' or platform.machine()!='x86_64':
        raise RuntimeError('pidfd fallback supports Linux x86_64 only')
    libc=ctypes.CDLL(None,use_errno=True)
    fd=libc.syscall(434,pid,0)
    if fd<0: raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()))
    return fd
def send(fd,sig):
    if hasattr(signal,'pidfd_send_signal'): return signal.pidfd_send_signal(fd,sig)
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.syscall(424,fd,sig,0,0)<0:
        raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()))
results=[]
for expected in json.loads(sys.argv[1]):
    pid=expected['pid']; handle=None
    try:
        # pidfds prevent a PID reused after verification from being signalled.
        handle=pidfd_open(pid)
        p=Path('/proc')/str(pid)
        stat=(p/'stat').read_text().rsplit(')',1)[1].split()
        if (stat[19]!=expected['start_ticks'] or int(stat[1])!=1
                or (p/'comm').read_text().strip()!=expected['comm']):
            results.append(dict(pid=pid,status='identity_changed')); continue
        send(handle,int(sys.argv[2]))
        results.append(dict(pid=pid,status='signalled'))
    except (ProcessLookupError,FileNotFoundError): results.append(dict(pid=pid,status='gone'))
    finally:
        if handle is not None: os.close(handle)
print(json.dumps(results))
'''


def utc():
    return datetime.now(timezone.utc).isoformat()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def command(args):
    return subprocess.check_output(args, text=True, timeout=25)


def probe(container):
    return json.loads(command(["docker", "exec", container, "python3", "-c", PROBE]))


def select_stuck(snapshot):
    """Require one overdue foreground exec, no shell, orphan output pipe to agent."""
    pending = {}
    for line in snapshot["events"].splitlines():
        try:
            event = json.loads(line)
            data = event.get("data", {})
            if event.get("type") == "tool_call":
                pending[data["id"]] = event
            elif event.get("type") == "tool_result":
                pending.pop(data.get("id"), None)
        except (ValueError, KeyError, TypeError):
            continue  # tail may begin/end in an incomplete JSON line
    if len(pending) != 1:
        return None
    event = next(iter(pending.values()))
    data = event["data"]
    if data.get("name") != "exec":
        return None
    args = json.loads(data["arguments"])
    if args.get("run_in_background") or args.get("sandbox"):
        return None
    start = datetime.fromisoformat(event["time"].replace("Z", "+00:00")).timestamp()
    timeout = args.get("timeout", 120)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = 120
    if snapshot["now"] - start < timeout + 45:
        return None
    procs = snapshot["processes"]
    agents = [p for p in procs if p["comm"] == "bkcrab-eval" and p["state"] != "Z"]
    if len(agents) != 1:
        return None
    agent = agents[0]
    # A surviving direct child could still be a legitimate active command.
    if any(p["ppid"] == agent["pid"] and p["state"] != "Z" for p in procs):
        return None
    pipes = {v for fd, v in agent["fds"].items() if int(fd) > 2 and v.startswith("pipe:[")}
    eligible = [p for p in procs if p["ppid"] == 1 and p["state"] != "Z"
                and p["started"] >= start - 2 and p["started"] <= start + timeout + 2
                and p["pid"] != agent["pid"]]
    chosen = []
    # Walk pipe edges back from the agent's capture pipe through sort/head/etc.
    while True:
        added = [p for p in eligible if p not in chosen and pipes.intersection(p["fds"].values())]
        if not added:
            break
        chosen.extend(added)
        pipes.update(v for p in added for v in p["fds"].values() if v.startswith("pipe:["))
    if not chosen:
        return None
    return {"call_id": data["id"], "command": args["command"], "timeout_seconds": timeout,
            "age_seconds": snapshot["now"] - start, "targets": chosen}


def recover(container, trial, initial):
    # Re-read immediately before recording/signalling; stale evidence never authorizes a kill.
    before = probe(container)
    stuck = select_stuck(before)
    if not stuck or stuck["call_id"] != initial["call_id"]:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = RUN / "interventions" / (stamp + "-watchdog")
    evidence.mkdir(parents=True, mode=0o700)
    write(evidence / "before.json", before)
    record = {"timestamp_utc": utc(), "human_intervened": True, "automatic_watchdog": True,
              "authorization": "User requested periodic automatic recovery with intervention records.",
              "agent": "bkcrab", "trial": trial.name, "container_id": container,
              "reason": "Overdue foreground exec; shell gone; orphan processes retain agent output pipes.",
              "action": "SIGTERM; after 3 seconds SIGKILL only for same surviving orphan identities.",
              "evidence_directory": str(evidence), "detection": stuck, "status": "prepared",
              "evaluation_note": "Human-assisted run. Separate from unassisted scores. No retries or deadline extension."}
    write(evidence / "intervention.json", record)
    # A separate append-only event keeps previous manual markers intact.
    with (trial / "watchdog-interventions.jsonl").open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    marker = trial / "human-intervention.json"
    if not marker.exists():
        write(marker, {"human_intervened": True, "automatic_watchdog": True,
                       "records": "watchdog-interventions.jsonl", "evaluation_note": record["evaluation_note"]})
    with (RUN / "HUMAN_INTERVENTIONS.md").open("a") as f:
        f.write(f"\n- {record['timestamp_utc']} 自动看护干预 `{trial.name}`：超时 shell 遗留管道进程；"
                f"此题需按人工辅助单列。证据：`{evidence.relative_to(RUN)}/intervention.json`。\n")
    targets = json.dumps(stuck["targets"])
    record["term"] = json.loads(command(["docker", "exec", container, "python3", "-c", KILL, targets, str(signal.SIGTERM)]))
    write(evidence / "intervention.json", record)
    time.sleep(3)
    after = probe(container)
    alive = {(p["pid"], p["start_ticks"]) for p in after["processes"] if p["state"] != "Z"}
    remaining = [p for p in stuck["targets"] if (p["pid"], p["start_ticks"]) in alive]
    if remaining:
        record["kill"] = json.loads(command(["docker", "exec", container, "python3", "-c", KILL,
                                            json.dumps(remaining), str(signal.SIGKILL)]))
        time.sleep(1)
        after = probe(container)
    write(evidence / "after.json", after)
    alive = {(p["pid"], p["start_ticks"]) for p in after["processes"] if p["state"] != "Z"}
    record["status"] = ("cleanup_failed" if any((p["pid"], p["start_ticks"]) in alive for p in stuck["targets"])
                        else "target_processes_terminated")
    record["finished_at"] = utc()
    write(evidence / "intervention.json", record)
    with (trial / "watchdog-interventions.jsonl").open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def tick(apply):
    status = {"updated_at": utc(), "mode": "apply" if apply else "dry-run", "jobs": {}, "checks": []}
    for path in (RUN / "jobs").glob("*/result.json"):
        job = json.loads(path.read_text())
        status["jobs"][path.parent.name] = job.get("stats")
    exit_file = ROOT / "logs/swe-live-20-exit-code"
    if exit_file.exists():
        status.update(state="finished", pipeline_exit_code=exit_file.read_text().strip())
        return status
    pid = int((ROOT / "logs/swe-live-20-pipeline.pid").read_text().strip())
    process = Path(f"/proc/{pid}/cmdline")
    if not process.exists() or b"scripts/run-swe-live-20.sh" not in process.read_bytes():
        status.update(state="pipeline_stopped", error="Pipeline process disappeared without an exit-code file; no automatic restart.")
        return status
    # Exact name AND Compose project/service labels; never use a broad process kill.
    containers = command(["docker", "ps", "--filter", "label=com.docker.compose.service=main", "--format", "{{.ID}} {{.Names}}"])
    for line in containers.splitlines():
        short_id, name = line.split(maxsplit=1)
        for trial in (RUN / "jobs/bkcrab").glob("*"):
            if not trial.is_dir() or (trial / "result.json").exists():
                continue
            if name != trial.name.lower() + "__env-main-1":
                continue
            cfg = json.loads((trial / "config.json").read_text())
            if Path(cfg["trials_dir"]).resolve() != (RUN / "jobs/bkcrab").resolve():
                continue
            info = json.loads(command(["docker", "inspect", short_id]))[0]
            if info["Config"]["Labels"].get("com.docker.compose.project") != trial.name.lower() + "__env":
                continue
            container = info["Id"]
            snapshot = probe(container)
            stuck = select_stuck(snapshot)
            check = {"trial": trial.name, "overdue_orphan_pipeline": stuck}
            if stuck and apply:
                check["intervention"] = recover(container, trial, stuck)
            status["checks"].append(check)
    status["state"] = "watching"
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "watchdog.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write(STATE / "process.json", {"pid": os.getpid(), "started_at": utc()})
        while True:
            try:
                status = tick(args.apply)
            except Exception as exc:
                status = {"updated_at": utc(), "state": "error", "error": str(exc)}
            write(STATE / "status.json", status)
            print(json.dumps(status, ensure_ascii=False), flush=True)
            if args.once or status["state"] in ("finished", "pipeline_stopped"):
                return
            time.sleep(30)


if __name__ == "__main__":
    main()
