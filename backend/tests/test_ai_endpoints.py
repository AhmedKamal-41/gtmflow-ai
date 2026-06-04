from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIOutput, WorkflowEvent

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
    leads = client.get(f"/api/leads?batch_id={up['batch_id']}").json()
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
    assert "detected_pain_points" in content
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
    assert isinstance(content["personalization_points"], list)
    assert content["call_note"]

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
    body = response.json()
    assert len(body) == 2
    # outreach was created second -> appears first
    assert body[0]["output_type"] == "outreach_email"
    assert body[1]["output_type"] == "company_summary"


def test_list_ai_outputs_empty_when_lead_has_none(client: TestClient) -> None:
    lead_id = _upload_and_get_lead_id(client)
    response = client.get(f"/api/leads/{lead_id}/ai-outputs")
    assert response.status_code == 200
    assert response.json() == []


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


def test_summary_then_outreach_uses_persisted_summary(
    client: TestClient, db_session: Session
) -> None:
    """The outreach generator pulls the latest stored summary into its context."""
    lead_id = _upload_and_get_lead_id(client)
    summary_resp = client.post(f"/api/leads/{lead_id}/generate-summary").json()
    detected = summary_resp["content"]["detected_pain_points"]
    assert detected, "expected the mock summary to detect at least one pain term"

    outreach = client.post(f"/api/leads/{lead_id}/generate-outreach").json()
    call_note = outreach["content"]["call_note"].lower()
    # The latest stored summary's first pain term shows up in the outreach call note.
    assert detected[0].lower() in call_note
