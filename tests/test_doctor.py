import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_harness_eval import doctor


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(doctor.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: "/bin/" + tool)
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: SimpleNamespace(free=30 * 1024**3))
    monkeypatch.delenv("DOCTOR_TEST_KEY", raising=False)
    calls = []

    def probe(argv):
        calls.append(argv)
        tool = Path(argv[0]).name
        return {"docker": '{"OSType":"linux","NCPU":4,"MemTotal":8589934592}',
                "git": "git version 2.43.0", "uv": "uv 0.13.0", "node": "v22.0.0",
                "npm": "10.9.0", "go": "go version go1.25.0 linux/amd64"}.get(tool)

    monkeypatch.setattr(doctor, "probe", probe)
    return calls


def rows(root, **kwargs):
    return {item["check_id"]: item for item in doctor.inspect(root, **kwargs)["checks"]}


def test_missing_docker_and_low_disk_are_actionable(host, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: None if tool == "docker" else "/bin/" + tool)
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: SimpleNamespace(free=1024))
    checks = rows(tmp_path)
    assert checks["docker"]["status"] == checks["disk"]["status"] == "fail"
    assert all(checks[k]["remediation"] for k in ("docker", "disk"))
    assert not any(Path(call[0]).name == "docker" for call in host)


def test_dsh_does_not_require_go_or_bkcrab_source(host, tmp_path, monkeypatch):
    monkeypatch.delenv("BKCRAB_SOURCE", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: None if tool == "go" else "/bin/" + tool)
    checks = rows(tmp_path, agents=["dsh"])
    assert "go" not in checks
    assert checks["node"]["status"] == checks["npm"]["status"] == "pass"
    assert not any(Path(call[0]).name == "go" for call in host)


def test_bkcrab_does_not_probe_node(host, tmp_path):
    checks = rows(tmp_path, agents=["bkcrab"])
    assert checks["go"]["status"] == "pass"
    assert "node" not in checks and "npm" not in checks


def test_bkcrab_requires_eval_entry(host, tmp_path, monkeypatch):
    monkeypatch.setenv("BKCRAB_SOURCE", str(tmp_path / "source"))
    assert rows(tmp_path, agents=["bkcrab"])["bkcrab-source"]["status"] == "fail"
    (tmp_path / "source/cmd/bkcrab-eval").mkdir(parents=True)
    assert rows(tmp_path, agents=["bkcrab"])["bkcrab-source"]["status"] == "pass"


def test_busy_port_warns_without_stopping_service(host, tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert rows(tmp_path, port=port)["port"]["status"] == "warn"
        assert listener.getsockname()[1] == port


def test_credentials_are_optional_for_mock_and_never_exposed(host, tmp_path, monkeypatch):
    assert rows(tmp_path, api_key_env="DOCTOR_TEST_KEY")["model-credentials"]["status"] == "pass"
    assert rows(tmp_path, live=True, api_key_env="DOCTOR_TEST_KEY")["model-credentials"]["status"] == "fail"
    monkeypatch.setenv("DOCTOR_TEST_KEY", "private-key-do-not-print")
    result = doctor.inspect(tmp_path, live=True, api_key_env="DOCTOR_TEST_KEY")
    assert "private-key-do-not-print" not in json.dumps(result)
    assert rows(tmp_path, live=True, api_key_env="DOCTOR_TEST_KEY")["model-connectivity"]["status"] == "warn"
    assert all(Path(call[0]).name in {"git", "uv", "docker"} for call in host)


@pytest.mark.parametrize("output", [None, "invalid", "[]", '{"OSType":"windows"}'])
def test_daemon_failure_or_non_linux_is_not_ready(host, tmp_path, monkeypatch, output):
    real_probe = doctor.probe
    monkeypatch.setattr(doctor, "probe", lambda argv: output if Path(argv[0]).name == "docker" else real_probe(argv))
    assert rows(tmp_path)["docker"]["status"] == "fail"


def test_old_selected_tool_fails_and_negative_threshold_rejected(host, tmp_path, monkeypatch):
    real_probe = doctor.probe
    monkeypatch.setattr(doctor, "probe", lambda argv: "v18.20.0" if Path(argv[0]).name == "node" else real_probe(argv))
    assert rows(tmp_path, agents=["dsh"])["node"]["status"] == "fail"
    with pytest.raises(ValueError):
        doctor.inspect(tmp_path, min_free_gib=-1)


def test_python_below_project_minimum_fails(host, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.sys, "version_info", (3, 11, 0))
    assert rows(tmp_path)["python"]["status"] == "fail"


def test_probe_has_timeout_and_does_not_leak_stderr(monkeypatch):
    def timeout(argv, **kwargs):
        assert kwargs["timeout"] == 10
        raise subprocess.TimeoutExpired(argv, 10, stderr="private-key")
    monkeypatch.setattr(doctor.subprocess, "run", timeout)
    assert doctor.probe(["docker", "info"]) is None


def test_cli_doctor_loads_without_execution_dependencies(tmp_path):
    # -S removes site packages: even broken/missing Harbor and Pydantic must not
    # prevent a user from checking an unsupported host.
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    proc = subprocess.run([sys.executable, "-S", "-m", "agent_harness_eval.cli", "doctor",
                           "--root", str(tmp_path / "absent"), "--json"],
                          env=env, capture_output=True, text=True, timeout=45)
    assert proc.returncode == 1, proc.stderr
    result = json.loads(proc.stdout)
    assert result["ok"] is False
    assert all(set(item) == {"check_id", "required_by", "status", "evidence", "remediation"}
               for item in result["checks"])
