from __future__ import annotations

from fastapi.testclient import TestClient

# Cascade + Northbridge -> Hot, Vault -> Cold.
SCORED_CSV = (
    "company_name,industry,contact_title,contact_name,contact_email,website,company_size,source,notes\n"
    "Cascade Modular,Housing,VP Operations,Sarah Chen,sarah@cascade.com,cascade.com,240,referral,tenant maintenance leasing scheduling pain\n"
    "Northbridge Clinics,Healthcare,Practice Manager,Anika Rao,anika@northbridge.com,northbridge.com,1000+,webinar,patient appointment scheduling intake forms\n"
    "Vault Outfitters,Retail,COO,Jamie Russo,hello@vault.com,vault.com,560,csv,seasonal staff\n"
)

# Two rows; one is missing website + contact_email -> missing_data_rate=50%.
MISSING_CSV = (
    "company_name,industry,contact_email,website\n"
    "FullCo,Housing,owner@fullco.com,fullco.com\n"
    "SparseCo,Healthcare,,\n"
)


def _upload(client: TestClient, csv: str, name: str = "metrics-test") -> dict:
    files = {"file": ("leads.csv", csv.encode("utf-8"), "text/csv")}
    return client.post(
        "/api/batches/upload", files=files, data={"batch_name": name}
    ).json()


def _scored_batch_setup(client: TestClient) -> tuple[str, list[dict]]:
    up = _upload(client, SCORED_CSV)
    batch_id = up["batch_id"]
    client.post(f"/api/batches/{batch_id}/score")
    leads = client.get(f"/api/leads?batch_id={batch_id}").json()
    return batch_id, leads


def _lead_by(leads: list[dict], company_name: str) -> dict:
    return next(lead for lead in leads if lead["company_name"] == company_name)


def test_metrics_empty_database_returns_zeros(client: TestClient) -> None:
    body = client.get("/api/metrics/dashboard").json()
    assert body == {
        "total_leads_uploaded": 0,
        "total_leads_processed": 0,
        "hot_leads": 0,
        "warm_leads": 0,
        "cold_leads": 0,
        "outreach_generated": 0,
        "outreach_approved": 0,
        "outreach_rejected": 0,
        "approval_rate": 0.0,
        "leads_pushed": 0,
        "unique_leads_pushed": 0,
        "push_success_rate": 0.0,
        "failed_push_count": 0,
        "estimated_time_saved_minutes": 0,
        "estimated_time_saved_hours": 0.0,
        "average_lead_score": 0.0,
        "missing_data_rate": 0.0,
        "automation_coverage": 0.0,
    }


def test_metrics_counts_uploaded_leads(client: TestClient) -> None:
    _upload(client, SCORED_CSV)
    body = client.get("/api/metrics/dashboard").json()
    assert body["total_leads_uploaded"] == 3
    assert body["total_leads_processed"] == 0
    assert body["automation_coverage"] == 0.0


def test_metrics_counts_scored_and_priority(client: TestClient) -> None:
    _scored_batch_setup(client)
    body = client.get("/api/metrics/dashboard").json()
    assert body["total_leads_processed"] == 3
    assert body["hot_leads"] == 2
    assert body["cold_leads"] == 1
    assert body["warm_leads"] == 0
    assert body["automation_coverage"] == 100.0


def test_metrics_average_score(client: TestClient) -> None:
    _, leads = _scored_batch_setup(client)
    scores: list[int] = []
    for lead in leads:
        scores.append(
            client.get(f"/api/leads/{lead['id']}/score").json()["total_score"]
        )
    expected = round(sum(scores) / len(scores), 2)
    body = client.get("/api/metrics/dashboard").json()
    assert body["average_lead_score"] == expected


def test_metrics_estimated_time_saved(client: TestClient) -> None:
    _scored_batch_setup(client)
    body = client.get("/api/metrics/dashboard").json()
    assert body["estimated_time_saved_minutes"] == 15
    assert body["estimated_time_saved_hours"] == round(15 / 60, 2)


def test_metrics_missing_data_rate(client: TestClient) -> None:
    _upload(client, MISSING_CSV)
    body = client.get("/api/metrics/dashboard").json()
    assert body["total_leads_uploaded"] == 2
    assert body["missing_data_rate"] == 50.0


def test_metrics_counts_outreach_generated(client: TestClient) -> None:
    _, leads = _scored_batch_setup(client)
    client.post(f"/api/leads/{leads[0]['id']}/generate-outreach")
    client.post(f"/api/leads/{leads[1]['id']}/generate-outreach")
    body = client.get("/api/metrics/dashboard").json()
    assert body["outreach_generated"] == 2


def test_metrics_approval_rate_and_counts(client: TestClient) -> None:
    _, leads = _scored_batch_setup(client)
    for lead in leads:
        client.post(f"/api/leads/{lead['id']}/generate-outreach")
    client.post(f"/api/leads/{leads[0]['id']}/approve-outreach")
    client.post(f"/api/leads/{leads[1]['id']}/approve-outreach")
    client.post(
        f"/api/leads/{leads[2]['id']}/reject-outreach",
        json={"reason": "Too generic"},
    )

    body = client.get("/api/metrics/dashboard").json()
    assert body["outreach_generated"] == 3
    assert body["outreach_approved"] == 2
    assert body["outreach_rejected"] == 1
    assert body["approval_rate"] == round((2 / 3) * 100, 2)


def test_metrics_push_success_rate_counts_mock_and_success(
    client: TestClient,
) -> None:
    _, leads = _scored_batch_setup(client)
    hot = _lead_by(leads, "Cascade Modular")
    client.post(f"/api/leads/{hot['id']}/push", json={"integration_type": "slack"})
    body = client.get("/api/metrics/dashboard").json()
    assert body["leads_pushed"] == 1
    assert body["unique_leads_pushed"] == 1
    assert body["push_success_rate"] == 100.0
    assert body["failed_push_count"] == 0


def test_metrics_unique_leads_pushed_dedupes_repeat_pushes(
    client: TestClient,
) -> None:
    """Same lead pushed twice -> leads_pushed=2, unique_leads_pushed=1."""
    _, leads = _scored_batch_setup(client)
    hot = _lead_by(leads, "Cascade Modular")
    client.post(f"/api/leads/{hot['id']}/push", json={"integration_type": "slack"})
    client.post(f"/api/leads/{hot['id']}/push", json={"integration_type": "slack"})
    body = client.get("/api/metrics/dashboard").json()
    assert body["leads_pushed"] == 2
    assert body["unique_leads_pushed"] == 1
    assert body["push_success_rate"] == 100.0


def test_metrics_failed_push_count_uses_monkeypatched_sender(
    client: TestClient,
    monkeypatch,
) -> None:
    from app.services import integration_push as push_service

    monkeypatch.setattr(
        push_service,
        "send_slack_payload",
        lambda _url, _payload: ("failed", "HTTP 500: simulated"),
    )

    _, leads = _scored_batch_setup(client)
    hot = _lead_by(leads, "Cascade Modular")
    client.post(f"/api/leads/{hot['id']}/push", json={"integration_type": "slack"})

    body = client.get("/api/metrics/dashboard").json()
    assert body["failed_push_count"] == 1
    assert body["leads_pushed"] == 0
    assert body["unique_leads_pushed"] == 0
    assert body["push_success_rate"] == 0.0
