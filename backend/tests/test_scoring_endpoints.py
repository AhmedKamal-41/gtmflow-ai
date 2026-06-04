from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LeadScore, WorkflowEvent

ZERO_UUID = "00000000-0000-0000-0000-000000000000"

# Three rows seeded to produce a deterministic Hot/Hot/Cold mix.
# `notes` is unknown to the schema, so it's preserved in cleaned_data and
# fed into the pain-point keyword scorer (the realistic field shape sales
# teams send: company facts plus a free-text reason-to-call).
MIXED_CSV = (
    "company_name,industry,contact_title,contact_name,contact_email,website,company_size,source,notes\n"
    "Cascade Property Mgmt,property management,Property Manager,Sarah Chen,sarah@cascade.com,cascade.com,201-500,referral,needs better tenant maintenance scheduling\n"
    "Northbridge Clinics,clinic,Practice Manager,Anika Rao,anika@northbridge.com,northbridge.com,1000+,webinar,patient appointment scheduling and intake pain\n"
    "Vault Outfitters,Retail,COO,Jamie Russo,hello@vault.com,vault.com,11-50,csv,seasonal staff churn\n"
)


def _upload(client: TestClient, csv: str = MIXED_CSV, batch_name: str = "test") -> dict:
    files = {"file": ("leads.csv", csv.encode("utf-8"), "text/csv")}
    data = {"batch_name": batch_name}
    return client.post("/api/batches/upload", files=files, data=data).json()


def _first_lead_id(client: TestClient, batch_id: str) -> str:
    leads = client.get(f"/api/leads?batch_id={batch_id}").json()
    assert leads, "no leads found in batch"
    return leads[0]["id"]


def test_post_lead_score_creates_lead_score(
    client: TestClient, db_session: Session
) -> None:
    upload = _upload(client)
    lead_id = _first_lead_id(client, upload["batch_id"])

    response = client.post(f"/api/leads/{lead_id}/score")
    assert response.status_code == 200
    body = response.json()
    assert body["lead_id"] == lead_id
    assert body["priority"] in {"Hot", "Warm", "Cold"}
    assert 0 <= body["total_score"] <= 100
    assert "industry_fit" in body["score_breakdown"]
    assert "industry_terms" in body["matched_signals"]
    assert isinstance(body["reasoning"], str) and body["reasoning"]

    rows = (
        db_session.execute(
            select(LeadScore).where(LeadScore.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].total_score == body["total_score"]
    assert rows[0].priority == body["priority"]

    # lead status should now be "scored"
    lead_detail = client.get(f"/api/leads/{lead_id}").json()
    assert lead_detail["status"] == "scored"


def test_post_lead_score_updates_existing_row(
    client: TestClient, db_session: Session
) -> None:
    upload = _upload(client)
    lead_id = _first_lead_id(client, upload["batch_id"])

    first = client.post(f"/api/leads/{lead_id}/score").json()
    second = client.post(f"/api/leads/{lead_id}/score").json()
    assert first == second  # deterministic

    rows = (
        db_session.execute(
            select(LeadScore).where(LeadScore.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # upserted, not duplicated


def test_post_batch_score_scores_all_leads(client: TestClient) -> None:
    upload = _upload(client)
    batch_id = upload["batch_id"]

    response = client.post(f"/api/batches/{batch_id}/score")
    assert response.status_code == 200
    body = response.json()
    assert body["batch_id"] == batch_id
    assert body["scored_leads"] == 3
    assert body["hot"] + body["warm"] + body["cold"] == 3
    assert body["hot"] >= 2  # property mgmt + clinic should both be Hot
    assert body["cold"] >= 1  # retail should be Cold
    assert isinstance(body["average_score"], (int, float))

    detail = client.get(f"/api/batches/{batch_id}").json()
    assert detail["status"] == "scored"


def test_get_lead_score_returns_persisted_payload(client: TestClient) -> None:
    upload = _upload(client)
    lead_id = _first_lead_id(client, upload["batch_id"])
    posted = client.post(f"/api/leads/{lead_id}/score").json()

    fetched = client.get(f"/api/leads/{lead_id}/score")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["lead_id"] == lead_id
    assert body["total_score"] == posted["total_score"]
    assert body["priority"] == posted["priority"]
    assert body["score_breakdown"] == posted["score_breakdown"]
    assert body["matched_signals"] == posted["matched_signals"]
    assert body["reasoning"] == posted["reasoning"]


def test_post_unknown_lead_returns_404(client: TestClient) -> None:
    assert client.post(f"/api/leads/{ZERO_UUID}/score").status_code == 404


def test_get_unknown_lead_returns_404(client: TestClient) -> None:
    assert client.get(f"/api/leads/{ZERO_UUID}/score").status_code == 404


def test_get_unscored_lead_returns_404(client: TestClient) -> None:
    upload = _upload(client)
    lead_id = _first_lead_id(client, upload["batch_id"])
    # We have not POSTed a score yet -> GET must 404.
    assert client.get(f"/api/leads/{lead_id}/score").status_code == 404


def test_post_unknown_batch_returns_404(client: TestClient) -> None:
    assert client.post(f"/api/batches/{ZERO_UUID}/score").status_code == 404


def test_lead_scored_workflow_event_is_created(
    client: TestClient, db_session: Session
) -> None:
    upload = _upload(client)
    lead_id = _first_lead_id(client, upload["batch_id"])
    client.post(f"/api/leads/{lead_id}/score")

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    lead_scored = [e for e in events if e.event_type == "lead_scored"]
    assert len(lead_scored) == 1
    assert "total_score" in lead_scored[0].event_data
    assert "priority" in lead_scored[0].event_data


def test_batch_scored_workflow_event_is_created(
    client: TestClient, db_session: Session
) -> None:
    upload = _upload(client)
    batch_id = upload["batch_id"]
    summary = client.post(f"/api/batches/{batch_id}/score").json()

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.batch_id == UUID(batch_id))
        )
        .scalars()
        .all()
    )
    batch_scored = [e for e in events if e.event_type == "batch_scored"]
    assert len(batch_scored) == 1
    data = batch_scored[0].event_data
    assert data["scored_leads"] == summary["scored_leads"]
    assert data["hot"] == summary["hot"]
    assert data["warm"] == summary["warm"]
    assert data["cold"] == summary["cold"]
