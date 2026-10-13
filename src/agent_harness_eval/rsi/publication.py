import json
import re

from ..guardian.evidence import now, read, write
from .git import assert_clean, git
from .models import Command, Profile
from .process import checked


def readiness(store, record):
    """Only promote a complete improvement measured at this exact parent/child."""
    evaluations = record.get("evaluations", [])
    if not evaluations:
        return False, "尚未完成父子复评，保留草稿。"
    latest = evaluations[-1]
    if latest.get("assessment") != "improved":
        return False, "最新父子复评未明确改善，保留草稿。"
    if (
        not latest.get("finished_at")
        or latest.get("issues")
        or any(latest.get("errors", {}).values())
    ):
        return False, "复评未完成或存在异常，保留草稿。"
    for side, key in (("parent", "parent_commit"), ("candidate", "candidate_commit")):
        expected = record["parent_commit" if side == "parent" else "commit"]
        measured = latest.get(key)
        if measured is None:
            # Legacy records retained source commits in frozen build metadata.
            identity = latest.get("id", "")
            if not re.fullmatch(r"[0-9a-f]{32}", identity):
                return False, "无法确认复评源码版本，保留草稿。"
            root = store.path("candidates", record["id"]) / "evaluations" / identity
            try:
                artifacts = read(root / (side + "-artifacts.json"), {})
                if not artifacts or any(
                    artifact.get("commit") != expected or not artifact.get("sha256")
                    for artifact in artifacts.values()
                ):
                    return False, "无法确认复评源码版本，保留草稿。"
            except (OSError, ValueError, AttributeError):
                return False, "无法确认复评源码版本，保留草稿。"
        elif measured != expected:
            return False, "复评源码版本与当前候选不一致，保留草稿。"
    return True, "最新父子复评明确改善，且被测父子提交与当前候选一致。"


def validate_url(repo, url):
    if not re.fullmatch(rf"https://github\.com/{re.escape(repo)}/pull/[0-9]+", url):
        raise ValueError("Unexpected PR URL; inspect publication log before retry")


async def publish(store, record, *, best_effort=False):
    """Publish or reconcile, retaining partial progress for a safe retry."""
    try:
        await _publish(store, record)
    except Exception as error:
        from ..console import redact

        record["pr_sync_error"] = redact(str(error))
        store.save("candidates", record)
        if best_effort:
            return
        raise
    else:
        record["pr_sync_error"] = None
        record["pr_synced_at"] = now()
        store.save("candidates", record)


async def _publish(store, record):
    from ..console import redact
    from .runner import pr_title, write_pr_body

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
    ready, record["pr_ready_reason"] = readiness(store, record)
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
                "url,headRefOid,state,isDraft",
            ],
            pass_env=["GH_TOKEN", "GITHUB_TOKEN"],
        ),
        workspace,
    )
    existing = json.loads(result["stdout"])
    if existing:
        if len(existing) != 1:
            raise ValueError("Multiple PRs found for this candidate branch")
        pr = existing[0]
        url = pr["url"]
        validate_url(repo, url)
        if record.get("pr_url") and record["pr_url"] != url:
            raise ValueError("Existing PR does not match recorded publication")
        state = pr["state"]
        if state not in {"OPEN", "CLOSED", "MERGED"} or type(pr["isDraft"]) is not bool:
            raise ValueError("Unexpected GitHub PR state")
        record["pr_url"] = url
        record["pr_state"] = (
            state.lower() if state != "OPEN" else "draft" if pr["isDraft"] else "ready"
        )
        store.save("candidates", record)
        if state != "OPEN":
            return
        if pr["headRefOid"] != record["commit"]:
            raise ValueError(
                "Existing PR branch does not match this validated candidate"
            )
        write_pr_body(store, record)
        body = root / "PR.md"
        body.write_text(redact(body.read_text(encoding="utf-8")), encoding="utf-8")
        await checked(
            Command(
                argv=[
                    *common,
                    "edit",
                    url,
                    "--repo",
                    repo,
                    "--title",
                    pr_title(record),
                    "--body-file",
                    str(body),
                ],
                pass_env=["GH_TOKEN", "GITHUB_TOKEN"],
            ),
            workspace,
            root / "pr-edit.json",
        )
        if ready and pr["isDraft"]:
            await checked(
                Command(
                    argv=[*common, "ready", url, "--repo", repo],
                    pass_env=["GH_TOKEN", "GITHUB_TOKEN"],
                ),
                workspace,
                root / "pr-ready.json",
            )
            record["pr_state"] = "ready"
    else:
        if record.get("pr_url"):
            raise ValueError(
                "Recorded PR was not found; refusing to create a duplicate"
            )
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
                    *([] if ready else ["--draft"]),
                    "--base",
                    profile.pr_base,
                    "--head",
                    branch,
                    "--title",
                    pr_title(record),
                    "--body-file",
                    str(body),
                ],
                pass_env=["GH_TOKEN", "GITHUB_TOKEN"],
            ),
            workspace,
            root / "publish.json",
        )
        url = result["stdout"].strip()
        record["pr_state"] = "ready" if ready else "draft"
    validate_url(repo, url)
    record["pr_url"] = url
    store.save("candidates", record)
