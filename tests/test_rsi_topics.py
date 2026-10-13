import argparse
import sys

import pytest
from fastapi.testclient import TestClient
from test_rsi import create, fixture_run, operate, setup  # noqa: F401

from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.rsi.cli import register
from agent_harness_eval.rsi.evidence import verify_case
from agent_harness_eval.rsi.language import DEFAULT_OBJECTIVE
from agent_harness_eval.rsi.store import Store
from agent_harness_eval.web import create_app


def test_case_can_be_created_without_user_objective(tmp_path):
    fixture_run(tmp_path)
    app = create_app(tmp_path)
    client = TestClient(app, base_url="http://localhost")
    runs = client.get("/api/runs").json()["runs"]
    assert len(runs) == 1
    payload = {"run_id": runs[0]["id"], "agent": "a"}
    result = client.post("/api/rsi/cases", json=payload)
    assert result.status_code == 201, result.text
    case = result.json()
    assert case["objective"] == DEFAULT_OBJECTIVE
    assert "diagnosis_topic" not in case
    verify_case(Store(tmp_path), case)
    assert (
        client.post(
            "/api/rsi/cases", json={**payload, "objective": "Guess the cause"}
        ).status_code
        == 422
    )
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["rsi-case", "run", "--agent", "a"])
    assert args.agent == "a" and not hasattr(args, "objective")


def test_agent_topic_survives_failed_repair_and_does_not_change_evidence(setup):  # noqa: F811
    store, case, _, _, profile = setup
    root = store.path("cases", case["id"]) / "evidence"
    frozen = (root / "case.json").read_bytes()
    client = TestClient(create_app(store.project), base_url="http://localhost")
    url = "/api/rsi/cases/" + case["id"]
    assert "diagnosis_topic" not in client.get(url).json()
    profile["checks"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(1)"]
    write(store.project / "rsi-profiles/fixture.json", profile)
    candidate, action = operate(store, create(setup))
    assert action["status"] == "failed"
    assert candidate["phase"] == "failed"
    assert candidate["diagnosis"]["topic"] == "测试值错误导致执行失败"
    shown = client.get(url).json()
    assert shown["diagnosis_topic"] == candidate["diagnosis"]["topic"]
    assert shown["diagnosis_candidate_id"] == candidate["id"]
    assert (
        client.get("/api/rsi").json()["cases"][0]["diagnosis_topic"]
        == shown["diagnosis_topic"]
    )
    context = read(store.path("candidates", candidate["id"]) / "adapter-context.json")
    assert context["objective"] == DEFAULT_OBJECTIVE != case["objective"]
    assert "topic" in context["result_schema"]["required"]
    assert "原因未确定" in context["instructions"]
    # A new pending attempt must not erase an existing diagnosis.
    create(setup)
    assert client.get(url).json()["diagnosis_candidate_id"] == candidate["id"]
    assert (root / "case.json").read_bytes() == frozen
    verify_case(store, store.get("cases", case["id"]))


@pytest.mark.parametrize("replacement", ["English topic", "", "django-15022"])
def test_non_chinese_topics_are_not_published(setup, replacement):  # noqa: F811
    store, case, _, adapter, _ = setup
    adapter.write_text(
        adapter.read_text(encoding="utf-8").replace(
            "测试值错误导致执行失败", replacement
        ),
        encoding="utf-8",
    )
    candidate, action = operate(store, create(setup))
    assert action["status"] == "failed"
    assert "diagnosis" not in candidate
    client = TestClient(create_app(store.project), base_url="http://localhost")
    assert "diagnosis_topic" not in client.get("/api/rsi/cases/" + case["id"]).json()


def test_legacy_diagnosis_does_not_use_objective_or_summary_as_topic(setup):  # noqa: F811
    store, case, *_ = setup
    candidate = create(setup)
    candidate["diagnosis"] = {"summary": "Legacy summary", "changes": "Legacy changes"}
    store.save("candidates", candidate)
    client = TestClient(create_app(store.project), base_url="http://localhost")
    assert "diagnosis_topic" not in client.get("/api/rsi/cases/" + case["id"]).json()
