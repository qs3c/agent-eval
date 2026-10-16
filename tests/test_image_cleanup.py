"""Regression for Harbor Compose images retained by the old hb__ filter."""

import json
from types import SimpleNamespace

import pytest

from agent_harness_eval import batching
from agent_harness_eval.guardian.evidence import read, write


@pytest.mark.parametrize("mode", ["compose", "external", "wrong_project", "wrong_task", "container", "failure", "legacy"])
def test_trial_image_cleanup_uses_captured_identity(tmp_path, monkeypatch, mode):
    task = tmp_path / "tasks/django__django-123"
    task.mkdir(parents=True)
    trial_name = task.name + "__AbC123"
    project = trial_name.lower() + "__env"
    trial = tmp_path / "jobs/agent" / trial_name
    write(trial / "result.json", {
        "task_name": task.name,
        "task_id": {"path": str(task if mode != "wrong_task" else tmp_path / "other")},
        "trial_name": trial_name,
    })
    image_id = "sha256:fixture"
    write(trial / "guardian-agent-end.json", {"container": {
        "image": image_id,
        "project": project if mode != "wrong_project" else "other__env",
    }})
    tag = "hb__legacy:latest" if mode == "legacy" else project + "-main:latest"
    tags = [tag] + (["production:keep"] if mode == "external" else [])
    removed = []

    def run(cmd, **kwargs):
        if cmd[1:3] == ["image", "inspect"]:
            if not tags:
                return SimpleNamespace(returncode=1, stdout="", stderr="No such image")
            return SimpleNamespace(returncode=0, stdout=json.dumps([{"Id": image_id, "RepoTags": tags}]), stderr="")
        if cmd[1:3] == ["image", "rm"]:
            removed.append(cmd[-1])
            assert "--force" not in cmd
            if mode == "failure":
                return SimpleNamespace(returncode=1, stdout="", stderr="conflict")
            tags.remove(cmd[-1])
        return SimpleNamespace(returncode=0, stdout="consumer\n" if cmd[1] == "ps" and mode == "container" else "", stderr="")

    monkeypatch.setattr(batching.subprocess, "run", run)
    if mode == "failure":
        with pytest.raises(RuntimeError, match="image cleanup failed"):
            batching._cleanup_batch_images(None, tmp_path, [task], 1)
    else:
        batching._cleanup_batch_images(None, tmp_path, [task], 1)
    assert removed == ([tag] if mode in {"compose", "external", "failure", "legacy"} else [])
    if mode == "external":
        assert tags == ["production:keep"]
        assert read(tmp_path / "batches/001/cleanup.json")["images"][0]["status"] == "retained_external_tag"
