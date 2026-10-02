from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIOutputReview, WorkflowEvent
from tests.conftest import review_json

# These tests generate outreach, which needs an explicitly activated
# seller revision (Phase 5). The fixture activates a synthetic one.
pytestmark = pytest.mark.usefixtures("active_seller_profile")

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


def _scored_lead_with_outreach(client: TestClient) -> tuple[str, str]:
    """Returns (lead_id, ai_output_id) for a freshly-generated outreach draft."""
    up = _upload(client)
    client.post(f"/api/batches/{up['batch_id']}/score")
    lead = client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"][0]
    output = client.post(f"/api/leads/{lead['id']}/generate-outreach").json()
    return lead["id"], output["id"]


def _scored_lead_without_outreach(client: TestClient) -> str:
    up = _upload(client)
    client.post(f"/api/batches/{up['batch_id']}/score")
    return client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"][0]["id"]


# --------- approve ---------


def test_approve_outreach_creates_workflow_event_and_review(
    client: TestClient, db_session: Session
) -> None:
    lead_id, output_id = _scored_lead_with_outreach(client)

    response = client.post(
        f"/api/leads/{lead_id}/approve-outreach",
        json=review_json(client, lead_id, output_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["event_type"] == "outreach_approved"
    assert body["lead_id"] == lead_id
    assert body["ai_output_id"] == output_id
    assert body["review_id"]
    assert body["idempotent_replay"] is False
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
    assert approved[0].event_data["ai_output_id"] == output_id

    reviews = (
        db_session.execute(
            select(AIOutputReview).where(AIOutputReview.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    assert len(reviews) == 1
    assert reviews[0].decision == "approved"
    assert str(reviews[0].ai_output_id) == output_id
    assert reviews[0].reviewer_label == "user:test-operator"
    assert reviews[0].legacy_unlinked is False


def test_approve_outreach_sets_lead_status(client: TestClient) -> None:
    lead_id, output_id = _scored_lead_with_outreach(client)
    client.post(
        f"/api/leads/{lead_id}/approve-outreach", json=review_json(client, lead_id, output_id)
    )
    lead = client.get(f"/api/leads/{lead_id}").json()
    assert lead["status"] == "outreach_approved"


def test_approve_outreach_missing_ai_output_id_returns_422(client: TestClient) -> None:
    lead_id = _scored_lead_without_outreach(client)
    response = client.post(f"/api/leads/{lead_id}/approve-outreach", json={})
    assert response.status_code == 422
    body = response.json()
    assert any("ai_output_id" in str(err.get("loc", "")) for err in body["detail"])


def test_approve_outreach_unknown_output_id_returns_404(client: TestClient) -> None:
    lead_id = _scored_lead_without_outreach(client)
    response = client.post(
        f"/api/leads/{lead_id}/approve-outreach", json=review_json(client, lead_id, str(uuid4()))
    )
    assert response.status_code == 404


def test_approve_outreach_404_for_unknown_lead(client: TestClient) -> None:
    response = client.post(
        f"/api/leads/{ZERO_UUID}/approve-outreach",
        json=review_json(client, ZERO_UUID, str(uuid4())),
    )
    assert response.status_code == 404


# --------- reject ---------


def test_reject_outreach_creates_workflow_event_with_reason(
    client: TestClient, db_session: Session
) -> None:
    lead_id, output_id = _scored_lead_with_outreach(client)

    response = client.post(
        f"/api/leads/{lead_id}/reject-outreach",
        json=review_json(client, lead_id, output_id, reason="Too generic"),
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


def test_reject_outreach_requires_a_reason(client: TestClient, db_session: Session) -> None:
    """Phase 6: a rejection must say why. Missing or blank reasons are
    refused before anything is written."""
    lead_id, output_id = _scored_lead_with_outreach(client)
    for body in (review_json(client, lead_id, output_id), review_json(client, lead_id, output_id, reason="")):
        response = client.post(f"/api/leads/{lead_id}/reject-outreach", json=body)
        assert response.status_code == 422

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    assert [e for e in events if e.event_type == "outreach_rejected"] == []


def test_reject_outreach_sets_lead_status(client: TestClient) -> None:
    lead_id, output_id = _scored_lead_with_outreach(client)
    client.post(
        f"/api/leads/{lead_id}/reject-outreach", json=review_json(client, lead_id, output_id, reason="Not suitable (test rejection)")
    )
    lead = client.get(f"/api/leads/{lead_id}").json()
    assert lead["status"] == "outreach_rejected"


def test_reject_outreach_missing_ai_output_id_returns_422(client: TestClient) -> None:
    lead_id = _scored_lead_without_outreach(client)
    response = client.post(
        f"/api/leads/{lead_id}/reject-outreach", json={"reason": "n/a"}
    )
    assert response.status_code == 422


def test_reject_outreach_404_for_unknown_lead(client: TestClient) -> None:
    response = client.post(
        f"/api/leads/{ZERO_UUID}/reject-outreach",
        json=review_json(client, ZERO_UUID, str(uuid4()), reason="n/a"),
    )
    assert response.status_code == 404


# --------- Part D acceptance scenarios ---------


def test_review_rejects_output_belonging_to_another_lead(client: TestClient) -> None:
    lead_a_id, output_a_id = _scored_lead_with_outreach(client)
    lead_b_id, _ = _scored_lead_with_outreach(client)
    assert lead_a_id != lead_b_id

    response = client.post(
        f"/api/leads/{lead_b_id}/approve-outreach",
        json=review_json(client, lead_b_id, output_a_id),
    )
    assert response.status_code == 400
    assert "different lead" in response.json()["detail"].lower()


def test_review_rejects_non_outreach_output_type(client: TestClient) -> None:
    up = _upload(client)
    client.post(f"/api/batches/{up['batch_id']}/score")
    lead = client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"][0]
    summary = client.post(f"/api/leads/{lead['id']}/generate-summary").json()

    response = client.post(
        f"/api/leads/{lead['id']}/approve-outreach",
        json=review_json(client, lead['id'], summary["id"]),
    )
    assert response.status_code == 400
    assert "not outreach" in response.json()["detail"].lower()


def test_stale_tab_cannot_approve_superseded_draft(client: TestClient) -> None:
    """The Phase 1 audit's stale-tab bug (C.2), fixed: regenerating outreach
    after a tab has loaded draft v1 must make approving v1 fail loudly, not
    silently approve v2 instead."""
    lead_id, v1_id = _scored_lead_with_outreach(client)
    v2 = client.post(f"/api/leads/{lead_id}/generate-outreach").json()
    assert v2["id"] != v1_id

    stale_approve = client.post(
        f"/api/leads/{lead_id}/approve-outreach", json=review_json(client, lead_id, v1_id)
    )
    assert stale_approve.status_code == 409
    assert "superseded" in stale_approve.json()["detail"].lower()

    # The current draft can still be approved normally.
    current_approve = client.post(
        f"/api/leads/{lead_id}/approve-outreach", json=review_json(client, lead_id, v2["id"])
    )
    assert current_approve.status_code == 200
    assert current_approve.json()["ai_output_id"] == v2["id"]


def test_repeated_identical_approval_is_idempotent(
    client: TestClient, db_session: Session
) -> None:
    """Retrying the exact same approve request must not create a second
    review row, a second WorkflowEvent, or inflate approval_rate past 100%
    (docs/engineering-log/audit.md E.1)."""
    lead_id, output_id = _scored_lead_with_outreach(client)

    first = client.post(
        f"/api/leads/{lead_id}/approve-outreach", json=review_json(client, lead_id, output_id)
    )
    second = client.post(
        f"/api/leads/{lead_id}/approve-outreach", json=review_json(client, lead_id, output_id)
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    assert first.json()["review_id"] == second.json()["review_id"]

    reviews = (
        db_session.execute(
            select(AIOutputReview).where(AIOutputReview.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    assert len(reviews) == 1

    events = (
        db_session.execute(
            select(WorkflowEvent).where(
                WorkflowEvent.lead_id == UUID(lead_id),
                WorkflowEvent.event_type == "outreach_approved",
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1

    metrics = client.get("/api/metrics/dashboard").json()
    assert metrics["outreach_generated"] == 1
    assert metrics["outreach_approved"] == 1
    assert metrics["approval_rate"] == 100.0


def test_approve_then_reject_is_a_new_auditable_event_not_idempotent(
    client: TestClient, db_session: Session
) -> None:
    """A genuinely different decision on the same output is NOT collapsed by
    the idempotency check -- it's a deliberate later decision and must stay
    its own auditable event (Part D point 8)."""
    lead_id, output_id = _scored_lead_with_outreach(client)

    approve = client.post(
        f"/api/leads/{lead_id}/approve-outreach", json=review_json(client, lead_id, output_id)
    )
    reject = client.post(
        f"/api/leads/{lead_id}/reject-outreach", json=review_json(client, lead_id, output_id, reason="Not suitable (test rejection)")
    )
    assert approve.json()["idempotent_replay"] is False
    assert reject.json()["idempotent_replay"] is False
    assert approve.json()["review_id"] != reject.json()["review_id"]

    lead = client.get(f"/api/leads/{lead_id}").json()
    assert lead["status"] == "outreach_rejected"

    reviews = (
        db_session.execute(
            select(AIOutputReview)
            .where(AIOutputReview.lead_id == UUID(lead_id))
            .order_by(AIOutputReview.created_at)
        )
        .scalars()
        .all()
    )
    assert [r.decision for r in reviews] == ["approved", "rejected"]

    # Re-approving after the reject is, in turn, its own new event.
    re_approve = client.post(
        f"/api/leads/{lead_id}/approve-outreach", json=review_json(client, lead_id, output_id)
    )
    assert re_approve.json()["idempotent_replay"] is False
