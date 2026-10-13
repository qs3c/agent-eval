import shutil
from pathlib import Path

from fastapi.testclient import TestClient

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
