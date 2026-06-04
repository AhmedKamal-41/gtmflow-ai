"""Tests for the one-click demo endpoint (POST /api/demo/run)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_demo_run_seeds_and_runs_full_flow(client: TestClient) -> None:
    res = client.post("/api/demo/run")
    assert res.status_code == 201
    body = res.json()

    # The sample CSV is tuned to 2 Hot / 4 Warm / 4 Cold.
    assert body["total_leads"] == 10
    assert body["hot"] == 2
    assert body["warm"] == 4
    assert body["cold"] == 4
    assert body["hot"] + body["warm"] + body["cold"] == body["total_leads"]

    # Only Hot leads get generated, approved, and pushed.
    assert body["outreach_generated"] == body["hot"]
    assert body["outreach_approved"] == body["hot"]
    assert body["leads_pushed"] == body["hot"]  # mock_success by default


def test_demo_run_populates_metrics(client: TestClient) -> None:
    run = client.post("/api/demo/run").json()
    metrics = client.get("/api/metrics/dashboard").json()

    assert metrics["total_leads_uploaded"] == 10
    assert metrics["total_leads_processed"] == 10
    assert metrics["automation_coverage"] == 100.0
    assert metrics["hot_leads"] == run["hot"]
    assert metrics["outreach_approved"] == run["outreach_approved"]
    assert metrics["unique_leads_pushed"] == run["leads_pushed"]


def test_demo_run_batch_is_reachable(client: TestClient) -> None:
    run = client.post("/api/demo/run").json()
    batch_id = run["batch_id"]

    batch = client.get(f"/api/batches/{batch_id}")
    assert batch.status_code == 200
    assert batch.json()["status"] == "scored"

    leads = client.get(f"/api/leads?batch_id={batch_id}")
    assert leads.status_code == 200
    assert len(leads.json()) == 10


def test_demo_run_is_additive(client: TestClient) -> None:
    """Each run creates a fresh batch; it never wipes prior data."""
    first = client.post("/api/demo/run").json()
    second = client.post("/api/demo/run").json()
    assert first["batch_id"] != second["batch_id"]

    batches = client.get("/api/batches").json()
    assert len(batches) == 2
