import asyncio
import json
import sys

import pytest

from agent_harness_eval.platform_agents.executor import execute
from agent_harness_eval.platform_agents.protocol import PlatformAgentRequest
from agent_harness_eval.platform_agents.roles import guardian_command
from agent_harness_eval.platform_agents.model import prepare
from agent_harness_eval.policies.model import freeze
from agent_harness_eval.guardian.config import GuardianConfig
from agent_harness_eval.guardian.driver import run_driver


def test_guardian_multiturn_denies_writes_then_reads_and_concludes(tmp_path):
    script = tmp_path / "driver.py"
    script.write_text('''import json,sys,os
c=json.loads(sys.stdin.readline())
assert os.environ["HOME"].endswith("home-guardian")
for i,tool,args in [("1","exec",{"command":"echo answer > /testbed/answer.py"}),("2","snapshot",{})]:
 print(json.dumps({"id":i,"tool":tool,"arguments":args}),flush=True)
 r=json.loads(sys.stdin.readline())["result"]
 if i=="1": assert "error" in r
 else: assert r["state"]=="running"
print(json.dumps({"id":"3","tool":"final","arguments":{"decision":"continue","summary":"只读证据不足以判定死锁","evidence":["initial.json"]}}),flush=True)
''')
    class Backend:
        async def snapshot(self):
            return {"state": "running"}
        async def exec(self, command, timeout):
            raise AssertionError("Write crossed guardian boundary")
    config = GuardianConfig(mode="assist", command=[sys.executable, str(script)], version="fixture", diagnostic_seconds=5)
    result = asyncio.run(run_driver(config, tmp_path, Backend(), lambda: True))
    assert result["assessment"]["decision"] == "continue"
    assert not (tmp_path / "assisted.json").exists()
    assert json.loads((tmp_path / "platform-result.json").read_text())["status"] == "completed"
    assert len((tmp_path / "actions.jsonl").read_text().splitlines()) == 5


@pytest.mark.parametrize("command", ["kill 1", "cat /proc/1/environ", "python3 -c print(1)", "cat /logs/agent/../key", "ps; touch /testbed/a", "date --set=tomorrow", "cat /logs/agent/live.key", "tail /tests/test.sh"])
def test_inspection_scope_cannot_become_solution_writer(command):
    with pytest.raises(ValueError):
        guardian_command(command)


def test_model_budget_and_role_are_independent_and_persisted(tmp_path):
    model = {"mode": "live", "name": "fixed", "api_base": "https://fixture.invalid", "max_tokens": 32,
             "policy": {"parameters": {"temperature": 0}, "budget": {"max_requests": 1}}}
    body = {"model": "wrong", "purpose": "agent", "role": "improvement", "messages": []}
    for role in ("guardian", "diagnosis"):
        directory = tmp_path / role
        value = prepare(model, body, directory, role)
        assert value["model"] == "fixed" and value["temperature"] == 0
        assert "role" not in value and "purpose" not in value
        with pytest.raises(RuntimeError, match="budget exhausted"):
            prepare(model, body, directory, role)
        assert json.loads((directory / "policy-requests.jsonl").read_text())["role"] == role


def test_deadline_is_supervisor_owned_and_failure_evidence_survives(tmp_path):
    request = PlatformAgentRequest(role="integration", workspace=tmp_path, output=tmp_path / "attempt", timeout_seconds=.05, tools=("read", "final"))
    async def stuck(request, home):
        await asyncio.sleep(20)
    with pytest.raises(TimeoutError):
        asyncio.run(execute(request, stuck))
    result = json.loads((request.output / "platform-result.json").read_text())
    assert result["status"] == "interrupted" and result["cost_usd"] is None
    with pytest.raises(ValueError, match="role"):
        PlatformAgentRequest(role="integration", workspace=tmp_path, output=tmp_path / "bad", timeout_seconds=1,
                             model_policy=freeze({"name": "fixed", "max_tokens": 32}, "guardian"))


@pytest.mark.parametrize("role", ["improvement", "integration"])
def test_native_container_relay_pins_role_and_stops_at_budget(tmp_path, monkeypatch, role):
    import os
    import subprocess
    from types import SimpleNamespace
    from agent_harness_eval.rsi import native_adapter

    if os.environ.get("RSI_DOCKER_TESTS") != "1":
        pytest.skip("opt-in real platform relay Docker validation")
    workspace, evidence, output = (tmp_path / name for name in ("workspace", "evidence", "output"))
    for directory in (workspace, evidence, output):
        directory.mkdir()
    executable = tmp_path / "fixture-agent"
    executable.write_text('''#!/usr/local/bin/python3
import json,urllib.request,urllib.error
from pathlib import Path
body={"model":"caller-other-model","purpose":"agent","messages":[{"role":"user","content":"test"}],"tools":[{"type":"function","function":{"name":"write_file","parameters":{"properties":{"path":{"type":"string"},"content":{"type":"string"}}}}}]}
codes=[]
for _ in range(3):
 req=urllib.request.Request("http://127.0.0.1:18765/v1/chat/completions",data=json.dumps(body).encode(),headers={"Content-Type":"application/json"})
 try:
  with urllib.request.urlopen(req) as response: codes.append(response.status);response.read()
 except urllib.error.HTTPError as error: codes.append(error.code)
assert codes==[200,200,429],codes
Path("/output/diagnosis.json").write_text(json.dumps({"summary":"独立模型预算验证","codes":codes}))
''')
    executable.chmod(0o755)
    if role == "integration":
        executable.write_text(executable.read_text().replace('/output/diagnosis.json', '/output/integration.json') + '''
try:
 Path("/source/forbidden-write").write_text("must fail")
except OSError:
 pass
else:
 raise AssertionError("Integration modified upstream")
''')
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", "python:3.12.13-slim-bookworm", "--format", "{{json .RepoDigests}}"], text=True))[0]
    import tarfile
    from agent_harness_eval.platform_agents import releases
    executable.write_text(executable.read_text() + '\nPath("/logs/agent/agent-result.json").write_text(json.dumps({"schema_version":1,"status":"completed"}))\n')
    with tarfile.open(tmp_path / "agent.tar", "w") as archive:
        archive.add(executable, arcname="fixture-agent")
    recipe = {"id":"opencode", "source":{"repository":"https://github.com/example/fixture"},
        "build":{"image":image, "argv":["true"]},
        "entrypoint":{"argv":["python3","{agent_dir}/fixture-agent","{request_file}"]}}
    monkeypatch.setattr(releases, "resolve", lambda *args: (tmp_path, {"recipe":recipe}))
    monkeypatch.setattr(native_adapter, "credential", lambda root: "fixture-only-key")
    profile = {"mode": "mock", "name": "fixed-model", "max_tokens": 32, "policy": {"parameters": {"temperature": 0}, "budget": {"max_requests": 2}}}
    args = SimpleNamespace(root=tmp_path, executor="opencode", release="f" * 64, model="unused", timeout=30, image=image, model_policy_json=json.dumps(profile))
    context = {"workspace": str(workspace), "evidence_directory": str(evidence), "result_path": str(output / "diagnosis.json")}
    if role == "integration":
        source = tmp_path / "readonly-source"
        source.mkdir()
        context.update(purpose="integration_recipe", instruction="fixture native loop", source_directory=str(source))
    native_adapter.execute(args, context)
    assert json.loads((output / "diagnosis.json").read_text())["codes"] == [200, 200, 429]
    rows = [json.loads(line) for line in (output / "output/native/logs/policy-requests.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and all(row["role"] == role and row["body"]["model"] == "fixed-model" for row in rows)
