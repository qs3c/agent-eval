"""Run on the verifier side BEFORE the hidden test patch is applied."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def capture(repo: Path, base: str) -> bytes:
    def git(*args, env=None, data=None):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], env=env, input=data
        )

    # Validate as a commit, never accept revision option injection.
    base = (
        git("rev-parse", "--verify", "--end-of-options", base + "^{commit}")
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
            p for p in paths.split(b"\0") if p and (repo / os.fsdecode(p)).exists()
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
        return git(
            "diff",
            "--cached",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            base,
            env=env,
        )


if __name__ == "__main__":
    datum = json.loads(Path("/tests/config.json").read_text())
    out = Path("/logs/verifier")
    out.mkdir(parents=True, exist_ok=True)
    (out / "model.patch").write_bytes(capture(Path("/testbed"), datum["base_commit"]))
    (out / "prediction.json").write_text(
        json.dumps(
            {"instance_id": datum["instance_id"], "base_commit": datum["base_commit"]}
        )
    )
