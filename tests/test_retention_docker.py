"""Opt-in offline retention acceptance; creates only labeled isolated fixtures."""

import asyncio
import os
import subprocess
import sys
import tarfile
from uuid import uuid4

import pytest

from agent_harness_eval.guardian.docker import DockerTarget
from agent_harness_eval.guardian.evidence import write
from agent_harness_eval.guardian.retention import finalize

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("GUARDIAN_DOCKER_TESTS") != "1",
    reason="opt in with GUARDIAN_DOCKER_TESTS=1 on Linux with Docker",
)


def test_offline_workspace_snapshot_removes_original_secret_container(tmp_path):
    name = "retention-test-" + uuid4().hex[:12]
    backend = DockerTarget(name, timeout=10)
    cid = subprocess.check_output(
        [
            "docker",
            "run",
            "-d",
            "--pull",
            "never",
            "--network",
            "none",
            "--label",
            "com.docker.compose.project=" + backend.project,
            "--label",
            "com.docker.compose.service=main",
            "--env",
            "API_KEY=fixture-private-credential",
            "--entrypoint",
            "python3",
            "python:3.12.13-slim-bookworm",
            "-c",
            "import pathlib,time; pathlib.Path('/testbed').mkdir(); pathlib.Path('/installed-agent').mkdir(); pathlib.Path('/testbed/debug.log').write_text('fixture diagnostics'); pathlib.Path('/testbed/.env').write_text('API_KEY=fixture-private-credential'); pathlib.Path('/installed-agent/live.key').write_text('fixture-private-credential'); time.sleep(300)",
        ],
        text=True,
    ).strip()
    try:
        subprocess.run(
            [
                "docker",
                "exec",
                cid,
                "python3",
                "-c",
                "import time,pathlib; time.sleep(.3); assert pathlib.Path('/testbed/debug.log').exists()",
            ],
            check=True,
        )
        root = tmp_path / "output/jobs/fixture" / name
        write(
            root / "result.json",
            {
                "finished_at": "now",
                "exception_info": {"exception_type": "FixtureFailure"},
            },
        )
        result = asyncio.run(
            finalize(
                root,
                backend,
                {
                    "required_files": [],
                    "max_count": 3,
                    "max_gib": 0.001,
                    "ttl_hours": 24,
                },
            )
        )
        assert result["status"] == "snapshot_retained", result
        assert result["stopped"] and result["cleanup"]["resources_released"]
        assert (
            subprocess.run(
                ["docker", "inspect", cid], capture_output=True, check=False
            ).returncode
            != 0
        )
        with tarfile.open(root / result["archive"]) as archive:
            names = archive.getnames()
            assert any(name.endswith("debug.log") for name in names)
            assert not any(name.endswith((".env", "live.key")) for name in names)
        assert (
            b"fixture-private-credential" not in (root / result["archive"]).read_bytes()
        )
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)
