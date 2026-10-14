"""A diagnostic can finish after solve time ends without prolonging execution."""

import asyncio
import sys

import pytest
from harbor.models.trial.config import TrialConfig

from agent_harness_eval.guardian.config import GuardianConfig
from agent_harness_eval.guardian.evidence import read
from agent_harness_eval.guardian.supervisor import supervise


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subprocess isolation")
@pytest.mark.parametrize("worker_ends", [True, False])
def test_diagnostic_window_survives_worker_exit_or_outer_deadline(
    tmp_path, worker_ends
):
    class Target:
        def __init__(self):
            self.calls = []
            self.stopped = False

        async def snapshot(self):
            return {"logs": {"events.jsonl": {"size": 1, "text": ""}}}

        async def exec(self, command, timeout):
            self.calls.append(command)
            return {}

        async def stop(self):
            self.stopped = True
            return {"resources_released": True}

    target = Target()
    trial = TrialConfig(
        task={"path": tmp_path / "task"},
        trial_name="window",
        trials_dir=tmp_path / "jobs",
        agent={"name": "nop", "override_timeout_sec": 2},
    )
    root = trial.trials_dir / trial.trial_name
    worker = (
        "import pathlib,json,time;r=pathlib.Path(" + repr(str(root)) + ");"
        "(r/'guardian-state.json').write_text(json.dumps({'phase':'agent-start','since':time.monotonic()}));"
        + (
            "time.sleep(2);(r/'guardian-state.json').write_text(json.dumps({'phase':'end','since':time.monotonic()}));"
            "(r/'result.json').write_text(json.dumps({'exception_info':None}))"
            if worker_ends
            else "time.sleep(30)"
        )
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import json,sys,time\njson.loads(sys.stdin.readline())\ntime.sleep(3)\n"
        "print(json.dumps({'id':'exec','tool':'exec','arguments':{'command':'must not run'}}),flush=True)\n"
        "response=json.loads(sys.stdin.readline());assert 'error' in response['result']\n"
        "print(json.dumps({'id':'state','tool':'evidence','arguments':{'name':'guardian-state.json'}}),flush=True)\n"
        "response=json.loads(sys.stdin.readline());assert 'text' in response['result']\n"
        "print(json.dumps({'id':'done','tool':'final','arguments':{'decision':'continue','summary':'调查完成，作答已经结束','evidence':['state']}}),flush=True)\n",
        encoding="utf-8",
    )
    config = GuardianConfig(
        mode="assist",
        command=[sys.executable, str(driver)],
        version="fixture",
        poll_seconds=0.1,
        idle_seconds=1,
        confirmations=1,
        cleanup_seconds=1,
        diagnostic_seconds=6,
        max_incidents=1,
    )
    asyncio.run(
        supervise(
            trial,
            config,
            "fixture",
            backend=target,
            worker_argv=[sys.executable, "-c", worker],
        )
    )
    incident = next((root / "incidents").iterdir())
    assert read(incident / "driver-result.json")["status"] == "finished"
    assert (
        read(incident / "outcome.json")["status"] == "diagnosis_completed_after_agent"
    )
    assert read(incident / "diagnosis.json")["summary"] == "调查完成，作答已经结束"
    assert target.calls == []
    if worker_ends:
        assert read(root / "result.json")["exception_info"] is None
    else:
        assert target.stopped
        assert (
            read(root / "result.json")["exception_info"]["exception_message"]
            == "phase_deadline_exceeded: agent-start"
        )
