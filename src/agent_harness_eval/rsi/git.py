from .models import Command
from .process import run


async def git(directory, *args):
    result = await run(
        Command(
            argv=["git", "-c", "core.hooksPath=/dev/null", "-C", str(directory), *args],
            timeout_seconds=120,
        ),
        directory,
        limit=4 * 1024 * 1024,
    )
    if result["returncode"] or result["truncated"]:
        raise ValueError(
            "Git failed or output exceeds 4 MiB: " + result["stderr"][:2000]
        )
    return result["stdout"].strip()


async def clone(source, target, commit):
    if target.exists():
        raise ValueError("Candidate workspace already exists; create a new candidate")
    await git(
        source,
        "clone",
        "--no-hardlinks",
        "--no-checkout",
        "--",
        str(source),
        str(target),
    )
    await git(target, "checkout", "--detach", commit)
    if await git(target, "rev-parse", "HEAD") != commit:
        raise ValueError("Clone does not match parent commit")


async def assert_clean(workspace, commit):
    if await git(workspace, "rev-parse", "HEAD") != commit or await git(
        workspace, "status", "--porcelain"
    ):
        raise ValueError("Candidate changed after commit; validation no longer applies")
