import asyncio
import json
import subprocess
import sys

import pytest

from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.rsi.evidence import create_case, verify_case
from agent_harness_eval.rsi.runner import create_candidate, perform
from agent_harness_eval.rsi.store import Store


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def fixture_run(root):
    run = root / "artifacts/eval"
    write(
        run / "manifest.json",
        {
            "experiment": {
                "name": "rsi-test",
                "agents": [{"id": "a", "implementation": "nop", "version": "old"}],
                "benchmark": {"id": "fixture", "tasks": ["/task"]},
            },
            "tasks": {"task": {"sha256": "abc"}},
            "evidence_kind": "fixture",
        },
    )
    write(
        run / "jobs/a/t/result.json",
        {
            "task_name": "task",
            "trial_name": "t",
            "exception_info": {"exception_type": "ToolTimeout"},
        },
    )
    write(run / "jobs/a/t/incidents/i/incident.json", {"reason": "tool_overdue"})
    write(run / "jobs/a/t/human-intervention.json", {"human_intervened": True})
    return run


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    case = create_case(
        store, fixture_run(tmp_path), "a", objective="Fix the failing harness"
    )
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@localhost")
    (source / "harness.py").write_text("VALUE = 0\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    adapter = tmp_path / "adapter.py"
    adapter.write_text("""import sys,json,pathlib
context=json.loads(sys.stdin.readline())
(pathlib.Path(context['workspace'])/'harness.py').write_text('VALUE = 1\\n')
pathlib.Path(context['result_path']).write_text(json.dumps({'topic':'测试值错误导致执行失败','summary':'修复测试用例','changes':'设置正确的值','summary_en':'Repair a fixture','changes_en':'Set the correct value','evidence_refs':['e0001'],'limitations':'仅用于测试','limitations_en':'Fixture only'}), encoding='utf-8')
""")
    profile = {
        "repository": str(source),
        "parent_ref": "main",
        "adapter": {
            "argv": [sys.executable, str(adapter)],
            "version": "fixture-1",
            "timeout_seconds": 5,
        },
        "checks": [
            {
                "argv": [
                    sys.executable,
                    "-B",
                    "-c",
                    "exec(open('harness.py').read());assert VALUE == 1",
                ],
                "timeout_seconds": 5,
            }
        ],
    }
    write(tmp_path / "rsi-profiles/fixture.json", profile)
    return store, case, source, adapter, profile


def create(setup):
    store, case, *_ = setup
    return asyncio.run(create_candidate(store, case["id"], "fixture"))


def operate(store, candidate, kind="improve"):
    action = store.enqueue(candidate["id"], kind)
    asyncio.run(perform(store, action["id"]))
    return store.get("candidates", candidate["id"]), store.action(action["id"])


def test_evidence_sources_missing_truncated_and_immutable(tmp_path):
    run = fixture_run(tmp_path)
    log = run / "jobs/a/t/agent/events.jsonl"
    log.parent.mkdir()
    log.write_bytes(b"x" * (1024 * 1024 + 5))
    store = Store(tmp_path)
    case = create_case(store, run, "a")
    categories = {e["category"] for e in case["evidence"]}
    assert {"manual_intervention", "guardian_incident", "agent_trace"} <= categories
    assert any(e.get("error") for e in case["evidence"])
    tail = next(e for e in case["evidence"] if e["source"].endswith("events.jsonl"))
    assert tail["truncated"] and tail["offset"] == 5
    evidence = verify_case(store, case)
    (evidence / tail["file"]).write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        verify_case(store, case)
    assert log.stat().st_size == 1024 * 1024 + 5


def test_complete_improvement_preserves_source_and_links_parent(setup):
    store, case, source, *_ = setup
    parent_commit = git(source, "rev-parse", "HEAD")
    candidate, action = operate(store, create(setup))
    assert action["status"] == "completed"
    assert candidate["phase"] == "validated" and candidate["commit"] != parent_commit
    assert (source / "harness.py").read_text() == "VALUE = 0\n"
    assert git(source, "rev-parse", "HEAD") == parent_commit
    child = asyncio.run(create_candidate(store, case["id"], "fixture", candidate["id"]))
    assert child["parent_commit"] == candidate["commit"]
    assert (
        "unverified"
        in (store.path("candidates", candidate["id"]) / "PR.md").read_text()
    )
    body = (store.path("candidates", candidate["id"]) / "PR.md").read_text(
        encoding="utf-8"
    )
    assert body.index("## 中文") < body.index("## English")
    assert "修复测试用例" in body and "Repair a fixture" in body
    assert "仅用于测试" in body and "Fixture only" in body
    context = read(store.path("candidates", candidate["id"]) / "adapter-context.json")
    assert "in Chinese" in context["instructions"]
    assert "summary_en" in context["result_schema"]["required"]


@pytest.mark.parametrize(
    "change", ["silent", "bad_citation", "bad_check", "changed_check", "no_patch"]
)
def test_failed_adapter_or_validation_cannot_publish(setup, change):
    store, _, _, adapter, profile = setup
    if change == "silent":
        adapter.write_text("import time;time.sleep(30)")
        profile["adapter"]["timeout_seconds"] = 1
    if change == "bad_citation":
        adapter.write_text(adapter.read_text().replace("e0001", "imaginary"))
    if change == "bad_check":
        profile["checks"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(1)"]
    if change == "changed_check":
        profile["checks"][0]["argv"] = [
            sys.executable,
            "-c",
            "open('harness.py','w').write('VALUE=2')",
        ]
    if change == "no_patch":
        adapter.write_text(adapter.read_text().replace("VALUE = 1", "VALUE = 0"))
    write(store.project / "rsi-profiles/fixture.json", profile)
    candidate, action = operate(store, create(setup))
    assert action["status"] == "failed" and candidate["phase"] == "failed"
    with pytest.raises(ValueError):
        store.enqueue(candidate["id"], "publish")


def test_duplicate_queue_and_interrupted_action(setup):
    store, *_ = setup
    candidate = create(setup)
    action = store.enqueue(candidate["id"], "improve")
    with pytest.raises(ValueError, match="active"):
        store.enqueue(candidate["id"], "improve")
    store.claim(action["id"])
    store.recover()
    assert store.action(action["id"])["status"] == "interrupted"
    assert store.get("candidates", candidate["id"])["phase"] == "interrupted"


def test_recovery_preserves_active_standalone_worker(setup):
    import fcntl

    store, *_ = setup
    candidate = create(setup)
    action = store.enqueue(candidate["id"], "improve")
    with (store.path("candidates", candidate["id"]) / "operation.lock").open(
        "a"
    ) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store.claim(action["id"])
        store.recover()
        assert store.action(action["id"])["status"] == "running"
    store.recover()
    assert store.action(action["id"])["status"] == "interrupted"


def test_console_dispatches_real_rsi_worker(setup, monkeypatch):
    import time
    from pathlib import Path

    from agent_harness_eval.console import Console

    store, *_ = setup
    candidate = create(setup)
    action = store.enqueue(candidate["id"], "improve")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    console = Console(store.project)
    console.start()
    try:
        deadline = time.monotonic() + 30
        while store.action(action["id"])["status"] in {"queued", "running"}:
            assert time.monotonic() < deadline, "Console did not finish RSI action"
            time.sleep(0.1)
        assert store.action(action["id"])["status"] == "completed"
        assert store.get("candidates", candidate["id"])["phase"] == "validated"
    finally:
        console.close()


def test_evidence_path_escape_rejected(tmp_path):
    run = fixture_run(tmp_path)
    with pytest.raises(ValueError):
        create_case(Store(tmp_path), run, "../../outside")
    with pytest.raises(ValueError):
        create_case(Store(tmp_path), run, "a", ["../../outside"])
    with pytest.raises(ValueError):
        Store(tmp_path).get("cases", "../anything")


def test_comparison_rejects_assisted_and_preserves_error_meaning(tmp_path):
    from agent_harness_eval.config import Experiment
    from agent_harness_eval.rsi.evaluation import compare

    exp = Experiment(
        name="fixture",
        agents=[{"id": "a", "implementation": "nop", "version": "1"}],
        benchmark={"id": "fixture", "tasks": ["task"]},
    ).model_dump(mode="json")
    for name, reward in (("parent", 0), ("candidate", 1)):
        write(
            tmp_path / name / "manifest.json",
            {"experiment": exp, "tasks": {"task": {"sha256": "same"}}},
        )
        write(
            tmp_path / name / "report.json",
            {
                "trials": [
                    {"task": "task", "metrics": {"reward": reward}, "exception": None}
                ]
            },
        )
    assert (
        compare(tmp_path / "parent", tmp_path / "candidate")["assessment"] == "improved"
    )
    path = tmp_path / "candidate/report.json"
    data = read(path)
    data["trials"][0]["guardian"] = {"assisted": True}
    write(path, data)
    assert (
        compare(tmp_path / "parent", tmp_path / "candidate")["assessment"]
        == "inconclusive"
    )
    data["trials"][0].pop("guardian")
    write(path, data)
    parent = read(tmp_path / "parent/report.json")
    parent["trials"][0]["exception"] = {"type": "timeout"}
    write(tmp_path / "parent/report.json", parent)
    comparison = compare(tmp_path / "parent", tmp_path / "candidate")
    assert (
        comparison["assessment"] == "inconclusive"
        and comparison["reliability_change"] == "improved"
    )
    manifest = read(tmp_path / "candidate/manifest.json")
    manifest["experiment"]["agents"][0]["kwargs"] = {"temperature": 0.7}
    write(tmp_path / "candidate/manifest.json", manifest)
    assert (
        "contract mismatch: agents"
        in compare(tmp_path / "parent", tmp_path / "candidate")["issues"]
    )


def test_publication_retry_recovers_existing_pr_without_push(setup, monkeypatch):
    from agent_harness_eval.rsi import publication

    store, _, source, _, profile = setup
    git(source, "remote", "add", "origin", "https://github.com/example/fixture.git")
    profile["github_repository"] = "example/fixture"
    write(store.project / "rsi-profiles/fixture.json", profile)
    candidate, _ = operate(store, create(setup))
    calls = []

    async def existing(command, *args, **kwargs):
        calls.append(command.argv)
        return {
            "returncode": 0,
            "stdout": json.dumps(
                [
                    {
                        "url": "https://github.com/example/fixture/pull/7",
                        "headRefOid": candidate["commit"],
                    }
                ]
            ),
        }

    monkeypatch.setattr(publication, "checked", existing)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed" and candidate["pr_url"].endswith("/7")
    assert len(calls) == 1 and calls[0][2] == "list"


def test_manual_snapshot_reference_and_frozen_instructions(tmp_path):
    run = fixture_run(tmp_path)
    snapshot = run / "interventions/session/process-before.json"
    write(snapshot, {"pid": 335, "command": "grep"})
    intervention = run / "jobs/a/t/human-intervention.json"
    write(intervention, {"evidence_directory": str(snapshot.parent)})
    store = Store(tmp_path)
    case = create_case(store, run, "a")
    item = next(
        e for e in case["evidence"] if e["category"] == "manual_diagnostic_snapshot"
    )
    assert item["source"] == "interventions/session/process-before.json"
    root = verify_case(store, case)
    write(root / "case.json", {"objective": "changed"})
    with pytest.raises(ValueError, match="instructions changed"):
        verify_case(store, case)
    write(intervention, {"evidence_directory": str(tmp_path)})
    case = create_case(store, run, "a")
    assert any("not followed" in e.get("error", "") for e in case["evidence"])
    root = verify_case(store, case)
    write(root / "index.json", [])
    with pytest.raises(ValueError, match="index changed"):
        verify_case(store, case)


def test_publication_creates_draft_at_exact_commit_with_body_file(setup, monkeypatch):
    from pathlib import Path

    from agent_harness_eval.rsi import publication

    store, _, source, _, profile = setup
    git(source, "remote", "add", "origin", "https://github.com/example/fixture.git")
    profile["github_repository"] = "example/fixture"
    write(store.project / "rsi-profiles/fixture.json", profile)
    candidate, _ = operate(store, create(setup))
    calls = []

    async def fake(command, *args, **kwargs):
        calls.append(command.argv)
        output = ""
        if command.argv[:3] == ["gh", "pr", "list"]:
            output = "[]"
        if command.argv[:3] == ["gh", "pr", "create"]:
            assert "--draft" in command.argv
            title = command.argv[command.argv.index("--title") + 1]
            assert "修复测试用例 / Repair a fixture" in title
            body = Path(command.argv[command.argv.index("--body-file") + 1]).read_text()
            assert candidate["commit"] in body and "unverified" in body
            output = "https://github.com/example/fixture/pull/8\n"
        return {"returncode": 0, "stdout": output}

    monkeypatch.setattr(publication, "checked", fake)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "completed" and candidate["pr_url"].endswith("/8")
    assert len(calls) == 3
    assert (
        calls[1][-1] == f"{candidate['commit']}:refs/heads/codex/rsi-{candidate['id']}"
    )
    assert not any("--force" in call for call in calls)


def test_publication_rejects_destination_mismatch_before_external_commands(
    setup, monkeypatch
):
    from agent_harness_eval.rsi import publication

    store, _, source, _, profile = setup
    git(source, "remote", "add", "origin", "https://github.com/example/source.git")
    profile["github_repository"] = "example/different"
    write(store.project / "rsi-profiles/fixture.json", profile)
    candidate, _ = operate(store, create(setup))

    async def forbidden(*args, **kwargs):
        pytest.fail("External command attempted for mismatched destination")

    monkeypatch.setattr(publication, "checked", forbidden)
    candidate, action = operate(store, candidate, "publish")
    assert action["status"] == "failed" and "origin" in action["error"]


def test_live_evaluation_requires_explicit_permission(setup):
    from pathlib import Path

    store, _, _, _, profile = setup
    template = store.project / "live.json"
    write(
        template,
        {
            "name": "live",
            "agents": [{"id": "a", "implementation": "nop", "version": "1"}],
            "model": {"mode": "live", "name": "fixture"},
            "benchmark": {
                "id": "fixture",
                "tasks": [
                    str(Path(__file__).resolve().parents[1] / "tasks/file-create")
                ],
            },
        },
    )
    profile["evaluation"] = str(template)
    write(store.project / "rsi-profiles/fixture.json", profile)
    candidate, _ = operate(store, create(setup))
    candidate, action = operate(store, candidate, "evaluate")
    assert action["status"] == "failed" and "acknowledgement" in action["error"]
    assert not candidate["evaluations"]
