import json
import subprocess

from agent_harness_eval.benchmarks.swe import export, instrument, regrade_command
from agent_harness_eval.resources.capture_patch import capture


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def test_capture_all_changes_preserves_index(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    for name in ("edit", "delete", "staged", "committed"):
        (tmp_path / name).write_text("old\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD").decode().strip()
    (tmp_path / "committed").write_text("new commit\n")
    git(tmp_path, "add", "committed")
    git(tmp_path, "commit", "-qm", "agent commit")
    (tmp_path / "staged").write_text("new staged\n")
    git(tmp_path, "add", "staged")
    (tmp_path / "edit").write_text("new edit\n")
    (tmp_path / "delete").unlink()
    (tmp_path / "untracked").write_text("new file\n")
    before = (tmp_path / ".git/index").read_bytes()
    patch = capture(tmp_path, base)
    assert (tmp_path / ".git/index").read_bytes() == before
    for text in (
        b"new commit",
        b"new staged",
        b"new edit",
        b"new file",
        b"deleted file mode",
    ):
        assert text in patch
    clean = tmp_path.parent / (tmp_path.name + "-clean")
    git(tmp_path, "worktree", "add", "--detach", str(clean), base)
    subprocess.run(["git", "-C", str(clean), "apply", "-"], input=patch, check=True)
    assert not (clean / "delete").exists()
    assert (clean / "untracked").read_text() == "new file\n"


def test_instrument_before_hidden_tests(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/config.json").write_text(
        json.dumps({"instance_id": "repo-1", "base_commit": "123"})
    )
    (tmp_path / "tests/test.sh").write_text(
        "#!/bin/bash\nset -euo pipefail\ngit reset --hard\n# Reward logging for Harbor.\n"
    )
    instrument(tmp_path)
    text = (tmp_path / "tests/test.sh").read_text()
    assert text.index("capture_patch.py") < text.index("git reset")
    assert "if [ ! -s /logs/verifier/report.json ]; then exit 2; fi" in text


def test_export_never_selects_best_and_counts_missing(tmp_path, monkeypatch):
    from agent_harness_eval.benchmarks import swe

    monkeypatch.setattr(swe, "_uv_executable", lambda: "/fixture/uv")
    root = tmp_path / "run"
    root.mkdir()
    (root / "tasks/t/tests").mkdir(parents=True)
    (root / "tasks/t/tests/config.json").write_text(
        json.dumps({"instance_id": "repo-1"})
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {"tasks": {"t": {}}, "experiment": {"agents": [{"id": "a"}], "attempts": 2}}
        )
    )
    trial = root / "jobs/a/t-one"
    (trial / "verifier").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "swe-bench/t",
                "task_id": {"path": "/frozen/tasks/t"},
                "started_at": "1",
            }
        )
    )
    (trial / "verifier/model.patch").write_text("a patch")
    export(root, tmp_path / "export")
    assert (
        json.loads((tmp_path / "export/a.attempt-1.jsonl").read_text())["model_patch"]
        == "a patch"
    )
    assert (
        json.loads((tmp_path / "export/a.attempt-2.jsonl").read_text())["model_patch"]
        == ""
    )
    manifest = json.loads((tmp_path / "export/export-manifest.json").read_text())
    assert manifest["trials"][1]["exception"] == "missing trial"
    cmd = regrade_command(tmp_path / "export/a.attempt-1.jsonl", "dataset", "run1")
    assert "swebench==4.1.0" in cmd and "--max_workers" in cmd
