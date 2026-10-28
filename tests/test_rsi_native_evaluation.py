"""Fictional target fixture for shared regression evidence tests."""
import asyncio
import shutil
import sys
from pathlib import Path
import pytest
from test_rsi import git
from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.provenance import tree_manifest
from agent_harness_eval.rsi.evidence import create_case
from agent_harness_eval.rsi.store import Store

@pytest.fixture
def native_case(tmp_path, monkeypatch):
    from agent_harness_eval.rsi import regression
    from agent_harness_eval.rsi.models import Profile
    monkeypatch.setattr(regression, "builtin_profile", lambda *args: Profile.model_validate(record["profile"]))
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
            "id": "sample_agent",
            "implementation": "fixture.agent:Agent",
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
        run / "jobs/sample_agent/t/result.json",
        {"task_name": "file-create", "exception_info": {"exception_type": "Timeout"}},
    )
    store = Store(tmp_path)
    case = create_case(store, run, "sample_agent")
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
