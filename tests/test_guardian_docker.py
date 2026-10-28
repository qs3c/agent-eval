"""Opt-in, offline Linux/Docker acceptance tests; no model API calls."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from harbor.models.trial.config import TrialConfig

from agent_harness_eval.config import load
from agent_harness_eval.guardian.config import GuardianConfig
from agent_harness_eval.guardian.docker import DockerTarget
from agent_harness_eval.guardian.evidence import incident_summary, read
from agent_harness_eval.guardian.monitor import Monitor
from agent_harness_eval.guardian.supervisor import supervise
from agent_harness_eval.run import run_experiment

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("GUARDIAN_DOCKER_TESTS") != "1",
    reason="opt in with GUARDIAN_DOCKER_TESTS=1 on Linux with Docker",
)
IMAGE = "python:3.12.13-slim-bookworm"


@pytest.fixture
def target(tmp_path):
    name = "guardian-test-" + uuid4().hex[:12]
    backend = DockerTarget(name, timeout=5)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "events.jsonl").write_text("")
    cid = subprocess.check_output(
        [
            "docker",
            "run",
            "-d",
            "--network",
            "none",
            "--label",
            "com.docker.compose.project=" + backend.project,
            "--label",
            "com.docker.compose.service=main",
            "-v",
            f"{logs}:/logs/agent",
            IMAGE,
            "sleep",
            "300",
        ],
        text=True,
    ).strip()
    try:
        yield name, backend, logs, cid
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)


def test_real_probe_and_bounded_shell(target):
    _, backend, _, _ = target

    async def check():
        snap = await backend.snapshot()
        assert snap["processes"] and "events.jsonl" in snap["logs"]
        assert "Env" not in json.dumps(snap["container"])
        result = await backend.exec("yes x", 0.3)
        assert result["timed_out"] and result["truncated"]
        assert len(result["stdout"]) <= 65536
        # Shell exits but a descendant holds stdout: still bounded and killed.
        result = await backend.exec("sleep 60 & echo $! > /tmp/child", 0.3)
        assert result["timed_out"]
        result = await backend.exec("cat /tmp/child", 1)
        child = int(result["stdout"].strip())
        snap = await backend.snapshot()
        assert all(p["state"] == "Z" for p in snap["processes"] if p["pid"] == child)
        backend.active = lambda: False
        with pytest.raises(RuntimeError, match="expired"):
            await backend.exec("touch /tmp/forbidden", 1)

    asyncio.run(check())


def test_real_incremental_observer_is_independent_of_diagnostic_snapshots(target):
    _, backend, logs, _ = target
    path = logs / "events.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "tool_call",
                "time": "2026-01-01T00:00:00Z",
                "data": {
                    "id": "old-call",
                    "name": "exec",
                    "arguments": '{"timeout":120}',
                },
            }
        )
        + "\n"
    )

    async def check():
        monitor = Monitor(GuardianConfig(confirmations=1))
        monitor.observe(await backend.observe(), 0, "sample_agent")
        assert "old-call" in monitor.pending
        with path.open("a") as stream:
            stream.write(
                json.dumps({"type": "tool_result", "data": {"id": "old-call"}}) + "\n"
            )
            stream.write(
                json.dumps({"type": "model_request", "data": {"prompt": "x" * 200000}})
                + "\n"
            )
        await backend.snapshot()  # A guardian inspection cannot steal return events.
        snapshot = await backend.observe()
        assert snapshot["telemetry"]["complete"]
        assert monitor.observe(snapshot, 1, "sample_agent")[0] is None
        assert not monitor.pending

    asyncio.run(check())


def test_real_container_readonly_inspection_and_recording(target, tmp_path):
    name, backend, logs, _ = target
    adapter = tmp_path / "adapter.py"
    adapter.write_text("""import json,sys
json.loads(sys.stdin.readline())
print(json.dumps({"id":"1","tool":"exec","arguments":{"command":"pwd"}}),flush=True)
json.loads(sys.stdin.readline())
print(json.dumps({"id":"2","tool":"finish","arguments":{"summary":"Released fixture stall","assessment":"recovered"}}),flush=True)
""")
    cfg = TrialConfig(
        task={"path": tmp_path / "task"},
        trial_name=name,
        trials_dir=tmp_path / "jobs",
        agent={"name": "nop", "override_timeout_sec": 15},
    )
    root = cfg.trials_dir / name
    worker = tmp_path / "worker.py"
    worker.write_text(f"""import json,time,pathlib
root=pathlib.Path({str(root)!r})
(root/'guardian-state.json').write_text(json.dumps({{"phase":"agent-start","since":time.monotonic()}}))
time.sleep(4)
(root/'result.json').write_text(json.dumps({{"task_name":"fixture","trial_name":{name!r},"exception_info":None}}))
""")
    guardian = GuardianConfig(
        mode="assist",
        command=[sys.executable, str(adapter)],
        version="fixture-1",
        idle_seconds=1,
        confirmations=1,
        poll_seconds=0.1,
        command_seconds=2,
        diagnostic_seconds=5,
        verification_seconds=3,
    )
    asyncio.run(
        supervise(
            cfg,
            guardian,
            "fixture",
            backend=backend,
            worker_argv=[sys.executable, str(worker)],
        )
    )
    summary = incident_summary(root)
    assert summary["assisted"]
    assert summary["incidents"][0]["outcome"]["status"] in {
        "continued",
        "trial_ended",
    }
    incident = next((root / "incidents").iterdir())
    assert read(incident / "initial.json")["container"]["id"]
    assert '"command": "pwd"' in (incident / "actions.jsonl").read_text()
    assert read(root / "result.json")["exception_info"] is None


def test_real_container_stalled_driver_then_full_harbor_controls(target, tmp_path):
    name, backend, _, cid = target
    cfg = TrialConfig(
        task={"path": tmp_path / "task"},
        trial_name=name,
        trials_dir=tmp_path / "jobs",
        agent={"name": "nop", "override_timeout_sec": 3},
    )
    state = cfg.trials_dir / name / "guardian-state.json"
    worker = (
        "import pathlib,json,time;pathlib.Path("
        + repr(str(state))
        + ").write_text(json.dumps({'phase':'agent-start','since':time.monotonic()}));time.sleep(120)"
    )
    guardian = GuardianConfig(
        mode="assist",
        command=[sys.executable, "-c", "import time;time.sleep(120)"],
        version="silent-fixture",
        idle_seconds=1,
        confirmations=1,
        poll_seconds=0.1,
        diagnostic_seconds=1,
        verification_seconds=1,
        cleanup_seconds=5,
        command_seconds=2,
    )
    asyncio.run(
        supervise(
            cfg,
            guardian,
            "fixture",
            backend=backend,
            worker_argv=[sys.executable, "-c", worker],
        )
    )
    assert (
        read(cfg.trials_dir / name / "result.json")["exception_info"]["exception_type"]
        == "GuardianTrialTerminated"
    )
    assert (
        read(cfg.trials_dir / name / "result.json")["exception_info"][
            "exception_message"
        ]
        == "phase_deadline_exceeded: agent-start"
    )
    assert any(
        i["outcome"]["status"] == "investigation_failed"
        for i in incident_summary(cfg.trials_dir / name)["incidents"]
    )
    assert (
        subprocess.run(
            ["docker", "inspect", cid], capture_output=True, check=False
        ).returncode
        != 0
    )
    # Exercise real Harbor hooks, verification, cleanup and report after fallback.
    experiment = load(Path(__file__).resolve().parents[1] / "experiments/controls.json")
    experiment.guardian = GuardianConfig(poll_seconds=0.2)
    output = tmp_path / "harbor-controls"
    asyncio.run(run_experiment(experiment, output))
    report = read(output / "report.json")
    assert report["agents"]["positive"]["mean_on_scored"] == 1
    assert report["agents"]["negative"]["mean_on_scored"] == 0
    assert all(
        a["recorded"] == 2 and a["errors"] == 0 for a in report["agents"].values()
    )


def test_one_real_harbor_queue_continues_after_blocked_agent(tmp_path, monkeypatch):
    # Block the worker's event loop: Harbor's asyncio timeout cannot fire here.
    module = tmp_path / "guardian_fixture.py"
    module.write_text("""import time
from agent_harness_eval.agents.control import Control
class Agent(Control):
    async def run(self, instruction, environment, context):
        if "Leave no other files" not in instruction:
            time.sleep(120)
        await super().run(instruction, environment, context)
""")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(
        "PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", "")
    )
    experiment = load(Path(__file__).resolve().parents[1] / "experiments/controls.json")
    experiment.agents = experiment.agents[:1]
    experiment.agents[0].implementation = "guardian_fixture:Agent"
    experiment.concurrency = 1
    experiment.timeout_seconds = 2
    experiment.guardian = GuardianConfig(
        poll_seconds=0.1, cleanup_seconds=5, command_seconds=2
    )
    output = tmp_path / "queue"
    asyncio.run(run_experiment(experiment, output))
    report = read(output / "report.json")
    trials = {trial["task"]: trial for trial in report["trials"]}
    assert (
        trials["file-create"]["exception"]["exception_type"]
        == "GuardianTrialTerminated"
    )
    assert trials["file-create"]["guardian"]["assisted"]
    assert trials["file-preserve"]["metrics"]["reward"] == 1
    assert trials["file-preserve"]["exception"] is None
    assert not trials["file-preserve"]["guardian"]["assisted"]
