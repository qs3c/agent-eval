import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from test_rsi import fixture_run

from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.rsi.evidence import create_case, verify_case
from agent_harness_eval.rsi.language import LEGACY_OBJECTIVES
from agent_harness_eval.rsi.store import Store
from agent_harness_eval.web import create_app


def test_rsi_api_is_local_and_does_not_accept_commands(tmp_path):
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source / "tasks", tmp_path / "tasks")
    shutil.copytree(source / "experiments", tmp_path / "experiments")
    app = create_app(tmp_path)
    client = TestClient(app)
    assert (
        client.get("/api/rsi", headers={"Host": "localhost"}).json()["profiles"] == []
    )
    headers = {"Host": "localhost"}
    assert (
        client.post(
            "/api/rsi/candidates",
            headers=headers,
            json={"case_id": "x", "profile": "test", "command": ["sh"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/rsi/cases",
            headers=headers,
            json={"run": "/etc/passwd", "agent": "a", "objective": "x"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/rsi/cases",
            headers={**headers, "Origin": "https://evil.example"},
            json={},
        ).status_code
        == 403
    )
    run = client.post(
        "/api/runs", headers=headers, json={"template": "controls"}
    ).json()
    response = client.post(
        "/api/rsi/cases",
        headers=headers,
        json={"run_id": run["id"], "agent": "positive", "objective": "fix"},
    )
    assert response.status_code == 422 and "finish" in response.json()["detail"]


def test_legacy_objectives_are_chinese_without_changing_frozen_case(tmp_path):
    store = Store(tmp_path)
    client = TestClient(create_app(tmp_path), base_url="http://localhost")
    for original, translated in LEGACY_OBJECTIVES.items():
        case = create_case(store, fixture_run(tmp_path), "a")
        case["objective"] = original
        store.save("cases", case)
        snapshot = store.path("cases", case["id"]) / "evidence/case.json"
        frozen = read(snapshot)
        frozen["objective"] = original
        write(snapshot, frozen)
        before = snapshot.read_bytes()
        response = client.get("/api/rsi/cases/" + case["id"])
        assert response.status_code == 200
        assert response.json()["objective"] == translated
        overview = client.get("/api/rsi").json()
        assert (
            next(c for c in overview["cases"] if c["id"] == case["id"])["objective"]
            == translated
        )
        verify_case(store, store.get("cases", case["id"]))
        assert snapshot.read_bytes() == before
