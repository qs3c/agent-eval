"""Read-only GitHub observations. Publication remains a separate explicit action."""
import json
import re
import subprocess


def observe(url, *, run=subprocess.run):
    match = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/([1-9][0-9]*)", url)
    if not match:
        raise ValueError("Expected a GitHub pull request URL")
    repo, number = match.groups()
    result = run(["gh", "api", f"repos/{repo}/pulls/{number}"], capture_output=True, text=True, timeout=30, check=True)
    value = json.loads(result.stdout)
    if value.get("html_url") != url or value.get("base", {}).get("repo", {}).get("full_name", "").lower() != repo.lower():
        raise ValueError("GitHub response belongs to another PR")
    state = "merged" if value.get("merged") is True else value.get("state")
    if state not in {"open", "closed", "merged"}:
        raise ValueError("Unknown GitHub PR state")
    answer = {"url": url, "repository": "https://github.com/" + repo.lower() + ".git", "state": state,
        "base_ref": value["base"]["ref"], "base_sha": value["base"]["sha"], "head_sha": value["head"]["sha"],
        "merge_sha": value.get("merge_commit_sha") if state == "merged" else None}
    for key in ("base_sha", "head_sha", "merge_sha"):
        if answer[key] is not None and not re.fullmatch(r"[0-9a-f]{40}", answer[key]):
            raise ValueError("Invalid GitHub commit identity")
    if state == "merged" and not answer["merge_sha"]:
        raise ValueError("Merged PR has no confirmed merge SHA; retry read-only observation")
    return answer


def invalidate_drift(store, record, observation):
    """Preserve old validation evidence; attach a distinct freshness marker."""
    stale = observation["state"] == "open" and (observation["base_sha"] != record["parent_commit"] or observation["head_sha"] != record["commit"])
    record["upstream_observation"] = observation
    record["validation_stale"] = stale
    if stale:
        record["validation_stale_reason"] = "PR 基线或候选 HEAD 已漂移；旧验证保留，更新候选并重新验证后方可提升结论"
    store.save("candidates", record)
    return stale
