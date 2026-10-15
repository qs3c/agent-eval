import asyncio
import json
import os
from pathlib import Path

import pytest

from agent_harness_eval.config import BatchConfig, load
from agent_harness_eval.freezing import freeze_tree
from agent_harness_eval.provenance import write_json


def test_shared_freezing_is_independent_of_source(tmp_path):
    source = tmp_path / "source"
    (source / "environment").mkdir(parents=True)
    archive = source / "environment/grader-runtime.tar"
    archive.write_bytes(b"unchanged grader")
    shared = tmp_path / "frozen/shared-runtime"
    a, b = tmp_path / "frozen/a", tmp_path / "frozen/b"
    freeze_tree(source, a, shared)
    freeze_tree(source, b, shared)
    fa, fb = a / "environment/grader-runtime.tar", b / "environment/grader-runtime.tar"
    assert fa.stat().st_ino == fb.stat().st_ino
    assert not os.path.samefile(fa, archive)
    archive.write_bytes(b"changed source")
    assert fa.read_bytes() == fb.read_bytes() == b"unchanged grader"


@pytest.mark.parametrize("regrade_fails", [False, True])
def test_batches_pair_agents_before_cleanup_and_resume(tmp_path, monkeypatch, regrade_fails):
    from agent_harness_eval import batching
    from agent_harness_eval.guardian import supervisor
    from agent_harness_eval.run import run_experiment

    exp = load(Path("experiments/controls.json"))
    exp.batch = BatchConfig(size=1, min_free_gib=0)
    from agent_harness_eval.guardian.config import GuardianConfig

    exp.guardian = GuardianConfig()
    events = []

    async def run(exp, root, cfg, tasks, agent_id, **kwargs):
        task = tasks[0].path
        events.append(("run", task.name, agent_id))
        trial = root / "jobs" / agent_id / (task.name + "-fixture")
        trial.mkdir(parents=True)
        write_json(
            trial / "result.json",
            {
                "task_name": task.name,
                "task_id": {"path": str(task)},
                "trial_name": trial.name,
                "finished_at": "2026-10-13T12:00:00",
                "exception_info": None,
                "verifier_result": {"rewards": {"reward": 0}},
            },
        )

    def prepare(exp, root, paths, number):
        events.append(("prepare", paths[0].name))

    def regrade(exp, root, paths, number):
        events.append(("regrade", paths[0].name))
        if regrade_fails:
            raise ConnectionError("official scoring connection reset")

    def cleanup(exp, root, paths, number):
        events.append(("cleanup", paths[0].name))

    monkeypatch.setattr(supervisor, "run_guarded", run)
    monkeypatch.setattr(batching, "prepare_batch", prepare)
    monkeypatch.setattr(batching, "regrade_batch", regrade)
    monkeypatch.setattr(batching, "cleanup_batch", cleanup)
    root = tmp_path / "run"
    asyncio.run(run_experiment(exp, root))
    expected_events = [
        ("prepare", "file-create"),
        ("run", "file-create", "positive"),
        ("run", "file-create", "negative"),
        ("regrade", "file-create"),
        ("cleanup", "file-create"),
        ("prepare", "file-preserve"),
        ("run", "file-preserve", "positive"),
        ("run", "file-preserve", "negative"),
        ("regrade", "file-preserve"),
        ("cleanup", "file-preserve"),
    ]
    if regrade_fails:
        expected_events = [event for event in expected_events if event[0] != "cleanup"]
    assert events == expected_events
    report = json.loads((root / "report.json").read_text())
    assert report["agents"]["positive"]["expected"] == 2
    assert report["agents"]["negative"]["recorded"] == 2
    state = json.loads((root / "batch-state.json").read_text())
    assert state["phase"] == "completed"
    for batch in state["batches"]:
        assert batch["phase"] == "completed"
        assert batch["official_regrade"]["status"] == (
            "failed" if regrade_fails else "disabled"
        )
        if regrade_fails:
            assert "connection reset" in batch["official_regrade"]["error"]
    asyncio.run(run_experiment(exp, root, resume=True))
    assert len(events) == len(expected_events)
    exp.concurrency = 1
    with pytest.raises(ValueError, match="configuration"):
        asyncio.run(run_experiment(exp, root, resume=True))


@pytest.mark.parametrize("failure_stage", ["cancel_regrade", "cleanup"])
def test_batch_execution_still_stops_on_cancel_or_cleanup_error(tmp_path, monkeypatch, failure_stage):
    from types import SimpleNamespace

    from agent_harness_eval import batching
    from agent_harness_eval.guardian import supervisor

    exp = load(Path("experiments/controls.json"))
    exp.batch = BatchConfig(size=1, min_free_gib=0, official_regrade=True)
    events = []

    async def run(*args, **kwargs):
        events.append("run")

    def regrade(*args):
        if failure_stage == "cancel_regrade":
            raise asyncio.CancelledError()
        # Cleanup is now reached only after successful official grading.

    def cleanup(*args):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(supervisor, "run_guarded", run)
    monkeypatch.setattr(batching, "prepare_batch", lambda *a: None)
    monkeypatch.setattr(batching, "record_tasks", lambda *a: None)
    monkeypatch.setattr(batching, "report", lambda *a: None)
    monkeypatch.setattr(batching, "regrade_batch", regrade)
    monkeypatch.setattr(batching, "cleanup_batch", cleanup)
    tasks = [SimpleNamespace(path=tmp_path / str(i)) for i in range(2)]
    with pytest.raises(asyncio.CancelledError if failure_stage == "cancel_regrade" else RuntimeError):
        asyncio.run(batching.run_batches(exp, tmp_path, tasks, {a.id: None for a in exp.agents}))
    state = json.loads((tmp_path / "batch-state.json").read_text())
    assert state["current_batch"] == 1
    assert state["phase"] == ("interrupted" if failure_stage == "cancel_regrade" else "failed")
    assert len(events) == len(exp.agents)


def test_resume_stops_on_modified_frozen_input(tmp_path, monkeypatch):
    from agent_harness_eval import batching
    from agent_harness_eval.guardian import supervisor
    from agent_harness_eval.run import run_experiment

    exp = load(Path("experiments/controls.json"))
    exp.batch = BatchConfig(size=1, min_free_gib=0)
    from agent_harness_eval.guardian.config import GuardianConfig

    exp.guardian = GuardianConfig()

    async def run(*args, **kwargs):
        raise RuntimeError("interruption fixture")

    monkeypatch.setattr(supervisor, "run_guarded", run)
    monkeypatch.setattr(batching, "prepare_batch", lambda *a: None)
    root = tmp_path / "run"
    with pytest.raises(RuntimeError):
        asyncio.run(run_experiment(exp, root))
    (root / "tasks/file-create/instruction.md").write_text("modified")
    with pytest.raises(ValueError, match="Frozen task"):
        asyncio.run(run_experiment(exp, root, resume=True))


def test_official_regrade_contains_only_current_batch(tmp_path, monkeypatch):
    from agent_harness_eval import batching

    exp = load(Path("experiments/controls.json"))
    exp.batch = BatchConfig(size=1, official_regrade=True, min_free_gib=0)
    task = tmp_path / "file-create"
    (task / "tests").mkdir(parents=True)
    write_json(task / "tests/config.json", {"instance_id": "file-create"})

    def export(root, staging):
        staging.mkdir()
        for agent in exp.agents:
            (staging / f"{agent.id}.attempt-1.jsonl").write_text(
                "".join(
                    json.dumps(
                        {
                            "instance_id": name,
                            "model_patch": "patch",
                            "model_name_or_path": agent.id,
                        }
                    )
                    + "\n"
                    for name in ["file-create", "file-preserve"]
                )
            )
        write_json(
            staging / "export-manifest.json",
            {
                "trials": [
                    {"instance_id": name} for name in ["file-create", "file-preserve"]
                ]
            },
        )

    def command(predictions, dataset, run_id):
        assert len(Path(predictions).read_text().splitlines()) == 1
        assert json.loads(Path(dataset).read_text()) == [{"instance_id": "file-create"}]
        return ["fixture-grader"]

    def run(command, **kwargs):
        write_json(
            kwargs["cwd"] / "fixture.json",
            {
                "submitted_instances": 1,
                "submitted_ids": ["file-create"],
                "resolved_instances": 0,
                "error_instances": 0,
                "incomplete_ids": [],
            },
        )
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0)

    from agent_harness_eval import regrading
    from agent_harness_eval.benchmarks import swe
    monkeypatch.setattr(swe, "export", export)
    monkeypatch.setattr(swe, "regrade_command", command)
    monkeypatch.setattr(regrading, "run_command", lambda cmd, work, log, inherited: run(cmd, cwd=work).returncode)
    batching.regrade_batch(exp, tmp_path, [task], 1)
    summary = json.loads((tmp_path / "official-summary.json").read_text())
    assert summary["agents"]["positive"]["observed"] == 1
    assert not summary["agents"]["positive"]["complete"]


def test_cleanup_keeps_images_referenced_by_other_containers(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agent_harness_eval import batching

    exp = load(Path("experiments/controls.json"))
    exp.batch = BatchConfig(min_free_gib=0)
    task = tmp_path / "task"
    task.mkdir()
    write_json(task / "runtime-provenance.json", {"image": "example/image@sha256:abc"})
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        stdout = (
            json.dumps([{"Id": "sha256:abc", "RepoTags": ["example/image:latest"]}])
            if cmd[1:3] == ["image", "inspect"]
            else "other-container\n"
            if cmd[1] == "ps"
            else ""
        )
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(batching.subprocess, "run", run)
    batching.cleanup_batch(exp, tmp_path, [task], 1)
    assert not any(c[1:3] == ["image", "rm"] for c in commands)
    record = json.loads((tmp_path / "batches/001/cleanup.json").read_text())
    assert record["images"][0]["status"] == "retained_container_reference"
