"""Phase 11: approval metrics over one cohort (distinct operational outreach
drafts by their latest review) and consistent mock-versus-real breakdowns."""
from __future__ import annotations

from uuid import UUID

import pytest

from app.models import AIOutput, IntegrationPush
from app.models.ai_output import PURPOSE_ANNOTATION
from tests.conftest import approve_current_draft, review_json
from tests.test_grounded_generation import ScriptedClient, use_client
from tests.test_metrics import _lead_by, _scored_batch_setup

pytestmark = pytest.mark.usefixtures("active_seller_profile")


def _metrics(client) -> dict:
    return client.get("/api/metrics/dashboard").json()


def _draft(client, lead_id) -> dict:
    response = client.post(f"/api/leads/{lead_id}/generate-outreach")
    assert response.status_code == 200, response.text
    return response.json()


def _consistent(m: dict) -> None:
    """Every breakdown sums to its top-level total; rates stay within 0-100."""
    g, d = m["generation_by_mode"], m["delivery_by_mode"]
    assert sum(v["drafts_generated"] for v in g.values()) == m["outreach_generated"]
    assert sum(v["drafts_approved"] for v in g.values()) == m["outreach_approved"]
    assert sum(v["drafts_rejected"] for v in g.values()) == m["outreach_rejected"]
    assert m["outreach_approved"] + m["outreach_rejected"] + m["outreach_pending_review"] == m["outreach_generated"]
    assert sum(v["delivered"] for v in d.values()) == m["leads_pushed"]
    assert sum(v["failed"] for v in d.values()) == m["failed_push_count"]
    assert sum(v["outcome_unknown"] for v in d.values()) == m["push_unknown_count"]
    for rate in [m["approval_rate"], m["reviewed_approval_rate"], m["push_success_rate"],
                 *(v["approval_rate"] for v in g.values()), *(v["success_rate"] for v in d.values())]:
        assert 0.0 <= rate <= 100.0


def test_repeated_decisions_on_one_draft_count_it_once_by_its_latest_review(client):
    _, leads = _scored_batch_setup(client)
    lead = _lead_by(leads, "Cascade Modular")
    draft = _draft(client, lead["id"])
    body = review_json(client, lead["id"], draft["id"])
    for _ in range(3):  # approve, reject, approve, ... and an idempotent repeat
        client.post(f"/api/leads/{lead['id']}/approve-outreach", json=body)
        client.post(f"/api/leads/{lead['id']}/reject-outreach", json={**body, "reason": "second thoughts"})
    m = _metrics(client)
    assert (m["outreach_generated"], m["outreach_approved"], m["outreach_rejected"]) == (1, 0, 1)
    assert m["approval_rate"] == 0.0 and m["reviewed_approval_rate"] == 0.0
    client.post(f"/api/leads/{lead['id']}/approve-outreach", json=body)
    m = _metrics(client)
    assert (m["outreach_approved"], m["outreach_rejected"], m["approval_rate"]) == (1, 0, 100.0)
    assert m["approval_events"] == 4 and m["rejection_events"] == 3  # audit history untouched
    _consistent(m)


def test_a_new_draft_is_a_new_cohort_member_and_the_old_approval_stays_with_the_old_draft(client):
    _, leads = _scored_batch_setup(client)
    lead = _lead_by(leads, "Cascade Modular")
    approve_current_draft(client, lead["id"])
    _draft(client, lead["id"])  # regenerated: pending
    m = _metrics(client)
    assert (m["outreach_generated"], m["outreach_approved"], m["outreach_pending_review"]) == (2, 1, 1)
    assert m["approval_rate"] == 50.0 and m["reviewed_approval_rate"] == 100.0
    _consistent(m)


def test_human_revisions_are_drafts_that_inherit_the_mode_and_annotation_outputs_are_excluded(client, db_session):
    _, leads = _scored_batch_setup(client)
    lead = _lead_by(leads, "Cascade Modular")
    draft = _draft(client, lead["id"])
    fixed = dict(draft["content"], subject="Edited subject")
    revision = client.post(f"/api/leads/{lead['id']}/ai-outputs/{draft['id']}/revisions",
                           json={"expected_content_hash": draft["content_hash"], "content": fixed}).json()
    client.post(f"/api/leads/{lead['id']}/approve-outreach",
                json={"ai_output_id": revision["id"], "content_hash": revision["content_hash"]})
    db_session.add(AIOutput(lead_id=UUID(lead["id"]), output_type="outreach_email", content=fixed,
                            model_used="mock", purpose=PURPOSE_ANNOTATION))
    db_session.commit()
    m = _metrics(client)
    assert (m["outreach_generated"], m["outreach_approved"]) == (2, 1)  # original + revision; annotation excluded
    assert m["generation_by_mode"]["mock"]["drafts_generated"] == 2 and m["generation_by_mode"]["real"]["drafts_generated"] == 0
    _consistent(m)


def test_real_and_mock_generation_and_delivery_are_reported_separately(client, db_session, monkeypatch):
    _, leads = _scored_batch_setup(client)
    mock_lead, real_lead = _lead_by(leads, "Cascade Modular"), _lead_by(leads, "Northbridge Clinics")
    approve_current_draft(client, mock_lead["id"])
    client.post(f"/api/leads/{mock_lead['id']}/push", json={"integration_type": "slack"})  # mock webhook

    class RealModel(ScriptedClient):
        name = "qwen3-4b-lora-v1"

    use_client(monkeypatch, RealModel())
    real_draft = _draft(client, real_lead["id"])
    client.post(f"/api/leads/{real_lead['id']}/approve-outreach", json=review_json(client, real_lead["id"], real_draft["id"]))
    # Delivery rows in the three states a real webhook can leave (written
    # directly: this test is about the arithmetic, not the transport).
    for status in ("success", "failed", "unknown"):
        db_session.add(IntegrationPush(lead_id=UUID(real_lead["id"]), integration_type="slack", payload={},
                                       status=status, delivery_mode="real"))
    # A pre-Phase-11 row: no delivery_mode recorded.
    db_session.add(IntegrationPush(lead_id=UUID(real_lead["id"]), integration_type="slack", payload={}, status="failed"))
    db_session.commit()

    m = _metrics(client)
    g, d = m["generation_by_mode"], m["delivery_by_mode"]
    assert (g["mock"]["drafts_generated"], g["mock"]["drafts_approved"]) == (1, 1)
    assert (g["real"]["drafts_generated"], g["real"]["drafts_approved"], g["real"]["approval_rate"]) == (1, 1, 100.0)
    assert (d["mock"]["attempts"], d["mock"]["delivered"]) == (1, 1)
    assert (d["real"]["attempts"], d["real"]["delivered"], d["real"]["failed"], d["real"]["outcome_unknown"]) == (3, 1, 1, 1)
    assert d["real"]["success_rate"] == 33.33
    assert (d["unknown"]["attempts"], d["unknown"]["failed"]) == (1, 1)  # legacy failure: mode undeterminable
    assert m["real_messages_delivered"] == 1 and m["mock_messages_delivered"] == 1
    assert m["push_unknown_count"] == 1 and m["data_mode"] == "mixed"
    _consistent(m)


def test_an_all_mock_database_says_so(client):
    _, leads = _scored_batch_setup(client)
    lead = _lead_by(leads, "Cascade Modular")
    approve_current_draft(client, lead["id"])
    client.post(f"/api/leads/{lead['id']}/push", json={"integration_type": "slack"})
    m = _metrics(client)
    assert m["data_mode"] == "mock_only" and m["real_messages_delivered"] == 0 and m["mock_messages_delivered"] == 1
    _consistent(m)
