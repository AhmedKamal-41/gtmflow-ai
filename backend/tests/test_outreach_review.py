from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WorkflowEvent

ZERO_UUID = "00000000-0000-0000-0000-000000000000"

SCORED_CSV = (
    "company_name,industry,contact_title,contact_name,contact_email,website,company_size,source,notes\n"
    "Cascade Modular,Housing,VP Operations,Sarah Chen,sarah@cascade.com,cascade.com,240,referral,tenant maintenance leasing scheduling pain\n"
)


def _upload(client: TestClient, csv: str = SCORED_CSV) -> dict:
    files = {"file": ("leads.csv", csv.encode("utf-8"), "text/csv")}
    return client.post(
        "/api/batches/upload", files=files, data={"batch_name": "review-test"}
    ).json()


def _scored_lead_with_outreach(client: TestClient) -> str:
    up = _upload(client)
    client.post(f"/api/batches/{up['batch_id']}/score")
    lead = client.get(f"/api/leads?batch_id={up['batch_id']}").json()[0]
    client.post(f"/api/leads/{lead['id']}/generate-outreach")
    return lead["id"]


def _scored_lead_without_outreach(client: TestClient) -> str:
    up = _upload(client)
    client.post(f"/api/batches/{up['batch_id']}/score")
    return client.get(f"/api/leads?batch_id={up['batch_id']}").json()[0]["id"]


# --------- approve ---------

def test_approve_outreach_creates_workflow_event(
    client: TestClient, db_session: Session
) -> None:
    lead_id = _scored_lead_with_outreach(client)

    response = client.post(f"/api/leads/{lead_id}/approve-outreach")
    assert response.status_code == 200
    body = response.json()
    assert body["event_type"] == "outreach_approved"
    assert body["lead_id"] == lead_id
    assert body["ai_output_id"]
    assert body["message"]

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    approved = [e for e in events if e.event_type == "outreach_approved"]
    assert len(approved) == 1
    assert approved[0].event_data["lead_id"] == lead_id
    assert approved[0].event_data["output_type"] == "outreach_email"
    assert approved[0].event_data["ai_output_id"] == body["ai_output_id"]


def test_approve_outreach_sets_lead_status(client: TestClient) -> None:
    lead_id = _scored_lead_with_outreach(client)
    client.post(f"/api/leads/{lead_id}/approve-outreach")
    lead = client.get(f"/api/leads/{lead_id}").json()
    assert lead["status"] == "outreach_approved"


def test_approve_outreach_400_if_no_outreach_exists(client: TestClient) -> None:
    lead_id = _scored_lead_without_outreach(client)
    response = client.post(f"/api/leads/{lead_id}/approve-outreach")
    assert response.status_code == 400
    assert "outreach" in response.json()["detail"].lower()


def test_approve_outreach_404_for_unknown_lead(client: TestClient) -> None:
    response = client.post(f"/api/leads/{ZERO_UUID}/approve-outreach")
    assert response.status_code == 404


# --------- reject ---------

def test_reject_outreach_creates_workflow_event_with_reason(
    client: TestClient, db_session: Session
) -> None:
    lead_id = _scored_lead_with_outreach(client)

    response = client.post(
        f"/api/leads/{lead_id}/reject-outreach",
        json={"reason": "Too generic"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["event_type"] == "outreach_rejected"

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    rejected = [e for e in events if e.event_type == "outreach_rejected"]
    assert len(rejected) == 1
    assert rejected[0].event_data["reason"] == "Too generic"
    assert rejected[0].event_data["lead_id"] == lead_id
    assert rejected[0].event_data["output_type"] == "outreach_email"


def test_reject_outreach_without_reason(
    client: TestClient, db_session: Session
) -> None:
    lead_id = _scored_lead_with_outreach(client)
    response = client.post(
        f"/api/leads/{lead_id}/reject-outreach", json={}
    )
    assert response.status_code == 200

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    rejected = [e for e in events if e.event_type == "outreach_rejected"]
    assert len(rejected) == 1
    assert rejected[0].event_data["reason"] is None


def test_reject_outreach_sets_lead_status(client: TestClient) -> None:
    lead_id = _scored_lead_with_outreach(client)
    client.post(f"/api/leads/{lead_id}/reject-outreach", json={})
    lead = client.get(f"/api/leads/{lead_id}").json()
    assert lead["status"] == "outreach_rejected"


def test_reject_outreach_400_if_no_outreach_exists(client: TestClient) -> None:
    lead_id = _scored_lead_without_outreach(client)
    response = client.post(
        f"/api/leads/{lead_id}/reject-outreach", json={"reason": "n/a"}
    )
    assert response.status_code == 400


def test_reject_outreach_404_for_unknown_lead(client: TestClient) -> None:
    response = client.post(
        f"/api/leads/{ZERO_UUID}/reject-outreach", json={"reason": "n/a"}
    )
    assert response.status_code == 404
