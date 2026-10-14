import json
import os
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.rsi import native_adapter
from agent_harness_eval.rsi.executors import (
    ExecutorSettings,
    builtin_profile,
    builtin_profiles,
    catalog,
    credential,
    save_settings,
)
from agent_harness_eval.web import create_app


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    root = tmp_path / "console"
    root.mkdir()
    write(
        root / ".cache/rsi-native.json",
        {
            "image": "sha256:" + "a" * 64,
            "executors": {"bkcrab": "bk-version", "dsh": "dsh-version"},
        },
    )
    return root


def test_catalog_is_explicit_and_save_keeps_secrets_private(configured):
    before = catalog(configured)
    assert [o["id"] for o in before["options"]] == ["bkcrab", "dsh"]
    assert not before["configured"] and not before["ready"]
    result = save_settings(
        configured, ExecutorSettings(executor="dsh", api_key="unit-test-secret")
    )
    assert result["ready"] and result["executor"] == "dsh"
    assert "unit-test-secret" not in json.dumps(result)
    assert "api_key" not in read(configured / ".rsi/executor-settings.json")
    assert credential(configured) == "unit-test-secret"
    if os.name == "posix":
        assert (configured / ".rsi/executor-key").stat().st_mode & 0o777 == 0o600
    save_settings(configured, ExecutorSettings(executor="bkcrab", api_key=""))
    assert credential(configured) == "unit-test-secret"
    assert [p["id"] for p in builtin_profiles(configured)] == [
        "builtin-bkcrab",
        "builtin-dsh",
    ]


def test_only_intended_credential_is_reused(configured):
    shared = configured.parent / "agent-harness-eval"
    shared.mkdir()
    (shared / ".env").write_text(
        'OTHER_SECRET=unrelated\nDEEPSEEK_API_KEY="model-key"\n'
    )
    assert credential(configured) == "model-key"
    assert catalog(configured)["has_api_key"]
    assert not catalog(configured)["ready"]


def test_native_profile_separates_executor_from_target_and_freezes_model(configured):
    repository = configured.parent / "bkcrab"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "remote",
            "add",
            "origin",
            "https://github.com/example/bkcrab.git",
        ],
        check=True,
    )
    save_settings(
        configured,
        ExecutorSettings(executor="dsh", model="deepseek-flash", api_key="key"),
    )
    profile = builtin_profile(configured, "dsh", "bkcrab")
    assert profile.repository == repository
    assert profile.adapter.argv[profile.adapter.argv.index("--executor") + 1] == "dsh"
    assert profile.github_repository == "example/bkcrab"
    save_settings(
        configured, ExecutorSettings(executor="bkcrab", model="changed-model")
    )
    assert (
        "deepseek-flash" in profile.adapter.argv
        and "changed-model" not in profile.adapter.argv
    )
    with pytest.raises(ValueError):
        builtin_profile(configured, "../../bin/sh", "bkcrab")
    with pytest.raises(ValueError):
        builtin_profile(configured, "dsh", "../../repository")


def test_settings_api_rejects_commands_and_cross_origin(configured):
    client = TestClient(create_app(configured))
    headers = {"Host": "localhost"}
    response = client.put(
        "/api/rsi/executor-settings",
        headers=headers,
        json={"executor": "dsh", "api_key": "private-test-key"},
    )
    assert response.status_code == 200 and response.json()["ready"]
    assert "private-test-key" not in response.text
    assert "private-test-key" not in client.get("/api/rsi", headers=headers).text
    for invalid in [
        {"executor": "shell"},
        {"executor": "dsh", "argv": ["sh"]},
        {"executor": "bkcrab", "repository": "/etc"},
        {"executor": "dsh", "model": "x; command"},
    ]:
        assert (
            client.put(
                "/api/rsi/executor-settings", headers=headers, json=invalid
            ).status_code
            == 422
        )
    assert (
        client.put(
            "/api/rsi/executor-settings",
            headers={**headers, "Origin": "https://other.example"},
            json={"executor": "dsh"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize("executor", ["bkcrab", "dsh"])
def test_native_transport_uses_readonly_evidence_and_returns_diagnosis(
    configured, monkeypatch, executor
):
    save_settings(
        configured, ExecutorSettings(executor=executor, api_key="transport-key")
    )
    workspace, evidence, output = [
        configured / x for x in ["workspace", "evidence", "candidate"]
    ]
    for directory in [workspace, evidence, output]:
        directory.mkdir()
    context = {
        "workspace": str(workspace),
        "evidence_directory": str(evidence),
        "result_path": str(output / "diagnosis.json"),
        "result_schema": {},
        "objective": "Fix bug",
    }
    calls = []

    def container(image, mounts, command, timeout, **kwargs):
        calls.append(command)
        assert (evidence, "/evidence", True) in mounts
        assert (workspace, "/workspace", False) in mounts
        assert "transport-key" not in json.dumps([command, kwargs])
        prompt = (output / "output/native/instruction.txt").read_text()
        assert '"result_path": "/output/diagnosis.json"' in prompt
        assert "in Chinese" in prompt and "summary_en" in prompt
        assert "title and title_en" in prompt
        assert "【RSI-generated pr】" in prompt
        assert "BKCrab PRs #3 and #4" in prompt
        assert "Do not invent commands" in prompt
        write(
            output / "output/native/diagnosis.json",
            {"summary": "fixture", "evidence_refs": ["e0001"]},
        )

    monkeypatch.setattr(native_adapter, "run_container", container)
    args = SimpleNamespace(
        root=configured,
        executor=executor,
        model="deepseek-flash",
        timeout=60,
        image="test-image",
    )
    native_adapter.execute(args, context)
    assert read(output / "diagnosis.json")["summary"] == "fixture"
    assert ("bkcrab-eval" if executor == "bkcrab" else "dsh/lib/bin.js") in " ".join(
        calls[0]
    )


def test_container_failure_always_removes_container(monkeypatch, tmp_path):
    calls = []

    def call(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, 1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", call)
    with pytest.raises(subprocess.TimeoutExpired):
        native_adapter.run_container(
            "test-image", [(tmp_path, "/workspace", False)], ["false"], 1
        )
    assert calls[-1][:3] == ["docker", "rm", "-f"]
    assert calls[-1][-1] == calls[0][calls[0].index("--name") + 1]


@pytest.mark.skipif(
    not os.environ.get("RSI_NATIVE_IMAGE"),
    reason="opt-in native Docker smoke check; no model invocation",
)
@pytest.mark.parametrize("executor", ["bkcrab", "dsh"])
def test_native_image_starts_without_model_calls(tmp_path, executor):
    if executor == "bkcrab":
        command = ["/installed-agent/bkcrab-eval", "--help"]
    else:
        command = [
            "/installed-agent/dsh/node",
            "/installed-agent/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js",
            "--profile",
            "sdk",
            "--patch",
            "/installed-agent/dsh/transport.patch.yml",
            "--patch",
            "/installed-agent/dsh/standard.patch.yml",
            "--dump-config",
        ]
    native_adapter.run_container(
        os.environ["RSI_NATIVE_IMAGE"],
        [(tmp_path, "/workspace", False)],
        command,
        60,
        env={
            "HOME": "/tmp/rsi-home",
            "DSH_HOME": "/tmp/dsh-home",
            "EVAL_MODEL": "deepseek-flash",
            "EVAL_MAX_TOKENS": "32768",
            "DSH_TOOLS_MODE": "native",
            "DSH_PERMISSION_MODE": "danger-full-access",
            "DSH_PRIMARY_RUNTIME": "",
        },
    )
