"""Opt-in isolated Docker acceptance; never prune host build caches."""

import os
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_harness_eval import batching
from agent_harness_eval.guardian.evidence import read, write


@pytest.mark.skipif(os.environ.get("CLEANUP_DOCKER_TESTS") != "1", reason="opt-in Docker acceptance")
def test_compose_cleanup_with_real_container_and_external_tag(tmp_path, monkeypatch):
    name = "cleanup-fixture-" + uuid4().hex[:12]
    project = name + "__env"
    tag, external = project + "-main:latest", name + ":external"
    task = tmp_path / "tasks" / name
    task.mkdir(parents=True)
    context = tmp_path / "build"
    context.mkdir()
    (context / "payload").write_text(name)
    (context / "Dockerfile").write_text("FROM scratch\nCOPY payload /payload\nCMD [\"/payload\"]\n")
    real_run = subprocess.run

    def command(*args):
        return subprocess.check_output(["docker", *args], text=True).strip()

    def run(cmd, **kwargs):
        if cmd[:3] == ["docker", "builder", "prune"]:
            return SimpleNamespace(returncode=0, stdout="fixture: host cache untouched", stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(batching.subprocess, "run", run)
    cid = None
    try:
        command("build", "--network=none", "-t", tag, str(context))
        image_id = command("image", "inspect", "--format", "{{.Id}}", tag)
        command("tag", tag, external)
        trial = tmp_path / "jobs/fixture" / name
        write(trial / "result.json", {"task_id": {"path": str(task)}, "trial_name": name})
        write(trial / "guardian-agent-end.json", {"container": {"image": image_id, "project": project}})
        cid = command("create", "--name", name, tag)
        assert batching._cleanup_batch_images(None, tmp_path, [task], 1) is False
        assert read(tmp_path / "batches/001/cleanup.json")["images"][0]["status"] == "retained_container_reference"
        command("rm", cid)
        cid = None
        assert batching._cleanup_batch_images(None, tmp_path, [task], 1) is True
        assert read(tmp_path / "batches/001/cleanup.json")["images"][0]["status"] == "retained_external_tag"
        assert command("image", "inspect", "--format", "{{.Id}}", external) == image_id
        assert real_run(["docker", "image", "inspect", tag], capture_output=True).returncode != 0
    finally:
        if cid:
            real_run(["docker", "rm", cid], capture_output=True)
        real_run(["docker", "image", "rm", tag, external], capture_output=True)
