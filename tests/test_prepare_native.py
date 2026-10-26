"""Execute the shell entrypoint with recording tools, without installing anything."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "posix", reason="Linux shell preparation entrypoint")
@pytest.mark.parametrize("selection", ["dsh", "bkcrab", "all"])
def test_selected_agent_has_independent_build_dependencies(tmp_path, selection):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "scripts").mkdir()
    shutil.copy(root / "scripts/prepare-native.sh", tmp_path / "scripts")
    shutil.copytree(root / "locks", tmp_path / "locks")
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "calls"
    executable = '#!/bin/sh\nprintf "%s %s\\n" "$(basename "$0")" "$*" >> "$CALL_LOG"\n'
    for name in ("uv", "npm", "go"):
        path = tools / name
        path.write_text(executable)
        path.chmod(0o755)
    (tmp_path / ".venv/bin").mkdir(parents=True)
    cli = tmp_path / ".venv/bin/agent-harness-eval"
    cli.write_text(executable)
    cli.chmod(0o755)
    env = {**os.environ, "PATH": str(tools) + ":" + os.environ["PATH"], "CALL_LOG": str(log)}
    env.pop("BKCRAB_SOURCE", None)
    if selection != "dsh":
        source = tmp_path / "source"
        source.mkdir()
        env["BKCRAB_SOURCE"] = str(source)
    subprocess.run(["bash", "scripts/prepare-native.sh", selection], cwd=tmp_path, env=env, check=True)
    calls = log.read_text()
    assert ("npm ci" in calls) == (selection != "bkcrab")
    assert ("bundle-dsh" in calls) == (selection != "bkcrab")
    assert ("go build" in calls) == (selection != "dsh")
    assert ("snapshot-bkcrab" in calls) == (selection != "dsh")
