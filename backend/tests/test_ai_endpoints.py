from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIOutput, WorkflowEvent

# Outreach needs an explicitly activated seller revision (Phase 5).
pytestmark = pytest.mark.usefixtures("active_seller_profile")

ZERO_UUID = "00000000-0000-0000-0000-000000000000"

VALID_CSV = (
    "company_name,industry,contact_title,contact_name,contact_email,website,company_size,source,notes\n"
    "Cascade Modular,Housing,VP Operations,Sarah Chen,sarah@cascade.com,cascade.com,240,referral,tenant maintenance and leasing scheduling pain\n"
)


def _upload_and_get_lead_id(client: TestClient) -> str:
    files = {"file": ("leads.csv", VALID_CSV.encode("utf-8"), "text/csv")}
    up = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "ai-test"}
    ).json()
    leads = client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"]
    assert leads, "expected at least one lead"
    return leads[0]["id"]


def test_post_generate_summary_creates_ai_output(
    client: TestClient, db_session: Session
) -> None:
    lead_id = _upload_and_get_lead_id(client)
    response = client.post(f"/api/leads/{lead_id}/generate-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["lead_id"] == lead_id
    assert body["output_type"] == "company_summary"
    assert body["model_used"] == "mock"
    content = body["content"]
    assert content["company_summary"]
    assert {"evidence", "unknowns", "hypotheses"} <= set(content)
    assert content["confidence"] in {"low", "medium", "high"}

    rows = (
        db_session.execute(
            select(AIOutput).where(AIOutput.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].output_type == "company_summary"


def test_post_generate_outreach_creates_ai_output(
    client: TestClient, db_session: Session
) -> None:
    lead_id = _upload_and_get_lead_id(client)
    response = client.post(f"/api/leads/{lead_id}/generate-outreach")
    assert response.status_code == 200
    body = response.json()
    assert body["output_type"] == "outreach_email"
    content = body["content"]
    assert content["subject"]
    assert content["email_body"]
    assert content["lead_facts_used"]
    assert content["call_note"]
    # The configured seller replaces the old hardcoded GTMFlow pitch.
    assert "Synthetic Seller Co" in content["email_body"]
    assert "GTMFlow" not in content["email_body"]

    rows = (
        db_session.execute(
            select(AIOutput).where(AIOutput.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].output_type == "outreach_email"


def test_workflow_event_ai_summary_generated_is_recorded(
    client: TestClient, db_session: Session
) -> None:
    lead_id = _upload_and_get_lead_id(client)
    client.post(f"/api/leads/{lead_id}/generate-summary")

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    matches = [e for e in events if e.event_type == "ai_summary_generated"]
    assert len(matches) == 1
    assert matches[0].event_data["output_type"] == "company_summary"


def test_workflow_event_outreach_generated_is_recorded(
    client: TestClient, db_session: Session
) -> None:
    lead_id = _upload_and_get_lead_id(client)
    client.post(f"/api/leads/{lead_id}/generate-outreach")

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    matches = [e for e in events if e.event_type == "outreach_generated"]
    assert len(matches) == 1


def test_list_ai_outputs_returns_newest_first(client: TestClient) -> None:
    lead_id = _upload_and_get_lead_id(client)
    client.post(f"/api/leads/{lead_id}/generate-summary")
    client.post(f"/api/leads/{lead_id}/generate-outreach")

    response = client.get(f"/api/leads/{lead_id}/ai-outputs")
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2
    body = page["items"]
    assert len(body) == 2
    # outreach was created second -> appears first
    assert body[0]["output_type"] == "outreach_email"
    assert body[1]["output_type"] == "company_summary"


def test_list_ai_outputs_empty_when_lead_has_none(client: TestClient) -> None:
    lead_id = _upload_and_get_lead_id(client)
    response = client.get(f"/api/leads/{lead_id}/ai-outputs")
    assert response.status_code == 200
    page = response.json()
    assert page["items"] == []
    assert page["total"] == 0


def test_latest_ai_output_returns_match(client: TestClient) -> None:
    lead_id = _upload_and_get_lead_id(client)
    client.post(f"/api/leads/{lead_id}/generate-summary")
    response = client.get(
        f"/api/leads/{lead_id}/latest-ai-output?output_type=company_summary"
    )
    assert response.status_code == 200
    assert response.json()["output_type"] == "company_summary"


def test_latest_ai_output_404_when_missing_type(client: TestClient) -> None:
    lead_id = _upload_and_get_lead_id(client)
    # generate a summary -- but ask for outreach
    client.post(f"/api/leads/{lead_id}/generate-summary")
    response = client.get(
        f"/api/leads/{lead_id}/latest-ai-output?output_type=outreach_email"
    )
    assert response.status_code == 404


def test_unknown_lead_returns_404_on_all_ai_endpoints(client: TestClient) -> None:
    assert (
        client.post(f"/api/leads/{ZERO_UUID}/generate-summary").status_code == 404
    )
    assert (
        client.post(f"/api/leads/{ZERO_UUID}/generate-outreach").status_code == 404
    )
    assert client.get(f"/api/leads/{ZERO_UUID}/ai-outputs").status_code == 404
    assert (
        client.get(
            f"/api/leads/{ZERO_UUID}/latest-ai-output?output_type=company_summary"
        ).status_code
        == 404
    )


def test_stored_summary_is_not_used_as_outreach_evidence(
    client: TestClient, db_session: Session
) -> None:
    """Phase 5 reverses the v1 behavior: a stored summary is an earlier
    generation's output, not evidence, so it never enters the outreach
    context. Generating a summary first leaves the outreach input unchanged."""
    lead_id = _upload_and_get_lead_id(client)
    before = client.post(f"/api/leads/{lead_id}/generate-outreach").json()
    client.post(f"/api/leads/{lead_id}/generate-summary")
    after = client.post(f"/api/leads/{lead_id}/generate-outreach").json()

    assert "latest_summary" not in after["input_snapshot"]
    assert after["input_hash"] == before["input_hash"]
