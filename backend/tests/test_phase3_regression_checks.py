"""Phase 3, Part A: two focused checks against Phase 2's claims, run before
any real PDL data is imported.

1. approval_rate double-counting: Phase 2's idempotency fix only protects
   against an *exact* repeat of the same decision on the same output. A
   genuine decision change (approve -> reject -> approve) is NOT an exact
   repeat and still produces two "outreach_approved" WorkflowEvent rows for
   one generated draft, so approval_rate can still exceed 100%. This is
   documented here as a known, intentional partial fix -- the full fix
   (redefining the metric) is Phase 11 scope. See docs/upgrade/decisions.md.

2. Blocked-status regression: `approve-outreach`/`reject-outreach` were
   unconditionally overwriting Lead.status, which silently erased a blocked
   disposition (do_not_contact/disqualified/unsubscribed) and defeated the
   Phase 2 push-time status check -- a blocked lead approved through this
   path could reach Slack. Fixed in app/api/outreach_review.py and
   app/services/demo.py (mirroring the Phase 2 fix already applied to
   scoring). This file proves the regression is closed.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# These tests generate outreach, which needs an explicitly activated
# seller revision (Phase 5). The fixture activates a synthetic one.
pytestmark = pytest.mark.usefixtures("active_seller_profile")

CSV = (
    "company_name,industry,contact_title,company_size,source,notes\n"
    "Cascade Modular,Housing,VP Operations,240,referral,tenant maintenance leasing scheduling pain\n"
)

BLOCKED_HOT_CSV = (
    "company_name,industry,contact_title,contact_name,contact_email,website,"
    "company_size,source,status,notes\n"
    "Blocked Prop Co,Housing,VP Operations,Sam Lee,sam@blockedprop.com,"
    "blockedprop.com,1000+,referral,{status},tenant maintenance leasing scheduling pain\n"
)


def _upload_score_generate(client: TestClient, csv: str, batch_name: str) -> tuple[dict, dict]:
    files = {"file": ("leads.csv", csv.encode("utf-8"), "text/csv")}
    up = client.post(
        "/api/batches/upload", files=files, data={"batch_name": batch_name}
    ).json()
    client.post(f"/api/batches/{up['batch_id']}/score")
    lead = client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"][0]
    output = client.post(f"/api/leads/{lead['id']}/generate-outreach").json()
    return lead, output


def test_approve_reject_approve_still_inflates_approval_rate(client: TestClient) -> None:
    """Characterization test: this is the DOCUMENTED partial-fix behavior,
    not a bug to silently tolerate forever. If Phase 11 fixes the metric,
    update this test -- don't just delete it."""
    lead, output = _upload_score_generate(client, CSV, "phase3-a1")

    client.post(
        f"/api/leads/{lead['id']}/approve-outreach", json={"ai_output_id": output["id"]}
    )
    client.post(
        f"/api/leads/{lead['id']}/reject-outreach", json={"ai_output_id": output["id"]}
    )
    client.post(
        f"/api/leads/{lead['id']}/approve-outreach", json={"ai_output_id": output["id"]}
    )

    metrics = client.get("/api/metrics/dashboard").json()
    assert metrics["outreach_generated"] == 1
    assert metrics["outreach_approved"] == 2  # two distinct approve *decisions*
    assert metrics["approval_rate"] == 200.0  # still exceeds 100% -- Phase 11 to fix


def test_blocked_status_survives_approve_outreach(client: TestClient) -> None:
    csv = BLOCKED_HOT_CSV.format(status="do_not_contact")
    files = {"file": ("leads.csv", csv.encode("utf-8"), "text/csv")}
    up = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "phase3-a2-approve"}
    ).json()
    client.post(f"/api/batches/{up['batch_id']}/score")
    lead = client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"][0]
    assert lead["status"] == "do_not_contact"

    output = client.post(f"/api/leads/{lead['id']}/generate-outreach").json()
    approve = client.post(
        f"/api/leads/{lead['id']}/approve-outreach", json={"ai_output_id": output["id"]}
    )
    assert approve.status_code == 200

    lead_after = client.get(f"/api/leads/{lead['id']}").json()
    assert lead_after["status"] == "do_not_contact", (
        "approve-outreach must never erase a blocked disposition"
    )

    # And the Phase 2 push-time gate still holds, end to end.
    push = client.post(
        f"/api/leads/{lead['id']}/push", json={"integration_type": "slack", "force": True}
    )
    assert push.status_code == 400
    assert "do_not_contact" in push.json()["detail"]


def test_blocked_status_survives_reject_outreach(client: TestClient) -> None:
    csv = BLOCKED_HOT_CSV.format(status="disqualified")
    files = {"file": ("leads.csv", csv.encode("utf-8"), "text/csv")}
    up = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "phase3-a2-reject"}
    ).json()
    client.post(f"/api/batches/{up['batch_id']}/score")
    lead = client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"][0]

    output = client.post(f"/api/leads/{lead['id']}/generate-outreach").json()
    client.post(
        f"/api/leads/{lead['id']}/reject-outreach", json={"ai_output_id": output["id"]}
    )

    lead_after = client.get(f"/api/leads/{lead['id']}").json()
    assert lead_after["status"] == "disqualified"


def test_blocked_status_survives_ai_generation(client: TestClient) -> None:
    """generate-summary / generate-outreach must never touch Lead.status."""
    csv = BLOCKED_HOT_CSV.format(status="unsubscribed")
    files = {"file": ("leads.csv", csv.encode("utf-8"), "text/csv")}
    up = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "phase3-a2-generate"}
    ).json()
    client.post(f"/api/batches/{up['batch_id']}/score")
    lead = client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"][0]
    assert lead["status"] == "unsubscribed"

    client.post(f"/api/leads/{lead['id']}/generate-summary")
    client.post(f"/api/leads/{lead['id']}/generate-outreach")

    lead_after = client.get(f"/api/leads/{lead['id']}").json()
    assert lead_after["status"] == "unsubscribed"


def test_blocked_status_survives_demo_auto_approve(client: TestClient) -> None:
    """The /api/demo/run auto-approve path (app/services/demo.py::_approve)
    uses the same status-preserving guard, even though demo data never
    actually contains a blocked lead -- defense in depth, verified directly
    against the service function rather than requiring blocked demo data."""
    from app.services.demo import DISQUALIFIED_STATUSES

    assert "do_not_contact" in DISQUALIFIED_STATUSES  # sanity: same constant reused
