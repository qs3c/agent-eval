import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_harness_eval.web import create_app


@pytest.fixture
def client(tmp_path):
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source / "tasks", tmp_path / "tasks")
    (tmp_path / "experiments").mkdir()
    shutil.copyfile(
        source / "experiments/controls.json", tmp_path / "experiments/controls.json"
    )
    # No lifespan here: API contract tests never start Docker work.
    return TestClient(create_app(tmp_path), base_url="http://localhost")


def test_api_boundaries(client):
    assert client.get("/api/templates").status_code == 200
    assert (
        client.post(
            "/api/runs", headers={"Origin": "https://evil.example"}, json={}
        ).status_code
        == 403
    )
    assert client.get("/api/runs", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/runs", content="{}").status_code == 415
    assert (
        client.post(
            "/api/runs", json={"template": "controls", "agents": []}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/runs", json={"template": "controls", "secret": "x" * 65536}
        ).status_code
        == 413
    )
    assert client.get("/api/runs/unknown").status_code == 404
    assert client.post("/api/runs", json={"template": "missing"}).status_code == 422


def test_create_read_compare(client):
    response = client.post(
        "/api/runs",
        json={"template": "controls"},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["expected"] == 4
    assert client.get("/api/runs/" + item["id"]).json()["status"] == "queued"
    compared = client.get(
        "/api/compare", params={"left": item["id"], "right": item["id"]}
    ).json()
    assert compared["compatible"] is False
    assert "缺少结果报告" in compared["differences"]


def test_trace_routes_are_scoped_to_registered_runs(client):
    assert client.get("/api/runs/unknown/traces").status_code == 404
    run = client.post("/api/runs", json={"template": "controls"}).json()
    assert client.get(f"/api/runs/{run['id']}/traces").json() == []
    assert client.get(f"/api/runs/{run['id']}/traces/not-a-trial").status_code == 404


def test_experiment_comparison_api_without_starting_work(client):
    assert client.get("/api/runs/unknown/comparison").status_code == 404
    run = client.post("/api/runs", json={"template": "controls"}).json()
    response = client.get(f"/api/runs/{run['id']}/comparison")
    assert response.status_code == 200
    data = response.json()
    assert data["task_count"] == 2
    assert set(data["agents"]) == {"positive", "negative"}
    assert all(
        a["score"] is None and a["missing"] == 2 for a in data["agents"].values()
    )
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "queued"
