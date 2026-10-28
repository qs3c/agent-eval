"""Run on the verifier side BEFORE the hidden test patch is applied."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def snapshot(repo: Path, base: str) -> str:
    def git(*args, env=None, data=None):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], env=env, input=data
        )

    # Validate as a tree, never accept revision option injection.
    base = (
        git("rev-parse", "--verify", "--end-of-options", base + "^{tree}")
        .decode()
        .strip()
    )
    with tempfile.TemporaryDirectory() as temp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temp) / "index")}
        git("read-tree", base, env=env)
        # Include current index's tracked files even if now ignored, plus new nonignored files.
        paths = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        git("add", "-A", env=env)
        existing = b"\0".join(
            p for p in paths.split(b"\0") if p and ((repo / os.fsdecode(p)).exists() or (repo / os.fsdecode(p)).is_symlink())
        )
        if existing:
            git(
                "--literal-pathspecs",
                "add",
                "-f",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                env=env,
                data=existing + b"\0",
            )
        return git("write-tree", env=env).decode().strip()


def capture(repo: Path, base: str) -> bytes:
    base = subprocess.check_output([
        "git", "-C", str(repo), "rev-parse", "--verify", "--end-of-options",
        base + "^{tree}",
    ]).decode().strip()
    tree = snapshot(repo, base)
    return subprocess.check_output([
        "git", "-C", str(repo), "diff", "--binary", "--no-ext-diff",
        "--no-textconv", base, tree,
    ])


def freeze(repo: Path, baseline: Path, base: str = "HEAD"):
    """Save the installed environment before model execution, without changing HEAD/index."""
    commit = subprocess.check_output([
        "git", "-C", str(repo), "rev-parse", "--verify", "--end-of-options",
        base + "^{commit}",
    ]).decode().strip()
    tree = snapshot(repo, commit)
    baseline.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite an earlier baseline after the agent has started.
    with baseline.open("x") as stream:
        json.dump({"version": 1, "initial_head": commit, "tree": tree}, stream)


def capture_frozen(repo: Path, baseline: Path, base: str):
    identity = json.loads(baseline.read_text())
    if identity.get("version") != 1 or not identity.get("initial_head"):
        raise ValueError("Invalid SWE baseline")
    # Official images may contain a setup commit on top of the dataset base.
    result = subprocess.run([
        "git", "-C", str(repo), "merge-base", "--is-ancestor", base,
        identity["initial_head"],
    ], capture_output=True)
    if result.returncode:
        raise ValueError("SWE baseline is not descended from the frozen task base commit")
    patch = capture(repo, identity["tree"])
    return patch, {**identity, "base_commit": base, "patch_sha256": hashlib.sha256(patch).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--repo", type=Path, default=Path("/testbed"))
    parser.add_argument("--baseline", type=Path, default=Path("/logs/agent/swe-baseline.json"))
    parser.add_argument("--base", default="HEAD")
    args = parser.parse_args()
    if args.freeze:
        freeze(args.repo, args.baseline, args.base)
        raise SystemExit(0)
    datum = json.loads(Path("/tests/config.json").read_text())
    out = Path("/logs/verifier")
    out.mkdir(parents=True, exist_ok=True)
    patch, identity = capture_frozen(args.repo, args.baseline, datum["base_commit"])
    (out / "model.patch").write_bytes(patch)
    (out / "patch-provenance.json").write_text(json.dumps(identity))
    (out / "prediction.json").write_text(
        json.dumps(
            {"instance_id": datum["instance_id"], "base_commit": datum["base_commit"]}
        )
    )
