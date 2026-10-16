"""Resolve uv before the grading worker changes cwd; never invoke real grading."""

import pytest

from agent_harness_eval import regrading
from agent_harness_eval.benchmarks import swe


def fake_uv(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nprintf 'fixture uv\\n'\n")
    path.chmod(0o755)
    return path


def test_bundled_uv_runs_through_grading_subprocess(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    monkeypatch.setattr(swe, "__file__", str(root / "src/agent_harness_eval/benchmarks/swe.py"))
    uv = fake_uv(root / ".tools/uv")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(swe.shutil, "which", lambda name: None)
    work = tmp_path / "unit"
    work.mkdir()
    monkeypatch.chdir(work)
    predictions = work / "predictions.jsonl"
    predictions.write_text("{}\n")
    command = swe.regrade_command(predictions, "fixture", "fixture")
    assert command[0] == str(uv)
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr(regrading, "state_root", lambda: locks)
    log = work / "console.log"
    assert regrading.run_command(command, work, log) == 0
    assert log.read_text() == "fixture uv\n"


def test_path_uv_takes_precedence(tmp_path, monkeypatch):
    uv = fake_uv(tmp_path / "bin/uv")
    monkeypatch.setenv("PATH", str(uv.parent))
    assert swe._uv_executable() == str(uv)


@pytest.mark.parametrize("nonexecutable", [False, True])
def test_missing_uv_has_actionable_error(tmp_path, monkeypatch, nonexecutable):
    monkeypatch.setattr(swe, "__file__", str(tmp_path / "src/agent_harness_eval/benchmarks/swe.py"))
    monkeypatch.setattr(swe.shutil, "which", lambda name: None)
    if nonexecutable:
        uv = fake_uv(tmp_path / ".tools/uv")
        uv.chmod(0o644)
    with pytest.raises(FileNotFoundError, match="service PATH"):
        swe._uv_executable()
