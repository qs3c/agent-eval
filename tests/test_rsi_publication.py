import json

import pytest
from test_rsi import create, git, operate
from test_rsi import setup as rsi_setup

from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.rsi import publication

setup = rsi_setup


@pytest.fixture
def published_candidate(setup):
    store, _, source, _, profile = setup
    git(source, "remote", "add", "origin", "https://github.com/example/fixture.git")
    profile["github_repository"] = "example/fixture"
    write(store.project / "rsi-profiles/fixture.json", profile)
    candidate, _ = operate(store, create(setup))
    candidate["pr_url"] = "https://github.com/example/fixture/pull/7"
    store.save("candidates", candidate)
    return store, candidate


def measured(candidate, assessment="improved", **extra):
    return {
        "id": "e" * 32,
        "assessment": assessment,
        "finished_at": "2026-10-13T00:00:00Z",
        "parent_commit": candidate["parent_commit"],
        "candidate_commit": candidate["commit"],
        "scores": {"parent": 0, "candidate": 1, "delta": 1},
        "errors": {"parent": 0, "candidate": 0},
        "issues": [],
        **extra,
    }


def operation(argv):
    return "edit" if argv[:2] == ["gh", "api"] else argv[2]


def github(monkeypatch, candidate, *, state="OPEN", draft=True, fail=None, head=None):
    calls = []

    async def checked(command, *args, **kwargs):
        calls.append(command.argv)
        if command.argv[:3] == ["gh", "pr", "edit"]:
            raise ValueError(
                "GraphQL: Projects (classic) is being deprecated (repository.pullRequest.projectCards)"
            )
        action = operation(command.argv)
        if action == fail:
            raise ValueError("GitHub unavailable")
        if action == "list":
            output = json.dumps(
                [
                    {
                        "url": candidate["pr_url"],
                        "headRefOid": head or candidate["commit"],
                        "state": state,
                        "isDraft": draft,
                    }
                ]
            )
        else:
            output = ""
        return {"returncode": 0, "stdout": output}

    monkeypatch.setattr(publication, "checked", checked)
    return calls


@pytest.mark.parametrize(
    "assessment",
    ["improved", "equal", "regressed", "inconclusive", "failed", "running", None],
)
def test_sync_promotes_only_completed_improvement(
    published_candidate, monkeypatch, assessment
):
    store, candidate = published_candidate
    candidate["evaluations"] = [measured(candidate, assessment)] if assessment else []
    store.save("candidates", candidate)
    calls = github(monkeypatch, candidate)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed"
    operations = [operation(call) for call in calls]
    assert operations == (
        ["list", "edit", "ready"] if assessment == "improved" else ["list", "edit"]
    )
    assert candidate["pr_state"] == ("ready" if assessment == "improved" else "draft")
    assert candidate["pr_ready_reason"]
    assert candidate["pr_sync_error"] is None
    body = (store.path("candidates", candidate["id"]) / "PR.md").read_text()
    assert "## 中文" in body and "## English" in body
    if assessment:
        assert "父分数" in body and "Parent score" in body


@pytest.mark.parametrize(
    "extra",
    [
        {"candidate_commit": "different"},
        {"parent_commit": "different"},
        {"finished_at": None},
        {"issues": ["incomplete report"]},
        {"errors": {"parent": 0, "candidate": 1}},
    ],
)
def test_unverified_improvement_stays_draft(published_candidate, monkeypatch, extra):
    store, candidate = published_candidate
    candidate["evaluations"] = [measured(candidate, **extra)]
    store.save("candidates", candidate)
    calls = github(monkeypatch, candidate)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed"
    assert candidate["pr_state"] == "draft"
    assert not any(operation(call) == "ready" for call in calls)


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_terminal_pr_is_observed_without_edit(published_candidate, monkeypatch, state):
    store, candidate = published_candidate
    calls = github(monkeypatch, candidate, state=state)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed"
    assert len(calls) == 1
    assert candidate["pr_state"] == state.lower()


def test_head_mismatch_blocks_all_mutation(published_candidate, monkeypatch):
    store, candidate = published_candidate
    calls = github(monkeypatch, candidate, head="different")
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "failed"
    assert len(calls) == 1
    assert candidate["pr_sync_error"]


@pytest.mark.parametrize("failure", ["edit", "ready"])
def test_sync_failure_can_retry_without_push_or_duplicate(
    published_candidate, monkeypatch, failure
):
    store, candidate = published_candidate
    candidate["evaluations"] = [measured(candidate)]
    store.save("candidates", candidate)
    github(monkeypatch, candidate, fail=failure)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "failed"
    assert candidate["pr_state"] == "draft" and candidate["pr_sync_error"]
    calls = github(monkeypatch, candidate)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed"
    assert [operation(c) for c in calls] == ["list", "edit", "ready"]
    assert candidate["pr_state"] == "ready" and candidate["pr_sync_error"] is None
    assert not candidate.get("last_error")


def test_sync_uses_rest_json_file_preserving_bilingual_body(
    published_candidate, monkeypatch
):
    from agent_harness_eval.rsi.runner import pr_title

    store, candidate = published_candidate
    candidate["diagnosis"]["summary"] = '修复 "引用" 与 `代码`'
    candidate["diagnosis"]["summary_en"] = 'Fix "quotes" and `$HOME`'
    store.save("candidates", candidate)
    calls = github(monkeypatch, candidate)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed"
    edit = next(call for call in calls if operation(call) == "edit")
    assert edit[:3] == ["gh", "api", "repos/example/fixture/pulls/7"]
    assert edit[edit.index("--method") + 1] == "PATCH"
    from pathlib import Path

    payload = read(Path(edit[edit.index("--input") + 1]))
    body = (store.path("candidates", candidate["id"]) / "PR.md").read_text()
    assert payload == {"title": pr_title(candidate), "body": body}
    assert "## 中文\n" in payload["body"] and "## English\n" in payload["body"]


def test_already_ready_pr_is_not_downgraded(published_candidate, monkeypatch):
    store, candidate = published_candidate
    calls = github(monkeypatch, candidate, draft=False)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed" and candidate["pr_state"] == "ready"
    assert [operation(c) for c in calls] == ["list", "edit"]


def test_legacy_evaluation_uses_frozen_artifact_commits(
    published_candidate, monkeypatch
):
    store, candidate = published_candidate
    evaluation = measured(candidate)
    del evaluation["candidate_commit"], evaluation["parent_commit"]
    candidate["evaluations"] = [evaluation]
    store.save("candidates", candidate)
    root = store.path("candidates", candidate["id"]) / "evaluations" / evaluation["id"]
    for side, commit in [
        ("parent", candidate["parent_commit"]),
        ("candidate", candidate["commit"]),
    ]:
        write(
            root / (side + "-artifacts.json"),
            {"binary": {"commit": commit, "sha256": "digest"}},
        )
    calls = github(monkeypatch, candidate)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed" and candidate["pr_state"] == "ready"
    assert operation(calls[-1]) == "ready"


def test_latest_evaluation_controls_promotion(published_candidate, monkeypatch):
    store, candidate = published_candidate
    candidate["evaluations"] = [measured(candidate), measured(candidate, "regressed")]
    store.save("candidates", candidate)
    calls = github(monkeypatch, candidate)
    candidate, _ = operate(store, candidate, "publish")
    assert candidate["pr_state"] == "draft" and operation(calls[-1]) == "edit"


@pytest.mark.parametrize("assessment", ["improved", "equal"])
def test_evaluation_before_publication_selects_initial_state(
    published_candidate, monkeypatch, assessment
):
    store, candidate = published_candidate
    candidate.pop("pr_url")
    candidate["evaluations"] = [measured(candidate, assessment)]
    store.save("candidates", candidate)
    calls = []

    async def checked(command, *args, **kwargs):
        calls.append(command.argv)
        if command.argv[:3] == ["gh", "pr", "list"]:
            output = "[]"
        elif command.argv[:3] == ["gh", "pr", "create"]:
            assert ("--draft" in command.argv) == (assessment != "improved")
            output = "https://github.com/example/fixture/pull/9"
        else:
            output = ""
        return {"returncode": 0, "stdout": output}

    monkeypatch.setattr(publication, "checked", checked)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed"
    assert candidate["pr_state"] == ("ready" if assessment == "improved" else "draft")
    assert len(calls) == 3


def test_missing_recorded_pr_is_not_recreated(published_candidate, monkeypatch):
    store, candidate = published_candidate
    calls = []

    async def checked(command, *args, **kwargs):
        calls.append(command.argv)
        return {"returncode": 0, "stdout": "[]"}

    monkeypatch.setattr(publication, "checked", checked)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "failed" and len(calls) == 1
    assert "duplicate" in candidate["pr_sync_error"]


def test_failed_evaluation_updates_pr_without_promoting(
    published_candidate, monkeypatch
):
    from agent_harness_eval.rsi import evaluation

    store, candidate = published_candidate

    async def evaluate(store, record, allow_live):
        record["evaluations"].append(measured(record, "failed", error="Build failed"))
        store.save("candidates", record)
        raise ValueError("Build failed")

    monkeypatch.setattr(evaluation, "evaluate", evaluate)
    calls = github(monkeypatch, candidate)
    candidate, action = operate(store, candidate, "evaluate")
    assert action["status"] == "failed" and action["error"] == "Build failed"
    assert candidate["evaluations"][-1]["assessment"] == "failed"
    assert candidate["pr_state"] == "draft"
    assert [operation(call) for call in calls] == ["list", "edit"]


def test_evaluation_startup_failure_does_not_publish_old_result(
    published_candidate, monkeypatch
):
    from agent_harness_eval.rsi import evaluation

    store, candidate = published_candidate
    candidate["evaluations"] = [measured(candidate)]
    store.save("candidates", candidate)

    async def evaluate(store, record, allow_live):
        raise ValueError("Template unavailable")

    monkeypatch.setattr(evaluation, "evaluate", evaluate)
    calls = github(monkeypatch, candidate)
    candidate, action = operate(store, candidate, "evaluate")
    assert action["status"] == "failed" and action["error"] == "Template unavailable"
    assert calls == []


@pytest.mark.parametrize("fail", [None, "edit", "ready"])
def test_evaluation_sync_failure_does_not_fail_measurement(
    published_candidate, monkeypatch, fail
):
    from agent_harness_eval.rsi import evaluation

    store, candidate = published_candidate

    async def evaluate(store, record, allow_live):
        record["evaluations"].append(measured(record))
        store.save("candidates", record)

    monkeypatch.setattr(evaluation, "evaluate", evaluate)
    github(monkeypatch, candidate, fail=fail)
    candidate, action = operate(store, candidate, "evaluate")
    assert action["status"] == "completed"
    assert candidate["phase"] == "validated"
    assert candidate["evaluations"][-1]["assessment"] == "improved"
    assert bool(candidate.get("pr_sync_error")) == bool(fail)
    if not fail:
        assert candidate["pr_state"] == "ready"
