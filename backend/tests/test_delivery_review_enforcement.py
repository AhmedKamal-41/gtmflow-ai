"""Phase 6: Slack delivery requires a current, applicable approval of the
exact draft. `force` bypasses only the Hot score threshold. A spy replaces
the transport, so a prohibited delivery provably never reaches it."""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import IntegrationPush, Lead, LeadBatch, WorkflowEvent
from tests.conftest import SYNTHETIC_SELLER_PROFILE, approve_current_draft, save_and_activate

pytestmark = pytest.mark.usefixtures("active_seller_profile")

CSV = (
    "company_name,industry,contact_title,contact_name,contact_email,website,company_size,source,notes\n"
    "Cascade Modular,Housing,VP Operations,Sarah Chen,sarah@cascade.com,cascade.com,240,referral,tenant maintenance leasing scheduling pain\n"
    "Vault Outfitters,Retail,COO,Jamie Russo,hello@vault.com,vault.com,560,csv,seasonal staff\n"
)


@pytest.fixture()
def transport(monkeypatch):
    calls = []

    def spy(url, payload):
        calls.append(payload)
        return "mock_success", "spy"

    monkeypatch.setattr("app.services.integration_push.send_slack_payload", spy)
    return calls


def scored(client: TestClient) -> tuple[str, dict, dict]:
    batch = client.post("/api/batches/upload", files={"file": ("l.csv", CSV.encode(), "text/csv")}).json()
    client.post(f"/api/batches/{batch['batch_id']}/score")
    leads = {l["company_name"]: l for l in client.get(f"/api/leads?batch_id={batch['batch_id']}").json()["items"]}
    return batch["batch_id"], leads["Cascade Modular"], leads["Vault Outfitters"]


def push(client, lead_id, force=False):
    return client.post(f"/api/leads/{lead_id}/push", json={"integration_type": "slack", "force": force})


def blocked_events(db_session):
    return [e.event_data for e in db_session.scalars(
        select(WorkflowEvent).where(WorkflowEvent.event_type == "lead_push_blocked"))]


@pytest.mark.parametrize("force", [False, True])
def test_no_draft_or_unreviewed_draft_never_reaches_transport(client, db_session, transport, force):
    _, hot, _ = scored(client)
    assert push(client, hot["id"], force).status_code == 409
    client.post(f"/api/leads/{hot['id']}/generate-outreach")
    response = push(client, hot["id"], force)
    assert response.status_code == 409
    assert "draft_not_reviewed" in response.json()["detail"]
    assert transport == []
    assert db_session.scalar(select(func.count()).select_from(IntegrationPush)) == 0
    assert blocked_events(db_session)[-1]["reason"] == "no_applicable_approval"


@pytest.mark.parametrize("force", [False, True])
def test_rejected_draft_never_reaches_transport(client, transport, force):
    _, hot, _ = scored(client)
    draft = client.post(f"/api/leads/{hot['id']}/generate-outreach").json()
    client.post(f"/api/leads/{hot['id']}/reject-outreach", json={
        "ai_output_id": draft["id"], "content_hash": draft["content_hash"], "reason": "Wrong angle"})
    response = push(client, hot["id"], force)
    assert response.status_code == 409 and "draft_rejected" in response.json()["detail"]
    assert transport == []


@pytest.mark.parametrize("force", [False, True])
def test_edited_or_regenerated_after_approval_never_reaches_transport(client, transport, force):
    _, hot, _ = scored(client)
    approved = approve_current_draft(client, hot["id"])
    content = deepcopy(approved["content"])
    content["email_body"] = "Hi Sarah,\n\nEdited after approval."
    edit = client.post(f"/api/leads/{hot['id']}/ai-outputs/{approved['id']}/revisions", json={
        "expected_content_hash": approved["content_hash"], "content": content})
    assert edit.status_code == 201
    assert push(client, hot["id"], force).status_code == 409

    approve_current_draft(client, hot["id"])  # approve a regenerated draft...
    client.post(f"/api/leads/{hot['id']}/generate-outreach")  # ...then regenerate
    assert push(client, hot["id"], force).status_code == 409
    assert transport == []


def test_seller_change_after_approval_blocks_delivery(client, transport):
    _, hot, _ = scored(client)
    approve_current_draft(client, hot["id"])
    profile = deepcopy(SYNTHETIC_SELLER_PROFILE)
    profile["value_proposition"] = "A different offer."
    save_and_activate(client, profile)
    response = push(client, hot["id"], force=True)
    assert response.status_code == 409
    assert "draft_not_from_active_seller_profile" in response.json()["detail"]
    assert transport == []


def test_blocked_status_and_incomplete_import_still_win_over_approval_and_force(client, db_session, transport):
    _, hot, _ = scored(client)
    approve_current_draft(client, hot["id"])
    lead = db_session.get(Lead, UUID(hot["id"]))
    lead.status = "do_not_contact"
    db_session.commit()
    assert push(client, hot["id"], force=True).status_code == 400

    lead.status = "new"
    db_session.get(LeadBatch, lead.batch_id).status = "partial"
    db_session.commit()
    assert push(client, hot["id"], force=True).status_code == 400
    assert transport == []


def test_force_bypasses_only_the_score_threshold(client, transport):
    _, hot, cold = scored(client)
    approve_current_draft(client, cold["id"])
    assert push(client, cold["id"], force=False).status_code == 400  # score threshold
    delivered = push(client, cold["id"], force=True)
    assert delivered.status_code == 200
    assert len(transport) == 1


def test_delivery_sends_exactly_the_approved_draft(client, db_session, transport):
    _, hot, _ = scored(client)
    approved = approve_current_draft(client, hot["id"])
    assert push(client, hot["id"]).status_code == 200
    assert approved["content"]["subject"] in transport[0]["text"]
    event = db_session.scalars(select(WorkflowEvent).where(WorkflowEvent.event_type == "lead_pushed")).one()
    assert event.event_data["approved_output_id"] == approved["id"]
    assert event.event_data["approved_content_hash"] == approved["content_hash"]


def test_batch_route_enforces_the_same_rules(client, db_session, transport):
    batch_id, hot, _ = scored(client)
    client.post(f"/api/leads/{hot['id']}/generate-outreach")  # unreviewed
    for force in (False, True):
        body = client.post(f"/api/batches/{batch_id}/push-hot",
                           json={"integration_type": "slack", "force": force}).json()
        assert body["pushed"] == 0 and body["blocked"] == 1
        assert "not overridable by force" in body["results"][0]["reason"]
    assert transport == []

    approve_current_draft(client, hot["id"])
    body = client.post(f"/api/batches/{batch_id}/push-hot", json={"integration_type": "slack"}).json()
    assert body["pushed"] == 1
    assert len(transport) == 1


def test_annotation_outputs_never_authorize_delivery(client, db_session, transport):
    """A training-annotation output for the same lead is not its draft."""
    from app.models import AIOutput
    from app.services import ai_generation

    _, hot, _ = scored(client)
    lead = db_session.get(Lead, UUID(hot["id"]))
    output = ai_generation._generate(
        db_session, lead, "outreach_email", "outreach_generated",
        ai_generation.resolve_active_seller(db_session), purpose="annotation",
    )
    db_session.commit()
    review = client.post(f"/api/leads/{hot['id']}/approve-outreach", json={
        "ai_output_id": str(output.id), "content_hash": client.get(
            f"/api/leads/{hot['id']}/ai-outputs?purpose=annotation").json()["items"][0]["content_hash"],
    })
    assert review.status_code == 400
    assert client.get(f"/api/leads/{hot['id']}/review-state").json()["status"] == "no_draft"
    assert client.get(f"/api/leads/{hot['id']}/latest-ai-output?output_type=outreach_email").status_code == 404
    assert push(client, hot["id"], force=True).status_code == 409
    assert transport == []
    assert db_session.get(AIOutput, output.id).purpose == "annotation"
