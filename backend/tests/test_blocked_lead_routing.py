"""Phase 2 Part A: blocked leads (do_not_contact / disqualified / unsubscribed)
must never reach Slack, regardless of score, the ``force`` flag, or whether
the push goes through the single-lead or batch route.

See docs/engineering-log/audit.md finding D.2 for the bug this fixes: a
``do_not_contact`` lead that scored Hot was previously pushed successfully
with ``force=false`` -- nothing checked ``lead.status`` at all.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IntegrationPush, WorkflowEvent
from app.scoring.lead_scoring import DISQUALIFIED_STATUSES
from tests.conftest import approve_current_draft

# Same shape as the Hot-scoring row used in test_push_endpoints.py, plus a
# blocked `status` column (a real CSV column the ingestion pipeline already
# supports -- see app/services/csv_ingestion.py KNOWN_COLUMNS).
BLOCKED_HOT_CSV = (
    "company_name,industry,contact_title,contact_name,contact_email,website,"
    "company_size,source,status,notes\n"
    "Blocked Prop Co,Housing,VP Operations,Sam Lee,sam@blockedprop.com,"
    "blockedprop.com,240,referral,{status},tenant maintenance leasing scheduling pain\n"
)


def _transport_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stub httpx.post at the transport layer; return the list of calls made.

    Asserting this list stays empty proves no network send was attempted --
    stronger evidence than checking the HTTP response alone.
    """
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, json: dict, timeout: float) -> Any:
        calls.append({"url": url, "json": json, "timeout": timeout})
        raise AssertionError("Slack transport must not be invoked for a blocked lead")

    monkeypatch.setattr("app.integrations.slack.httpx.post", fake_post)
    return calls


def _upload_blocked_hot_lead(client: TestClient, status: str) -> str:
    csv_text = BLOCKED_HOT_CSV.format(status=status)
    files = {"file": ("leads.csv", csv_text.encode("utf-8"), "text/csv")}
    up = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "blocked-test"}
    ).json()
    client.post(f"/api/batches/{up['batch_id']}/score")
    leads = client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"]
    lead = leads[0]
    assert lead["status"] == status, "CSV status column must round-trip onto Lead.status"
    return up["batch_id"], lead["id"]


@pytest.mark.parametrize("status", sorted(DISQUALIFIED_STATUSES))
def test_blocked_status_scores_hot_but_is_blocked(
    client: TestClient, status: str
) -> None:
    """Precondition check: confirms the scenario is real, not hypothetical --
    the lead genuinely reaches the Hot band despite the blocked status
    (Part A must not rely on the score also being low)."""
    _, lead_id = _upload_blocked_hot_lead(client, status)
    score = client.get(f"/api/leads/{lead_id}/score").json()
    assert score["priority"] == "Hot", (
        "test fixture must score Hot so the blocked-status check, not the "
        "Hot/force gate, is what's actually being exercised"
    )


# --------------------------------------------------------------------------
# Single-lead push
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(DISQUALIFIED_STATUSES))
@pytest.mark.parametrize("force", [False, True])
def test_single_push_blocked_status_rejected_regardless_of_force(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    force: bool,
) -> None:
    calls = _transport_spy(monkeypatch)
    _, lead_id = _upload_blocked_hot_lead(client, status)

    response = client.post(
        f"/api/leads/{lead_id}/push",
        json={"integration_type": "slack", "force": force},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert status in detail
    assert "force" in detail.lower()
    assert calls == [], "no Slack send should have been attempted"

    # No IntegrationPush row was created for this blocked attempt.
    rows = (
        db_session.execute(
            select(IntegrationPush).where(IntegrationPush.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    assert rows == []

    # A truthful audit event was recorded -- distinct from "lead_pushed".
    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    blocked_events = [e for e in events if e.event_type == "lead_push_blocked"]
    assert len(blocked_events) == 1
    assert blocked_events[0].event_data["lead_status"] == status
    assert not any(e.event_type == "lead_pushed" for e in events)

    # Lead.status was not overwritten to "pushed".
    lead_detail = client.get(f"/api/leads/{lead_id}").json()
    assert lead_detail["status"] == status


# --------------------------------------------------------------------------
# Batch push-hot
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(DISQUALIFIED_STATUSES))
@pytest.mark.parametrize("force", [False, True])
def test_batch_push_blocked_status_distinguished_from_pushed(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    force: bool,
) -> None:
    calls = _transport_spy(monkeypatch)
    batch_id, lead_id = _upload_blocked_hot_lead(client, status)

    response = client.post(
        f"/api/batches/{batch_id}/push-hot",
        json={"integration_type": "slack", "force": force},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["hot_leads_found"] == 1
    assert body["pushed"] == 0
    assert body["failed"] == 0
    assert body["blocked"] == 1
    assert calls == []

    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["lead_id"] == lead_id
    assert result["status"] == "blocked"
    assert status in result["reason"]
    assert result["push_id"] is None

    rows = (
        db_session.execute(
            select(IntegrationPush).where(IntegrationPush.lead_id == UUID(lead_id))
        )
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.usefixtures("active_seller_profile")
def test_batch_push_mixes_blocked_and_deliverable_leads(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One blocked lead and one clean Hot lead in the same batch: the clean
    lead is still delivered, the blocked one is skipped with a reason, and
    the transport spy proves exactly one send happened (for the clean lead)."""
    calls = _transport_spy_allow_one(monkeypatch)

    csv_text = (
        "company_name,industry,contact_title,contact_name,contact_email,website,"
        "company_size,source,status,notes\n"
        "Blocked Prop Co,Housing,VP Operations,Sam Lee,sam@blockedprop.com,"
        "blockedprop.com,240,referral,do_not_contact,tenant maintenance leasing scheduling pain\n"
        "Clean Prop Co,Housing,VP Operations,Robin Diaz,robin@cleanprop.com,"
        "cleanprop.com,240,referral,,tenant maintenance leasing scheduling pain\n"
    )
    files = {"file": ("leads.csv", csv_text.encode("utf-8"), "text/csv")}
    up = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "mixed-blocked"}
    ).json()
    client.post(f"/api/batches/{up['batch_id']}/score")
    # Delivery needs a current approval of the exact draft (Phase 6); the
    # blocked lead is approved too, to prove approval can't unblock it.
    for lead in client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"]:
        approve_current_draft(client, lead["id"])

    response = client.post(
        f"/api/batches/{up['batch_id']}/push-hot",
        json={"integration_type": "slack"},
    )
    body = response.json()
    assert body["hot_leads_found"] == 2
    assert body["pushed"] == 1
    assert body["blocked"] == 1
    statuses = {r["status"] for r in body["results"]}
    assert statuses == {"blocked", "mock_success"}
    # Mock mode never calls httpx at all, so the spy should see nothing.
    assert calls == []


def _transport_spy_allow_one(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Same as _transport_spy but for the mixed test, where mock mode means
    httpx.post is never called for either lead -- SLACK_WEBHOOK_URL is unset
    in tests, so both the blocked and the clean lead resolve via the mock
    path, not a real send. Kept separate so a raise-on-call spy can't
    accidentally fail the "clean lead" half of this test."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.integrations.slack.httpx.post",
        lambda *a, **kw: calls.append({"args": a, "kwargs": kw}),
    )
    return calls
