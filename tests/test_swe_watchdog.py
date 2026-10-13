"""Safety checks for the temporary experiment watchdog; no Docker required."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

spec = importlib.util.spec_from_file_location(
    "swe_watchdog", Path(__file__).parents[1] / "scripts/watch-swe-live-20.py"
)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def snapshot():
    event = {"type": "tool_call", "time": "2026-10-11T00:00:00Z", "data": {
        "id": "call-1", "name": "exec", "arguments": json.dumps({"command": "grep / | sort | head"})}}
    start = watch.datetime.fromisoformat("2026-10-11T00:00:00+00:00").timestamp()

    def proc(pid, ppid, comm, fds, started=start):
        return dict(pid=pid, ppid=ppid, comm=comm, state="S", start_ticks=str(pid),
                    started=started, fds=fds, command=comm)

    return {"now": start + 180, "events": json.dumps(event), "processes": [
        proc(50, 40, "bkcrab-eval", {"8": "pipe:[3]"}, start - 100),
        proc(100, 1, "grep", {"1": "pipe:[1]"}),
        proc(101, 1, "sort", {"0": "pipe:[1]", "1": "pipe:[2]"}),
        proc(102, 1, "head", {"0": "pipe:[2]", "1": "pipe:[3]"}),
        proc(103, 1, "unrelated", {"1": "/logs/other.log"}),
    ]}


def test_selects_only_expired_orphan_pipeline():
    data = snapshot()
    assert {p["pid"] for p in watch.select_stuck(data)["targets"]} == {100, 101, 102}
    # Neither elapsed time alone nor high CPU is a reason to kill a process.
    live = copy.deepcopy(data)
    live["processes"][1]["ppid"] = 50
    assert watch.select_stuck(live) is None
    fresh = copy.deepcopy(data)
    fresh["now"] -= 40
    assert watch.select_stuck(fresh) is None
    old = copy.deepcopy(data)
    for p in old["processes"][1:]:
        p["started"] -= 100
    assert watch.select_stuck(old) is None
    closed = copy.deepcopy(data)
    closed["events"] += '\n' + json.dumps({"type": "tool_result", "data": {"id": "call-1"}})
    assert watch.select_stuck(closed) is None


def test_respects_explicit_timeout_background_and_ambiguity():
    for args in ({"timeout": 600}, {"run_in_background": True}, {"sandbox": True}):
        data = snapshot()
        event = json.loads(data["events"])
        event["data"]["arguments"] = json.dumps({"command": "grep /", **args})
        data["events"] = json.dumps(event)
        assert watch.select_stuck(data) is None
    data = snapshot()
    event = json.loads(data["events"])
    event["data"]["id"] = "concurrent-call"
    data["events"] += '\n' + json.dumps(event)
    assert watch.select_stuck(data) is None


def test_truncated_event_tail_does_not_authorize_unrelated_cleanup():
    data = snapshot()
    data["events"] = 'partial garbage\n' + data["events"] + '\n{"unfinished":'
    assert len(watch.select_stuck(data)["targets"]) == 3
    data["processes"][0]["fds"] = {"1": "/logs/agent/stdout.log"}
    assert watch.select_stuck(data) is None


@pytest.mark.skipif(os.environ.get("SWE_WATCHDOG_DOCKER_TEST") != "1", reason="explicit isolated Docker test")
def test_real_orphan_cleanup_and_audit(tmp_path, monkeypatch):
    # No model requests. An isolated, network-disabled container reproduces the
    # killed shell + surviving pipeline + communicate() waiting on EOF failure.
    agent = r'''
import ctypes,json,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
ctypes.CDLL(None).prctl(15,b'bkcrab-eval',0,0,0)
Path('/logs/agent').mkdir(parents=True,exist_ok=True)
event={'type':'tool_call','time':datetime.now(timezone.utc).isoformat(),
       'data':{'id':'fixture-call','name':'exec','arguments':json.dumps({'command':'sleep 600 | cat','timeout':1})}}
Path('/logs/agent/events.jsonl').write_text(json.dumps(event)+'\n')
p=subprocess.Popen(['sh','-c','sleep 600 | cat'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
time.sleep(1);p.kill();p.communicate()
Path('/logs/agent/resumed').write_text('yes')
time.sleep(300)
'''
    init = "import subprocess,time; subprocess.Popen(['sleep','600']); subprocess.Popen(['python3','-c'," + repr(agent) + "]); time.sleep(300)"
    cid = subprocess.check_output([
        "docker", "run", "-d", "--rm", "--pull", "never", "--network", "none",
        "--read-only", "--tmpfs", "/logs", "--tmpfs", "/tmp", "--user", "root",
        "--entrypoint", "python3", "swebench/sweb.eval.x86_64.django_1776_django-9296:latest",
        "-c", init], text=True).strip()
    try:
        monkeypatch.setattr(watch, "RUN", tmp_path)
        trial = tmp_path / "jobs/bkcrab/fixture"
        trial.mkdir(parents=True)
        deadline = time.monotonic() + 55
        stuck = None
        while time.monotonic() < deadline:
            snap = watch.probe(cid)
            stuck = watch.select_stuck(snap)
            if stuck:
                break
            time.sleep(2)
        assert stuck, "Failed to detect the orphan pipeline"
        assert sorted(p["comm"] for p in stuck["targets"]) == ["cat", "sleep"]
        record = watch.recover(cid, trial, stuck)
        assert record["status"] == "target_processes_terminated"
        assert subprocess.check_output(["docker", "exec", cid, "cat", "/logs/agent/resumed"], text=True) == "yes"
        after = watch.probe(cid)
        # The unrelated sleep process must remain alive.
        assert len([p for p in after["processes"] if p["comm"] == "sleep" and p["state"] != "Z"]) == 1
        assert json.loads((trial / "human-intervention.json").read_text())["human_intervened"]
        assert len((trial / "watchdog-interventions.jsonl").read_text().splitlines()) == 2
        assert (Path(record["evidence_directory"]) / "before.json").exists()
        assert (Path(record["evidence_directory"]) / "after.json").exists()
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
