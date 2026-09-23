"""Phase 6: exact-draft review, human revisions, and approval applicability."""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AIOutput, AIOutputReview, WorkflowEvent
from tests.conftest import SYNTHETIC_SELLER_PROFILE, review_json, save_and_activate

pytestmark = pytest.mark.usefixtures("active_seller_profile")

CSV = (
    b"company_name,industry,contact_title,contact_name,contact_email,website,company_size\n"
    b"Cascade Modular,Housing,VP Operations,Sarah Chen,sarah@cascade.com,cascade.com,240\n"
    b"Northbridge Clinics,Healthcare,Practice Manager,Anika Rao,anika@northbridge.com,northbridge.com,1000+\n"
)


def leads(client: TestClient) -> list[dict]:
    batch = client.post("/api/batches/upload", files={"file": ("l.csv", CSV, "text/csv")}).json()
    return client.get(f"/api/leads?batch_id={batch['batch_id']}").json()["items"]


def draft(client: TestClient, lead_id: str) -> dict:
    response = client.post(f"/api/leads/{lead_id}/generate-outreach")
    assert response.status_code == 200, response.text
    return response.json()


def approve(client: TestClient, lead_id: str, output: dict, **override):
    body = {"ai_output_id": output["id"], "content_hash": output["content_hash"], **override}
    return client.post(f"/api/leads/{lead_id}/approve-outreach", json=body)


def reject(client: TestClient, lead_id: str, output: dict, reason: str = "Too generic"):
    return client.post(f"/api/leads/{lead_id}/reject-outreach", json={
        "ai_output_id": output["id"], "content_hash": output["content_hash"], "reason": reason,
    })


def state(client: TestClient, lead_id: str) -> dict:
    return client.get(f"/api/leads/{lead_id}/review-state").json()


def edited(output: dict, body: str) -> dict:
    content = deepcopy(output["content"])
    content["email_body"] = body
    return content


def revise(client: TestClient, lead_id: str, output: dict, content: dict):
    return client.post(f"/api/leads/{lead_id}/ai-outputs/{output['id']}/revisions", json={
        "expected_content_hash": output["content_hash"], "content": content,
    })


def count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


# ------------------------------------------------ targeting

def test_review_must_name_the_exact_content_displayed(client, db_session):
    lead = leads(client)[0]
    output = draft(client, lead["id"])
    wrong = approve(client, lead["id"], output, content_hash="f" * 64)
    assert wrong.status_code == 409
    assert "does not match" in wrong.json()["detail"]
    missing = client.post(f"/api/leads/{lead['id']}/approve-outreach", json={"ai_output_id": output["id"]})
    assert missing.status_code == 422
    assert count(db_session, AIOutputReview) == 0


def test_wrong_lead_and_wrong_output_are_refused(client, db_session):
    a, b = leads(client)
    output_a = draft(client, a["id"])
    summary_a = client.post(f"/api/leads/{a['id']}/generate-summary").json()

    assert approve(client, b["id"], output_a).status_code == 400
    assert approve(client, a["id"], summary_a).status_code == 400
    unknown = approve(client, a["id"], {"id": str(uuid4()), "content_hash": "0" * 64})
    assert unknown.status_code == 404
    assert count(db_session, AIOutputReview) == 0


def test_posted_reviewer_identity_is_rejected(client, db_session):
    lead = leads(client)[0]
    output = draft(client, lead["id"])
    response = approve(client, lead["id"], output, reviewer_label="Jane Authenticated")
    assert response.status_code == 422
    assert approve(client, lead["id"], output).status_code == 200
    review = db_session.scalars(select(AIOutputReview)).one()
    assert review.reviewer_label == "local-demo-unauthenticated"
    assert review.content_hash == output["content_hash"]


def test_superseded_draft_cannot_be_reviewed_from_a_stale_tab(client):
    lead = leads(client)[0]
    first = draft(client, lead["id"])
    second = draft(client, lead["id"])
    assert approve(client, lead["id"], first).status_code == 409
    assert reject(client, lead["id"], first).status_code == 409
    assert approve(client, lead["id"], second).status_code == 200
    listed = {o["id"]: o["review_status"] for o in client.get(f"/api/leads/{lead['id']}/ai-outputs").json()["items"]}
    assert listed[first["id"]] == "superseded"
    assert listed[second["id"]] == "approved"


# ------------------------------------------------ states and idempotency

def test_states_move_pending_to_approved_to_rejected(client, db_session):
    lead = leads(client)[0]
    assert state(client, lead["id"])["status"] == "no_draft"
    output = draft(client, lead["id"])
    assert state(client, lead["id"])["status"] == "pending"

    first = approve(client, lead["id"], output)
    retry = approve(client, lead["id"], output)
    assert first.json()["idempotent_replay"] is False
    assert retry.json()["idempotent_replay"] is True
    assert retry.json()["review_id"] == first.json()["review_id"]
    current = state(client, lead["id"])
    assert current["status"] == "approved" and current["approval_applicable"] is True

    changed = reject(client, lead["id"], output, reason="Changed my mind")
    assert changed.json()["idempotent_replay"] is False
    current = state(client, lead["id"])
    assert current["status"] == "rejected"
    assert current["approval_applicable"] is False
    assert current["latest_review"]["reason"] == "Changed my mind"
    events = [e.event_type for e in db_session.scalars(select(WorkflowEvent).order_by(WorkflowEvent.created_at))]
    assert events.count("outreach_approved") == 1 and events.count("outreach_rejected") == 1
    history = client.get(f"/api/leads/{lead['id']}/reviews").json()
    assert [r["decision"] for r in history["items"]] == ["rejected", "approved"]


def test_rejection_requires_a_reason(client):
    lead = leads(client)[0]
    output = draft(client, lead["id"])
    assert reject(client, lead["id"], output, reason="").status_code == 422


# ------------------------------------------------ human revisions

def test_edit_creates_an_immutable_revision_that_needs_its_own_review(client, db_session):
    lead = leads(client)[0]
    original = draft(client, lead["id"])
    assert approve(client, lead["id"], original).status_code == 200

    response = revise(client, lead["id"], original, edited(original, "Hi Sarah,\n\nA shorter, human-edited note."))
    assert response.status_code == 201, response.text
    revision = response.json()
    assert revision["origin"] == "human_edited"
    assert revision["parent_output_id"] == original["id"]
    assert revision["author_label"] == "local-demo-unauthenticated"
    assert revision["model_used"] == "human_edit"
    assert revision["input_hash"] == original["input_hash"]
    assert revision["seller_profile_content_hash"] == original["seller_profile_content_hash"]
    assert revision["content_hash"] != original["content_hash"]

    # The model response is untouched; the old approval stays in history
    # but authorizes nothing, and the revision is pending.
    db_session.expire_all()
    stored = db_session.get(AIOutput, UUID(original["id"]))
    assert stored.content == original["content"]
    current = state(client, lead["id"])
    assert current["draft_id"] == revision["id"]
    assert current["status"] == "pending"
    assert current["approval_applicable"] is False
    assert approve(client, lead["id"], original).status_code == 409
    assert approve(client, lead["id"], revision).status_code == 200
    assert state(client, lead["id"])["approval_applicable"] is True


def test_identical_edit_retry_is_idempotent_and_stale_edit_is_refused(client, db_session):
    lead = leads(client)[0]
    original = draft(client, lead["id"])
    content = edited(original, "Hi Sarah,\n\nEdited once.")
    first = revise(client, lead["id"], original, content)
    retry = revise(client, lead["id"], original, content)
    assert first.status_code == 201 and retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    # Editing the superseded original again (a stale tab) is refused.
    other = revise(client, lead["id"], original, edited(original, "Hi Sarah,\n\nA different edit."))
    assert other.status_code == 409
    assert count(db_session, AIOutput, AIOutput.origin == "human_edited") == 1


@pytest.mark.parametrize("mutate, status", [
    (lambda c: c.update(email_body=c["email_body"] + " We cut costs by 45%."), 422),
    (lambda c: c.update(claims_used=["claim-9"]), 422),
    (lambda c: c.update(approved=True), 422),
    (lambda c: None, 400),
])
def test_invalid_or_empty_edits_save_nothing(client, db_session, mutate, status):
    lead = leads(client)[0]
    original = draft(client, lead["id"])
    content = deepcopy(original["content"])
    mutate(content)
    response = revise(client, lead["id"], original, content)
    assert response.status_code == status
    assert count(db_session, AIOutput, AIOutput.origin == "human_edited") == 0


def test_edit_with_a_stale_expected_hash_is_refused(client):
    lead = leads(client)[0]
    original = draft(client, lead["id"])
    stale = dict(original, content_hash="a" * 64)
    assert revise(client, lead["id"], stale, edited(original, "x")).status_code == 409


# ------------------------------------------------ approval applicability

def test_regeneration_after_approval_requires_a_fresh_review(client):
    lead = leads(client)[0]
    first = draft(client, lead["id"])
    approve(client, lead["id"], first)
    draft(client, lead["id"])
    current = state(client, lead["id"])
    assert current["status"] == "pending"
    assert "draft_not_reviewed" in current["delivery_blockers"]


def test_seller_change_invalidates_an_approval(client):
    lead = leads(client)[0]
    output = draft(client, lead["id"])
    approve(client, lead["id"], output)
    assert state(client, lead["id"])["approval_applicable"] is True

    profile = deepcopy(SYNTHETIC_SELLER_PROFILE)
    profile["product_name"] = "A revised offer"
    save_and_activate(client, profile)
    current = state(client, lead["id"])
    assert current["status"] == "approved"  # the decision is still on record
    assert current["approval_applicable"] is False
    assert current["delivery_blockers"] == ["draft_not_from_active_seller_profile"]


def test_company_input_change_invalidates_an_approval(client, db_session):
    from app.models import Lead

    lead = leads(client)[0]
    output = draft(client, lead["id"])
    approve(client, lead["id"], output)
    row = db_session.get(Lead, UUID(lead["id"]))
    row.company_size = "5000+"
    db_session.commit()
    current = state(client, lead["id"])
    assert current["approval_applicable"] is False
    assert "draft_inputs_changed_since_generation" in current["delivery_blockers"]
    # Readiness reports the same blocker the delivery path enforces.
    gaps = client.get(f"/api/leads/{lead['id']}/readiness").json()["readiness"]["internal_slack_handoff"]["gaps"]
    assert "draft_inputs_changed_since_generation" in gaps


def test_fit_rescoring_with_new_values_invalidates_but_repeat_does_not(client):
    lead = leads(client)[0]
    output = draft(client, lead["id"])
    approve(client, lead["id"], output)
    client.post(f"/api/leads/{lead['id']}/fit-score")
    assert "draft_inputs_changed_since_generation" in state(client, lead["id"])["delivery_blockers"]

    fresh = draft(client, lead["id"])
    approve(client, lead["id"], fresh)
    client.post(f"/api/leads/{lead['id']}/fit-score")  # same values, new row
    assert state(client, lead["id"])["approval_applicable"] is True


def test_legacy_approval_without_content_identity_authorizes_nothing(client, db_session):
    lead = leads(client)[0]
    output = draft(client, lead["id"])
    db_session.add(AIOutputReview(
        lead_id=UUID(lead["id"]), ai_output_id=UUID(output["id"]), decision="approved",
        reviewer_label="local-demo-unauthenticated", content_hash=None,
    ))
    db_session.commit()
    current = state(client, lead["id"])
    assert current["status"] == "approved"
    assert current["delivery_blockers"] == ["approval_does_not_identify_content"]


def test_review_state_shows_source_freshness_for_uploaded_leads(client):
    lead = leads(client)[0]
    source = state(client, lead["id"])["source"]
    assert source["batch_source"] == "csv"
    assert "not verified" in source["freshness_note"]
