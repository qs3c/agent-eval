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


def test_explicit_bind_host_preserves_request_boundaries(tmp_path):
    origin = "http://100.100.100.10:8765"
    client = TestClient(create_app(tmp_path, bind_host="100.100.100.10"), base_url=origin)
    assert client.get("/").status_code == 200
    assert client.get("/api/runs").status_code == 200
    for host in ("evil.example", "100.100.100.11:8765", "100.100.100.10.evil.example"):
        assert client.get("/", headers={"Host": host}).status_code == 403
    assert client.post("/api/runs", headers={"Origin": origin}, json={}).status_code == 422
    assert client.post("/api/runs", headers={"Origin": "http://evil.example"}, json={}).status_code == 403
    assert client.post("/api/runs", headers={"Origin": origin, "Sec-Fetch-Site": "cross-site"}, json={}).status_code == 403


def test_default_host_rejects_remote_address(client):
    assert client.get("/", headers={"Host": "100.100.100.10:8765"}).status_code == 403


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "*.example.com"])
def test_bind_host_requires_specific_ip(tmp_path, host):
    with pytest.raises(ValueError):
        create_app(tmp_path, bind_host=host)


def test_ipv6_bind_host(tmp_path):
    client = TestClient(create_app(tmp_path, bind_host="fd7a:115c:a1e0::1"), base_url="http://[fd7a:115c:a1e0::1]:8765")
    assert client.get("/").status_code == 200


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


def test_incident_routes_are_scoped_to_registered_runs(client):
    assert client.get("/api/runs/unknown/incidents").status_code == 404
    run = client.post("/api/runs", json={"template": "controls"}).json()
    assert client.get(f"/api/runs/{run['id']}/incidents").json()["incidents"] == []
    assert client.get(f"/api/runs/{run['id']}/incidents/unknown").status_code == 404


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


def test_batch_regrade_api_is_idempotent_and_scoped(client):
    import hashlib
    import json

    from agent_harness_eval.guardian.evidence import write

    console = client.app.state.console
    output = console.artifacts / "regrade-example"
    exp = json.loads((console.root / "experiments/controls.json").read_text())
    exp["benchmark"]["score_kind"] = "resolved"
    write(output / "manifest.json", {"experiment": exp, "tasks": {}})
    write(output / "batch-state.json", {"phase": "completed", "batches": [
        {"number": n, "phase": "completed", "tasks": ["file-create"]} for n in (1, 2)]})
    console.import_history()
    run_id = "history-" + hashlib.sha256(output.name.encode()).hexdigest()[:20]
    url = f"/api/runs/{run_id}/batches/1/regrade"
    first = client.post(url, json={})
    assert first.status_code == 202
    assert client.post(url, json={}).json()["id"] == first.json()["id"]
    other = client.post(f"/api/runs/{run_id}/batches/2/regrade", json={})
    assert other.status_code == 202
    assert other.json()["id"] != first.json()["id"]
    detail = client.get(f"/api/runs/{run_id}").json()["official_regrading"]
    assert detail["concurrency"] == 2
    assert [b["status"] for b in detail["batches"]] == ["queued", "queued"]
    assert client.post(f"/api/runs/{run_id}/batches/99/regrade", json={}).status_code == 422
    assert client.post(url, json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get(f"/api/runs/{run_id}/batches/1/regrade-log").json() == {"logs": []}
