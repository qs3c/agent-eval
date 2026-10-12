import json

from ..guardian.evidence import write
from .git import assert_clean, git
from .models import Command, Profile
from .process import checked


async def publish(store, record):
    from ..console import redact
    from .runner import write_pr_body

    profile = Profile.model_validate(record["profile"])
    if not profile.github_repository:
        raise ValueError("Profile has no GitHub publication destination")
    root = store.path("candidates", record["id"])
    workspace = root / "workspace"
    await assert_clean(workspace, record["commit"])
    branch = "codex/rsi-" + record["id"]
    repo = profile.github_repository
    # The destination must also be the source repository's configured GitHub remote.
    remote = await git(profile.repository, "remote", "get-url", "origin")
    allowed = {
        f"https://github.com/{repo}.git",
        f"https://github.com/{repo}",
        f"git@github.com:{repo}.git",
    }
    if remote not in allowed:
        raise ValueError("Configured PR destination does not match source origin")
    common = ["gh", "pr"]
    result = await checked(
        Command(
            argv=[
                *common,
                "list",
                "--repo",
                repo,
                "--head",
                branch,
                "--state",
                "all",
                "--json",
                "url,headRefOid",
            ],
            pass_env=["GH_TOKEN", "GITHUB_TOKEN"],
        ),
        workspace,
    )
    existing = json.loads(result["stdout"])
    if existing:
        if len(existing) != 1 or existing[0]["headRefOid"] != record["commit"]:
            raise ValueError(
                "Existing PR branch does not match this validated candidate"
            )
        url = existing[0]["url"]
    else:
        write_pr_body(store, record)
        body = root / "PR.md"
        body.write_text(redact(body.read_text(encoding="utf-8")), encoding="utf-8")
        write(
            root / "publication-intent.json",
            {"repository": repo, "branch": branch, "commit": record["commit"]},
        )
        await checked(
            Command(
                argv=[
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "push",
                    remote,
                    f"{record['commit']}:refs/heads/{branch}",
                ],
                pass_env=["GH_TOKEN", "GITHUB_TOKEN"],
            ),
            workspace,
            root / "push.json",
        )
        result = await checked(
            Command(
                argv=[
                    *common,
                    "create",
                    "--repo",
                    repo,
                    "--draft",
                    "--base",
                    profile.pr_base,
                    "--head",
                    branch,
                    "--title",
                    "RSI: " + record["diagnosis"]["summary"][:120].replace("\n", " "),
                    "--body-file",
                    str(body),
                ],
                pass_env=["GH_TOKEN", "GITHUB_TOKEN"],
            ),
            workspace,
            root / "publish.json",
        )
        url = result["stdout"].strip()
    if (
        not url.startswith(f"https://github.com/{repo}/pull/")
        or not url.rsplit("/", 1)[-1].isdigit()
    ):
        raise ValueError("Unexpected PR URL; inspect publication log before retry")
    record["pr_url"] = url
    store.save("candidates", record)
