import asyncio
import copy
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_rsi import git

from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.provenance import tree_manifest
from agent_harness_eval.rsi import evaluation, native_build
from agent_harness_eval.rsi.api import public_candidate
from agent_harness_eval.rsi.evidence import create_case, verify_case
from agent_harness_eval.rsi.native_evaluation import configuration
from agent_harness_eval.rsi.store import Store
from agent_harness_eval.web import create_app


@pytest.fixture
def native_case(tmp_path):
    source = Path(__file__).resolve().parents[1]
    tasks = []
    for name in ("file-create", "file-preserve"):
        task = tmp_path / "tasks" / name
        shutil.copytree(source / "tasks" / name, task)
        tasks.append(task)
    exp = read(source / "experiments/controls.json")
    exp.update(attempts=2, timeout_seconds=432, concurrency=1)
    exp["agents"] = [
        {
            "id": "bkcrab",
            "implementation": "agent_harness_eval.agents.bkcrab:Bkcrab",
            "version": "old",
            "kwargs": {
                "binary": "/missing/old-binary",
                "provenance": "/missing/old-provenance",
            },
        }
    ]
    exp["benchmark"]["tasks"] = [str(p) for p in tasks]
    run = tmp_path / "artifacts/run"
    write(
        run / "manifest.json",
        {"experiment": exp, "tasks": {p.name: tree_manifest(p) for p in tasks}},
    )
    write(
        run / "jobs/bkcrab/t/result.json",
        {"task_name": "file-create", "exception_info": {"exception_type": "Timeout"}},
    )
    store = Store(tmp_path)
    case = create_case(store, run, "bkcrab")
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@localhost")
    (repo / "harness.py").write_text("VALUE = 0\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "parent")
    parent = git(repo, "rev-parse", "HEAD")
    (repo / "harness.py").write_text("VALUE = 1\n")
    git(repo, "commit", "-am", "candidate")
    record = store.create(
        "candidates",
        {
            "case": case["id"],
            "profile_id": "builtin-dsh",
            "phase": "validated",
            "parent_commit": parent,
            "commit": git(repo, "rev-parse", "HEAD"),
            "evaluations": [],
            "profile": {
                "repository": str(repo),
                "adapter": {
                    "argv": [
                        sys.executable,
                        "adapter.py",
                        "--image",
                        "sha256:" + "a" * 64,
                    ],
                    "version": "frozen-model",
                },
                "checks": [{"argv": ["true"]}],
            },
        },
    )
    shutil.copytree(repo, store.path("candidates", record["id"]) / "workspace")
    return store, case, record, tasks


def test_existing_candidate_inherits_selected_tasks_and_original_contract(native_case):
    store, case, record, tasks = native_case
    before = copy.deepcopy(record)
    profile, exp, hashes = configuration(store, record)
    assert exp.benchmark.tasks == tasks[:1]
    assert exp.attempts == 2 and exp.timeout_seconds == 432
    assert exp.guardian.mode == "observe"
    assert profile.artifacts == {
        "binary": "bkcrab-eval",
        "provenance": "provenance.json",
    }
    assert "sha256:" + "a" * 64 in profile.build.argv
    assert record == before == store.get("candidates", record["id"])
    assert (
        not profile.evaluation.exists()
    )  # GET must never migrate or rewrite a record.
    assert hashes == {tasks[0].name: tree_manifest(tasks[0])["sha256"]}
    public = public_candidate(record, store)
    assert public["can_evaluate"] and public["evaluation_from_case"]
    assert public["evaluation_tasks"] == ["file-create"]
    verify_case(store, case)


def test_new_build_uses_prepared_runtime_then_keeps_its_own_snapshot(native_case):
    store, _, record, _ = native_case
    original = copy.deepcopy(record["profile"])
    image = "sha256:" + "b" * 64
    write(store.project / ".cache/rsi-native.json", {"image": image})
    profile, _, _ = configuration(store, record)
    assert image in profile.build.argv
    record["paired_build"] = {
        "build": profile.build.model_dump(),
        "artifacts": profile.artifacts,
    }
    write(store.project / ".cache/rsi-native.json", {"image": "sha256:" + "c" * 64})
    assert image in configuration(store, record)[0].build.argv
    assert record["profile"] == original


def replace_case(native_case, edit):
    store, case, record, _ = native_case
    manifest = read(Path(case["run"]) / "manifest.json")
    edit(manifest)
    write(Path(case["run"]) / "manifest.json", manifest)
    case = create_case(store, case["run"], "bkcrab")
    record["case"] = case["id"]
    store.save("candidates", record)


def test_original_model_and_guardian_policy_are_preserved_without_assistance(
    native_case,
):
    def edit(manifest):
        manifest["experiment"]["model"] = {
            "mode": "live",
            "name": "original-model",
            "spending": "unbounded",
        }
        manifest["experiment"]["guardian"] = {
            "mode": "assist",
            "command": ["old-helper"],
            "version": "old",
            "idle_seconds": 77,
        }

    replace_case(native_case, edit)
    store, _, record, _ = native_case
    _, exp, _ = configuration(store, record)
    assert exp.model.name == "original-model"
    assert exp.guardian.mode == "observe" and exp.guardian.idle_seconds == 77
    assert not exp.guardian.command and exp.guardian.model is None


def test_namespaced_harbor_task_uses_known_directory_and_retained_inputs(native_case):
    store, case, record, tasks = native_case
    run = Path(case["run"])
    write(
        run / "jobs/bkcrab/t/result.json",
        {
            "task_name": "dataset/namespace__file-create",
            "task_id": {"path": "/untrusted/location/file-create"},
            "exception_info": {"exception_type": "Timeout"},
        },
    )
    retained = run / "tasks/file-create"
    shutil.copytree(tasks[0], retained)
    case = create_case(store, run, "bkcrab")
    record["case"] = case["id"]
    _, exp, hashes = configuration(store, record)
    assert exp.benchmark.tasks == [retained]
    assert hashes["file-create"] == tree_manifest(retained)["sha256"]


def test_api_exposes_recovered_template_and_enforces_live_confirmation(native_case):
    replace_case(
        native_case,
        lambda m: m["experiment"].update(
            model={"mode": "live", "name": "original", "spending": "unbounded"}
        ),
    )
    store, _, record, _ = native_case
    path = f"/api/rsi/candidates/{record['id']}"
    for enabled in (False, True):
        client = TestClient(create_app(store.project, allow_live=enabled))
        headers = {"Host": "localhost"}
        response = client.get(path, headers=headers)
        assert response.status_code == 200 and response.json()["can_evaluate"]
        assert response.json()["evaluation_requires_live"]
        response = client.post(
            path + "/actions", headers=headers, json={"kind": "evaluate"}
        )
        assert response.status_code == 422 and not store.actions()
        response = client.post(
            path + "/actions",
            headers=headers,
            json={"kind": "evaluate", "confirm_unbounded": True},
        )
        assert response.status_code == (201 if enabled else 422)


def test_missing_or_changed_evidence_explains_disabled_button(native_case):
    store, case, record, _ = native_case
    path = store.path("cases", case["id"]) / "evidence/experiment.json"
    path.write_text("{}")
    public = public_candidate(record, store)
    assert not public["can_evaluate"]
    assert "冻结的原测试配置已变化" in public["evaluation_blocked_reason"]


def test_live_gate_precedes_template_writes_and_builds(native_case):
    replace_case(
        native_case,
        lambda m: m["experiment"].update(
            model={"mode": "live", "name": "original", "spending": "unbounded"}
        ),
    )
    store, _, record, _ = native_case
    with pytest.raises(ValueError, match="spending acknowledgement"):
        asyncio.run(evaluation.evaluate(store, record, False))
    assert not record["evaluations"]
    assert not (
        store.path("candidates", record["id"]) / "evaluation-template.json"
    ).exists()


def test_frozen_model_key_is_loaded_and_environment_restored_on_failure(
    native_case, monkeypatch
):
    from agent_harness_eval.model_pool import ModelPool

    key_env = "EVAL_POOL_KEY_" + "c" * 32
    replace_case(
        native_case,
        lambda m: m["experiment"].update(
            model={
                "mode": "live",
                "name": "original",
                "spending": "unbounded",
                "api_key_env": key_env,
            }
        ),
    )
    store, _, record, _ = native_case
    monkeypatch.delenv(key_env, raising=False)

    def environment(self, config):
        assert config["name"] == "original" and config["api_key_env"] == key_env
        return {key_env: "test-key"}

    async def build(*args, **kwargs):
        assert os.environ[key_env] == "test-key"
        raise ValueError("fixture build failure")

    monkeypatch.setattr(ModelPool, "environment", environment)
    monkeypatch.setattr(evaluation, "checked", build)
    with pytest.raises(ValueError, match="fixture build failure"):
        asyncio.run(evaluation.evaluate(store, record, True))
    assert key_env not in os.environ
    assert record["evaluations"][-1]["assessment"] == "failed"


def test_changed_task_is_rejected_before_any_build(native_case, monkeypatch):
    store, _, record, tasks = native_case
    (tasks[0] / "instruction.md").write_text("changed task")

    async def no_build(*args, **kwargs):
        pytest.fail("must reject changed inputs before building")

    monkeypatch.setattr(evaluation, "checked", no_build)
    with pytest.raises(ValueError, match="冻结哈希不一致"):
        asyncio.run(evaluation.evaluate(store, record, False))
    assert record["evaluations"][-1]["assessment"] == "failed"


def test_native_pair_builds_exact_commits_and_overrides_stale_artifacts(
    native_case, monkeypatch
):
    store, case, record, _ = native_case
    builds, runs = [], []

    async def build(command, workspace, log, values):
        builds.append(git(workspace, "rev-parse", "HEAD"))
        shutil.copyfile(workspace / "harness.py", values["output"] / "bkcrab-eval")
        write(values["output"] / "provenance.json", {"commit": builds[-1]})

    async def run(exp, output, **kwargs):
        runs.append(exp)
        assert exp.agents[0].version == builds[-1]
        assert all(
            Path(v).is_file()
            for k, v in exp.agents[0].kwargs.items()
            if k in {"binary", "provenance"}
        )
        write(
            output / "manifest.json",
            {
                "experiment": exp.model_dump(mode="json"),
                "tasks": {p.name: tree_manifest(p) for p in exp.benchmark.tasks},
            },
        )
        write(
            output / "report.json",
            {
                "trials": [
                    {"task": "file-create", "metrics": {"reward": len(runs) - 1}}
                    for _ in range(exp.attempts)
                ]
            },
        )

    monkeypatch.setattr(evaluation, "checked", build)
    monkeypatch.setattr(evaluation, "run_experiment", run)
    asyncio.run(evaluation.evaluate(store, record, False))
    assert builds == [record["parent_commit"], record["commit"]]
    assert record["evaluations"][-1]["assessment"] == "improved"
    assert runs[0].model == runs[1].model and runs[0].benchmark == runs[1].benchmark
    assert not record["profile"].get("evaluation")
    verify_case(store, case)


def test_native_build_records_source_and_binary_provenance(native_case, monkeypatch):
    store, _, record, _ = native_case
    workspace = store.path("candidates", record["id"]) / "workspace"
    output = store.project / "build"
    output.mkdir()

    def container(image, mounts, command, timeout, **kwargs):
        assert (workspace, "/workspace", False) in mounts
        assert (output, "/output", False) in mounts
        assert "./cmd/bkcrab-eval" in command[-1]
        (output / "bkcrab-eval").write_bytes(b"new binary")

    monkeypatch.setattr(native_build, "run_container", container)
    native_build.build("sha256:" + "a" * 64, workspace, output)
    provenance = read(output / "provenance.json")
    assert provenance["commit"] == record["commit"] and not provenance["dirty"]
    assert provenance["binary_sha256"]
