import asyncio
import json
import sys
import time

import pytest
from harbor.models.trial.config import TrialConfig

from agent_harness_eval.guardian.config import GuardianConfig
from agent_harness_eval.guardian.driver import run_driver
from agent_harness_eval.guardian.evidence import capture, incident_summary, read, write
from agent_harness_eval.guardian.monitor import Monitor
from agent_harness_eval.guardian.supervisor import supervise


class FakeTarget:
    def __init__(self):
        self.calls = []
        self.stopped = False

    async def snapshot(self):
        return {
            "logs": {"events.jsonl": {"size": 1, "text": ""}},
            "time_unix": time.time(),
        }

    async def exec(self, command, timeout):
        self.calls.append(command)
        return {"returncode": 0, "stdout": "observed", "stderr": ""}

    async def stop(self):
        self.stopped = True
        return {"returncode": 0}


def test_config_requires_explicit_driver_and_version():
    assert GuardianConfig().mode == "observe"
    for kwargs in (
        {"mode": "assist"},
        {"poll_seconds": 0},
        {"command": ["x"]},
        {"mode": "assist", "command": ["x"], "version": "1", "pass_env": ["KEY=value"]},
    ):
        with pytest.raises(ValueError):
            GuardianConfig(**kwargs)


def test_capture_retains_partial_failure(tmp_path):
    class Broken:
        async def snapshot(self):
            raise RuntimeError("container disappeared")

    root = tmp_path / "incident"
    asyncio.run(capture(root, Broken(), {"reason": "overdue"}))
    assert read(root / "incident.json")["reason"] == "overdue"
    assert "disappeared" in read(root / "initial.json")["error"]
    with pytest.raises(FileExistsError):
        asyncio.run(capture(root, Broken(), {}))


def test_monitor_quiet_is_not_immediate_deadlock_and_progress_resets():
    m = Monitor(GuardianConfig(idle_seconds=10, confirmations=2))
    snap = {"logs": {"events.jsonl": {"size": 3, "text": ""}}}
    assert m.observe(snap, 0, "x") == (None, True)
    assert m.observe(snap, 11, "x")[0] is None
    assert m.observe(snap, 12, "x")[0] == "no_observed_progress"
    snap["logs"]["events.jsonl"]["size"] = 4
    assert m.observe(snap, 13, "x") == (None, True)


def test_monitor_pending_tool_and_return():
    m = Monitor(GuardianConfig(confirmations=1, tool_grace_seconds=0))
    call = {
        "type": "tool_call",
        "seq": 1,
        "time": "2026-01-01T00:00:00Z",
        "data": {
            "id": "c",
            "name": "exec",
            "arguments": '{"command":"sleep 99","timeout":1}',
        },
    }
    snap = {
        "time_unix": 1800000000,
        "logs": {"events.jsonl": {"size": 20, "text": json.dumps(call)}},
    }
    assert m.observe(snap, 0, "bkcrab")[0] == "tool_overdue"
    done = {
        "type": "tool_result",
        "seq": 2,
        "time": "2026-01-01T00:00:01Z",
        "data": {"id": "c"},
    }
    snap["logs"]["events.jsonl"] = {
        "size": 40,
        "text": json.dumps(call) + "\n" + json.dumps(done),
    }
    assert m.observe(snap, 1, "bkcrab")[0] is None


def driver(tmp_path, body):
    path = tmp_path / "driver.py"
    path.write_text(
        "import json,sys,time\ncontext=json.loads(sys.stdin.readline())\n" + body
    )
    return GuardianConfig(
        mode="assist",
        command=[sys.executable, str(path)],
        version="fixture-1",
        diagnostic_seconds=1,
        poll_seconds=0.1,
        verification_seconds=1,
        idle_seconds=1,
        confirmations=1,
        cleanup_seconds=1,
    )


def test_driver_direct_action_is_recorded_and_finish_is_only_a_claim(tmp_path):
    cfg = driver(
        tmp_path,
        """print(json.dumps({"id":"a","tool":"exec","arguments":{"command":"ps"}}),flush=True)
result=json.loads(sys.stdin.readline())
print(json.dumps({"id":"b","tool":"finish","arguments":{"assessment":"recovered"}}),flush=True)
""",
    )
    target = FakeTarget()
    result = asyncio.run(run_driver(cfg, tmp_path, target, lambda: True))
    assert target.calls == ["ps"]
    assert result["status"] == "finished"
    assert read(tmp_path / "assisted.json")["actor"] == "guardian"
    assert not (tmp_path / "outcome.json").exists()
    actions = [
        json.loads(l) for l in (tmp_path / "actions.jsonl").read_text().splitlines()
    ]
    assert [a["kind"] for a in actions] == ["request", "result", "request"]


@pytest.mark.parametrize(
    "body", ["time.sleep(30)", "print('invalid',flush=True);time.sleep(30)"]
)
def test_silent_or_invalid_driver_is_bounded(tmp_path, body):
    cfg = driver(tmp_path, body)
    start = time.monotonic()
    with pytest.raises((TimeoutError, ValueError)):
        asyncio.run(run_driver(cfg, tmp_path, FakeTarget(), lambda: True))
    assert time.monotonic() - start < 8


def test_phase_change_revokes_actions(tmp_path):
    cfg = driver(
        tmp_path,
        'print(json.dumps({"id":"a","tool":"exec","arguments":{"command":"kill 1"}}),flush=True)\n',
    )
    backend = FakeTarget()
    with pytest.raises(RuntimeError, match="expired"):
        asyncio.run(run_driver(cfg, tmp_path, backend, lambda: False))
    assert backend.calls == []
    assert not (tmp_path / "assisted.json").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux worker isolation")
def test_hung_trial_is_terminated_and_next_trial_runs(tmp_path):
    g = GuardianConfig(poll_seconds=0.1, setup_seconds=1, cleanup_seconds=1)
    target = FakeTarget()
    cfg = TrialConfig(
        task={"path": tmp_path / "task"},
        trial_name="hung",
        trials_dir=tmp_path / "jobs",
    )
    asyncio.run(
        supervise(
            cfg,
            g,
            "a",
            backend=target,
            worker_argv=[sys.executable, "-c", "import time;time.sleep(60)"],
        )
    )
    result = read(tmp_path / "jobs/hung/result.json")
    assert result["exception_info"]["exception_type"] == "GuardianTrialTerminated"
    assert target.stopped
    assert incident_summary(tmp_path / "jobs/hung")["assisted"]
    cfg.trial_name = "next"
    destination = tmp_path / "jobs/next/result.json"
    source = (
        "import json,pathlib;pathlib.Path("
        + repr(str(destination))
        + ").write_text(json.dumps({'task_name':'task','trial_name':'next','exception_info':None,'finished_at':'now'}))"
    )
    target = FakeTarget()
    asyncio.run(
        supervise(
            cfg, g, "a", backend=target, worker_argv=[sys.executable, "-c", source]
        )
    )
    assert read(destination)["exception_info"] is None
    assert not target.stopped


def test_historical_intervention_counts_as_assistance(tmp_path):
    write(tmp_path / "human-intervention.json", {"human_intervened": True})
    assert incident_summary(tmp_path)["assisted"]


def test_stderr_activity_is_not_recovery():
    monitor = Monitor(GuardianConfig(idle_seconds=1, confirmations=1))
    snapshot = {"logs": {"stderr.log": {"size": 1}}}
    assert monitor.observe(snapshot, 0, "fixture") == (None, False)
    snapshot["logs"]["stderr.log"]["size"] = 100
    assert monitor.observe(snapshot, 2, "fixture") == ("no_observed_progress", False)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux worker isolation")
def test_cancelled_supervisor_records_and_cleans(tmp_path):
    target = FakeTarget()
    cfg = TrialConfig(
        task={"path": tmp_path / "task"},
        trial_name="cancel",
        trials_dir=tmp_path / "jobs",
    )

    async def run():
        task = asyncio.create_task(
            supervise(
                cfg,
                GuardianConfig(poll_seconds=0.1),
                "fixture",
                backend=target,
                worker_argv=[sys.executable, "-c", "import time;time.sleep(60)"],
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert target.stopped
    assert (
        read(tmp_path / "jobs/cancel/result.json")["exception_info"][
            "exception_message"
        ]
        == "supervisor_cancelled"
    )


def test_incident_public_view_excludes_raw_output_and_symlinks(tmp_path):
    from agent_harness_eval.guardian.views import detail, incidents

    root = tmp_path / "run"
    event = root / "jobs/a/t/incidents/i"
    write(event / "incident.json", {"reason": "tool_overdue"})
    write(event / "initial.json", {"private": "raw reasoning"})
    (event / "actions.jsonl").write_text(
        json.dumps(
            {"kind": "result", "result": {"stdout": "raw reasoning", "returncode": 0}}
        )
        + "\n"
    )
    rows = incidents(root)["incidents"]
    data = detail(root, rows[0]["id"])
    assert data["actions"][0]["returncode"] == 0
    assert "raw reasoning" not in json.dumps(data)
    with pytest.raises(KeyError):
        detail(root, "../../outside")


def test_driver_action_budget(tmp_path):
    cfg = driver(
        tmp_path,
        """print(json.dumps({"id":"a","tool":"snapshot"}),flush=True)
sys.stdin.readline()
time.sleep(30)
""",
    )
    cfg.max_actions = 1
    with pytest.raises(RuntimeError, match="budget exhausted"):
        asyncio.run(run_driver(cfg, tmp_path, FakeTarget(), lambda: True))


@pytest.mark.skipif(sys.platform != "linux", reason="Linux worker isolation")
def test_recovery_claim_without_progress_does_not_keep_trial_alive(tmp_path):
    cfg = driver(
        tmp_path,
        'print(json.dumps({"id":"done","tool":"finish","arguments":{"assessment":"recovered"}}),flush=True)\n',
    )
    trial = TrialConfig(
        task={"path": tmp_path / "task"},
        trial_name="claim",
        trials_dir=tmp_path / "jobs",
        agent={"name": "nop", "override_timeout_sec": 60},
    )
    state = trial.trials_dir / trial.trial_name / "guardian-state.json"
    worker = (
        "import pathlib,json,time;pathlib.Path("
        + repr(str(state))
        + ").write_text(json.dumps({'phase':'agent-start','since':time.monotonic()}));time.sleep(60)"
    )
    target = FakeTarget()
    asyncio.run(
        supervise(
            trial,
            cfg,
            "fixture",
            backend=target,
            worker_argv=[sys.executable, "-c", worker],
        )
    )
    assert target.stopped
    summary = incident_summary(state.parent)
    assert summary["incidents"][0]["outcome"]["reason"] == "recovery_not_verified"


def test_assistance_preserves_rewards_and_original_result(tmp_path):
    from agent_harness_eval.config import Experiment
    from agent_harness_eval.guardian.supervisor import fallback_result
    from agent_harness_eval.report import report

    exp = Experiment(
        name="fixture",
        agents=[{"id": "a", "implementation": "nop", "version": "1"}],
        benchmark={"id": "fixture", "tasks": ["task"]},
    )
    write(
        tmp_path / "manifest.json",
        {
            "experiment": exp.model_dump(mode="json"),
            "tasks": {"task": {}},
            "evidence_kind": "fixture",
        },
    )
    root = tmp_path / "jobs/a/trial"
    original = {
        "task_name": "task",
        "trial_name": "trial",
        "exception_info": None,
        "verifier_result": {"rewards": {"reward": 1}},
    }
    write(root / "result.json", original)
    write(root / "incidents/i/incident.json", {"reason": "tool_overdue"})
    write(root / "incidents/i/assisted.json", {"actor": "guardian"})
    cfg = TrialConfig(
        task={"path": tmp_path / "task"}, trial_name="trial", trials_dir=root.parent
    )
    fallback_result(root, cfg, "do not replace", "now")
    assert read(root / "result.json") == original
    result = report(tmp_path)
    assert result["agents"]["a"]["mean_on_scored"] == 1
    assert result["agents"]["a"]["assisted"] == 1
    assert "externally assisted" in (tmp_path / "report.md").read_text()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux symlinks")
def test_incident_symlink_outside_trial_is_ignored(tmp_path):
    write(tmp_path / "outside/incident.json", {"reason": "private"})
    trial = tmp_path / "trial"
    (trial / "incidents").mkdir(parents=True)
    (trial / "incidents/link").symlink_to(
        tmp_path / "outside", target_is_directory=True
    )
    assert incident_summary(trial)["incidents"] == []


def test_separate_verifier_is_rejected_before_worker_launch(tmp_path):
    from agent_harness_eval.guardian.config import validate_tasks

    (tmp_path / "task.toml").write_text('[verifier]\nenvironment_mode = "separate"\n')
    with pytest.raises(ValueError, match="shared verifier"):
        validate_tasks([tmp_path])
