import asyncio
import io
import json
import sys

import pytest
from harbor.models.trial.config import TrialConfig

from agent_harness_eval.guardian import model_driver
from agent_harness_eval.guardian.config import GuardianConfig
from agent_harness_eval.guardian.decisions import parse_decision
from agent_harness_eval.guardian.driver import read_evidence
from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.guardian.supervisor import supervise


def call(name, args, key="a"):
    return {
        "id": key,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_decision_narrative_never_authorizes_termination():
    assert parse_decision({"assessment": "please terminate"}).decision == "continue"
    with pytest.raises(ValueError):
        parse_decision({"decision": "terminate", "summary": "stuck"})
    assert (
        parse_decision(
            {
                "decision": "terminate",
                "summary": "broken process",
                "evidence": ["PID exited"],
            }
        ).decision
        == "terminate"
    )


@pytest.mark.parametrize(
    "legacy", ["wait", "false_positive", "recovered", "inconclusive"]
)
def test_legacy_decisions_normalize_without_exposing_extra_model_choices(legacy):
    assert parse_decision({"decision": legacy}).model_dump() == {
        "decision": "continue",
        "summary": "",
        "evidence": [],
    }
    assert model_driver.FINAL_PARAMETERS["properties"]["decision"]["enum"] == [
        "continue",
        "terminate",
    ]


def test_evidence_read_is_bounded_and_cannot_escape_incident(tmp_path):
    write(tmp_path / "initial.json", {"text": "a" * 30000})
    first = read_evidence(tmp_path, {"name": "initial.json"})
    assert len(first["text"]) == 16384 and first["truncated"]
    second = read_evidence(
        tmp_path, {"name": "initial.json", "offset": first["next_offset"]}
    )
    assert not second["truncated"]
    with pytest.raises(ValueError):
        read_evidence(tmp_path, {"name": "../../secret"})


def test_multiple_calls_are_validated_before_any_is_executed():
    message = {
        "tool_calls": [call("snapshot", {}), call("exec", {"command": "pwd"}, "b")]
    }
    assert [r[1] for r in model_driver.validate_calls(message, 3)] == [
        "snapshot",
        "exec",
    ]
    message["tool_calls"].append(call("unknown", {}, "c"))
    with pytest.raises(ValueError, match="unknown_tool"):
        model_driver.validate_calls(message, 4)


def test_text_only_response_is_corrected_and_multiple_calls_run_serially(
    tmp_path, monkeypatch
):
    decision = {
        "decision": "continue",
        "summary": "测试仍在运行",
        "evidence": ["CPU increased"],
    }
    replies = iter(
        [
            {"role": "assistant", "content": "I should inspect"},
            {
                "role": "assistant",
                "reasoning_content": "inspect both",
                "tool_calls": [
                    call("snapshot", {}),
                    call("evidence", {"name": "incident.json"}, "b"),
                ],
            },
            {"role": "assistant", "tool_calls": [call("final", decision, "c")]},
        ]
    )
    histories = []

    def request(model, messages, audit=None):
        histories.append(json.loads(json.dumps(messages)))
        return next(replies)

    monkeypatch.setattr(model_driver, "request_message", request)
    context = {
        "incident_directory": str(tmp_path),
        "max_actions": 4,
        "instructions": "fixture",
        "model": {},
    }
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(context)
            + '\n{"result":{"running":true}}\n{"result":{"text":"evidence"}}\n'
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    model_driver.main()
    assert [json.loads(line)["tool"] for line in output.getvalue().splitlines()] == [
        "snapshot",
        "evidence",
        "final",
    ]
    assert histories[-1][-3]["reasoning_content"] == "inspect both"
    assert [m["tool_call_id"] for m in histories[-1] if m["role"] == "tool"] == [
        "a",
        "b",
    ]
    assert "no_native_tool_calls" in (tmp_path / "model-responses.jsonl").read_text()


def test_repeated_invalid_output_is_bounded(tmp_path, monkeypatch):
    context = {
        "incident_directory": str(tmp_path),
        "max_actions": 4,
        "instructions": "fixture",
        "model": {},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(context) + "\n"))
    monkeypatch.setattr(
        model_driver, "request_message", lambda *a: {"content": "no tool"}
    )
    with pytest.raises(RuntimeError, match="retries_exhausted"):
        model_driver.main()
    assert len((tmp_path / "model-responses.jsonl").read_text().splitlines()) == 3


def test_model_audit_preserves_finish_reason_and_redacts_secret(tmp_path, monkeypatch):
    from contextlib import closing

    monkeypatch.setenv("GUARD_TEST_KEY", "fixture-private-key")
    payload = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "role": "assistant",
                    "content": "fixture-private-key" + "x" * 70000,
                },
            }
        ],
        "usage": {"completion_tokens": 30000},
    }
    monkeypatch.setattr(
        model_driver,
        "urlopen",
        lambda *a, **kw: closing(io.BytesIO(json.dumps(payload).encode())),
    )
    audit = tmp_path / "responses.jsonl"
    model_driver.request_message(
        {"name": "test", "max_tokens": 32768, "api_key_env": "GUARD_TEST_KEY"},
        [],
        audit,
    )
    record = json.loads(audit.read_text())
    assert record["finish_reason"] == "length" and record["tool_call_count"] == 0
    assert record["truncated"] and "fixture-private-key" not in audit.read_text()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process isolation")
@pytest.mark.parametrize(
    "response,expected",
    [
        (
            {
                "decision": "continue",
                "summary": "legitimate wait",
            },
            "continued",
        ),
        (
            {"decision": "false_positive", "summary": "tool already returned"},
            "continued",
        ),
        (
            {"decision": "inconclusive", "summary": "insufficient evidence"},
            "continued",
        ),
        (None, "investigation_failed"),
        (
            {
                "decision": "terminate",
                "summary": "main process exited; cannot resume",
                "evidence": ["PID absent in two snapshots"],
            },
            "terminated",
        ),
    ],
)
def test_supervisor_continues_or_terminates_and_separates_driver_failure(
    tmp_path, response, expected
):
    class Target:
        stopped = False

        async def snapshot(self):
            return {"logs": {"events.jsonl": {"size": 1, "text": ""}}}

        async def stop(self):
            self.stopped = True
            return {"resources_released": True}

    target = Target()
    trial = TrialConfig(
        task={"path": tmp_path / "task"},
        trial_name="wait",
        trials_dir=tmp_path / "jobs",
        agent={"name": "nop", "override_timeout_sec": 10},
    )
    root = trial.trials_dir / trial.trial_name
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys,json\njson.loads(sys.stdin.readline())\n"
        + (
            "print("
            + repr(json.dumps({"id": "done", "tool": "final", "arguments": response}))
            + ",flush=True)\n"
            if response
            else "raise ValueError('driver failed')\n"
        )
    )
    worker = (
        "import json,pathlib,time;r=pathlib.Path("
        + repr(str(root))
        + ");(r/'guardian-state.json').write_text(json.dumps({'phase':'agent-start','since':time.monotonic()}));time.sleep(3);(r/'result.json').write_text(json.dumps({'exception_info':None}))"
    )
    config = GuardianConfig(
        mode="assist",
        command=[sys.executable, str(driver)],
        version="test",
        poll_seconds=0.1,
        idle_seconds=1,
        confirmations=1,
        verification_seconds=1,
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
    if expected == "terminated":
        assert target.stopped
        assert (
            read(root / "result.json")["exception_info"]["exception_message"]
            == "guardian_requested_termination"
        )
    else:
        assert not target.stopped
        assert read(root / "result.json")["exception_info"] is None
    assert (
        read(next((root / "incidents").iterdir()) / "outcome.json")["status"]
        == expected
    )
