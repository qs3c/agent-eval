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


def test_capture_frozen_excludes_environment_but_preserves_agent_edits(tmp_path):
    from agent_harness_eval.resources.capture_patch import freeze, capture_frozen
    import pytest

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "setup.py").write_text("old dependency\n")
    (repo / "code.py").write_text("old\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD").decode().strip()
    (repo / "setup.py").write_text("compatible dependency\n")
    (repo / "environment.txt").write_text("preinstalled\n")
    # Real SWE images can have an image-setup commit beyond dataset base.
    git(repo, "add", "setup.py")
    git(repo, "commit", "-qm", "image setup")
    baseline = tmp_path / "baseline.json"
    index = (repo / ".git/index").read_bytes()
    freeze(repo, baseline)
    assert (repo / ".git/index").read_bytes() == index
    assert git(repo, "rev-parse", "HEAD").decode().strip() != base
    (repo / "code.py").write_text("fixed\n")
    # Edits to files introduced by the environment must still be included.
    (repo / "environment.txt").write_text("agent changed\n")
    patch, identity = capture_frozen(repo, baseline, base)
    assert b"setup.py" not in patch
    assert b"fixed" in patch and b"agent changed" in patch
    clean = tmp_path / "regrade"
    git(repo, "worktree", "add", "--detach", str(clean), base)
    (clean / "setup.py").write_text("compatible dependency\n")
    (clean / "environment.txt").write_text("preinstalled\n")
    subprocess.run(["git", "-C", str(clean), "apply", "-"], input=patch, check=True)
    assert capture(clean, identity["tree"]) == patch
    with pytest.raises(FileExistsError):
        freeze(repo, baseline, base)
    with pytest.raises(ValueError, match="baseline"):
        capture_frozen(repo, baseline, "wrong")
    with pytest.raises(FileNotFoundError):
        capture_frozen(repo, tmp_path / "missing", base)


def test_official_patch_delivery_is_atomic_on_conflict(tmp_path):
    from agent_harness_eval.resources.official_runner import atomic_harness
    import pytest

    source = '''GIT_APPLY_CMDS = ["git apply --verbose", "git apply --verbose --reject", "patch --batch --fuzz=5 -p1 -i"]
def apply(repo, patch):
    import subprocess
    for command in GIT_APPLY_CMDS:
        result = subprocess.run(command.split() + [patch], cwd=repo, capture_output=True)
        if result.returncode == 0:
            return True
    return False
'''
    namespace = {}
    exec(atomic_harness(source, "official.py"), namespace)
    assert namespace["GIT_APPLY_CMDS"] == ["git apply --verbose"]
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    for name in ("setup", "business"):
        (tmp_path / name).write_text("old\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    for name in ("setup", "business"):
        (tmp_path / name).write_text("new\n")
    patch = tmp_path.parent / (tmp_path.name + ".patch")
    patch.write_bytes(git(tmp_path, "diff"))
    (tmp_path / "business").write_text("old\n")
    # setup is already applied, business is not: the historical failure pattern.
    assert not namespace["apply"](str(tmp_path), str(patch))
    assert (tmp_path / "business").read_text() == "old\n"
    assert (tmp_path / "setup").read_text() == "new\n"
    assert not list(tmp_path.glob("*.rej"))
    (tmp_path / "setup").write_text("old\n")
    assert namespace["apply"](str(tmp_path), str(patch))
    assert (tmp_path / "business").read_text() == "new\n"
    with pytest.raises(RuntimeError, match="Unsupported"):
        atomic_harness("GIT_APPLY_CMDS = ['something new']", "changed.py")
