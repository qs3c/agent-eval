import asyncio
import io
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from harbor.models.trial.config import TaskConfig

from agent_harness_eval.guardian import retention
from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.guardian.supervisor import resume_counts


def trial(tmp_path, name="trial"):
    root = tmp_path / "output/jobs/agent" / name
    root.mkdir(parents=True)
    write(
        root / "result.json",
        {
            "task_id": {"path": "/frozen/task"},
            "trial_name": name,
            "finished_at": "now",
            "verifier_result": {"rewards": {"reward": 0}},
            "exception_info": None,
        },
    )
    return root


def test_reward_zero_is_normal_and_missing_evidence_or_assistance_retains(tmp_path):
    root = trial(tmp_path)
    assert retention.reasons(root, {"required_files": []}) == []
    assert "missing_evidence:agent/events.jsonl" in retention.reasons(root, {})
    write(root / "incidents/a/incident.json", {})
    write(root / "incidents/a/assisted.json", {"actor": "guardian"})
    assert retention.reasons(root, {"required_files": []}) == ["externally_assisted"]


def source_tar(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, value in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    buffer.seek(0)
    return buffer


class Copy:
    def __init__(self, entries):
        self.stdout = source_tar(entries)
        self.returncode = 0

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_bounded_archive_excludes_secrets_and_evicts_by_count_and_ttl(
    tmp_path, monkeypatch
):
    entries = {
        "./debug.log": b"diagnostics",
        "./.env": b"API_KEY=private",
        "./installed-agent/live.key": b"private",
        "../escape": b"private",
        "./token.txt": b"private",
    }
    monkeypatch.setattr(retention.subprocess, "Popen", lambda *a, **k: Copy(entries))
    policy = {"max_count": 1, "max_gib": 0.001, "ttl_hours": 24}
    first = trial(tmp_path, "first")
    write(first / "retention.json", {})
    result = retention._archive(first, "cid", policy)
    assert result["status"] == "snapshot_retained"
    with tarfile.open(first / result["archive"]) as archive:
        assert archive.getnames() == ["./debug.log"]
    second = trial(tmp_path, "second")
    write(second / "retention.json", {})
    retention._archive(second, "cid", policy)
    assert not (first / "retained-workspace.tar").exists()
    assert read(first / "retention.json")["status"] == "snapshot_evicted"
    value = read(second / "retention.json")
    value["created_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    write(second / "retention.json", value)
    third = trial(tmp_path, "third")
    write(third / "retention.json", {})
    retention._archive(third, "cid", {**policy, "max_count": 3})
    assert not (second / "retained-workspace.tar").exists()


def test_oversized_snapshot_fails_without_leaving_partial_archive(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        retention.subprocess, "Popen", lambda *a, **k: Copy({"./huge": b"x" * 50000})
    )
    root = trial(tmp_path)
    with pytest.raises(RuntimeError, match="quota"):
        retention._archive(root, "cid", {"max_gib": 0.00004})
    assert not list(root.glob("*.tar*"))


def test_identity_guard_rejects_mismatched_project_before_stopping(monkeypatch):
    calls = []

    async def fake(argv, timeout):
        calls.append(argv)
        if argv[1] == "ps":
            return {"returncode": 0, "stdout": "a" * 64, "stderr": ""}
        return {
            "returncode": 0,
            "stdout": '"'
            + "a" * 64
            + '"\n{"com.docker.compose.project":"other"}\n{"Running":true}',
            "stderr": "",
        }

    monkeypatch.setattr(retention, "command", fake)
    backend = type("Target", (), {"project": "mine", "timeout": 1})()
    with pytest.raises(RuntimeError, match="identity mismatch"):
        asyncio.run(retention.stop_project(backend))
    assert all(call[1] not in {"stop", "kill", "rm"} for call in calls)


def test_failed_retention_records_failure_and_still_cleans(tmp_path, monkeypatch):
    root = trial(tmp_path)

    async def fail(*args):
        raise RuntimeError("Docker unavailable")

    monkeypatch.setattr(retention, "stop_project", fail)

    class Target:
        project = "fixture"

        async def stop(self):
            return {"resources_released": False}

    result = asyncio.run(retention.finalize(root, Target(), {}))
    assert result["status"] == "retention_failed" and result["cleanup_failed"]
    assert read(root / "retention.json") == result


def test_resume_skips_valid_failures_and_rejects_partial_attempt(tmp_path):
    root = trial(tmp_path)
    write(
        root / "result.json",
        {
            "task_id": {"path": "/old/task"},
            "trial_name": "trial",
            "finished_at": "now",
            "exception_info": {"exception_type": "timeout"},
        },
    )
    directory = root.parent
    task = TaskConfig(path=Path("/new/task"))
    assert resume_counts(directory, [task], 2)["task"] == 1
    write(
        directory / ".guardian-inputs/trial.json",
        {"trial_name": "trial", "task": {"path": "/old/task"}},
    )
    assert resume_counts(directory, [task], 2)["task"] == 1
    write(
        directory / ".guardian-inputs/partial.json",
        {"trial_name": "partial", "task": {"path": "/old/task"}},
    )
    with pytest.raises(ValueError, match="partial trial"):
        resume_counts(directory, [task], 2)
