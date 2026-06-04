from __future__ import annotations

import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IntegrationPush, WorkflowEvent

ZERO_UUID = "00000000-0000-0000-0000-000000000000"

# Cascade + Northbridge -> Hot, Vault -> Cold (verified by Phase 4 unit tests).
MIXED_CSV = (
    "company_name,industry,contact_title,contact_name,contact_email,website,company_size,source,notes\n"
    "Cascade Modular,Housing,VP Operations,Sarah Chen,sarah@cascade.com,cascade.com,240,referral,tenant maintenance leasing scheduling pain\n"
    "Northbridge Clinics,Healthcare,Practice Manager,Anika Rao,anika@northbridge.com,northbridge.com,1000+,webinar,patient appointment scheduling intake forms\n"
    "Vault Outfitters,Retail,COO,Jamie Russo,hello@vault.com,vault.com,560,csv,seasonal staff\n"
)


def _upload(client: TestClient, csv: str = MIXED_CSV, name: str = "push-test") -> dict:
    files = {"file": ("leads.csv", csv.encode("utf-8"), "text/csv")}
    return client.post(
        "/api/batches/upload", files=files, data={"batch_name": name}
    ).json()


def _scored_setup(client: TestClient) -> tuple[str, list[dict]]:
    up = _upload(client)
    batch_id = up["batch_id"]
    client.post(f"/api/batches/{batch_id}/score")
    leads = client.get(f"/api/leads?batch_id={batch_id}").json()
    return batch_id, leads


def _lead_by(leads: list[dict], company_name: str) -> dict:
    return next(lead for lead in leads if lead["company_name"] == company_name)


# ---------------- single lead push -----------------

def test_push_hot_lead_mock_success(
    client: TestClient, db_session: Session
) -> None:
    _, leads = _scored_setup(client)
    hot = _lead_by(leads, "Cascade Modular")

    response = client.post(
        f"/api/leads/{hot['id']}/push",
        json={"integration_type": "slack"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["integration_type"] == "slack"
    assert body["status"] == "mock_success"
    assert body["lead_id"] == hot["id"]
    assert "SLACK_WEBHOOK_URL" not in (body.get("response_text") or "")
    assert "hooks.slack.com" not in json.dumps(body)
    # The persisted payload should carry the readable Slack text.
    text = body["payload"]["text"]
    assert "Cascade Modular" in text
    assert "Hot" in text
    assert "94" in text or "/100" in text

    rows = (
        db_session.execute(
            select(IntegrationPush).where(IntegrationPush.lead_id == UUID(hot["id"]))
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == "mock_success"

    # Lead.status flipped to "pushed"
    lead_detail = client.get(f"/api/leads/{hot['id']}").json()
    assert lead_detail["status"] == "pushed"


def test_push_lead_without_score_returns_400(client: TestClient) -> None:
    # Upload only -- do NOT score the batch
    up = _upload(client)
    leads = client.get(f"/api/leads?batch_id={up['batch_id']}").json()
    response = client.post(
        f"/api/leads/{leads[0]['id']}/push",
        json={"integration_type": "slack"},
    )
    assert response.status_code == 400
    assert "scored" in response.json()["detail"].lower()


def test_push_non_hot_lead_without_force_returns_400(client: TestClient) -> None:
    _, leads = _scored_setup(client)
    cold = _lead_by(leads, "Vault Outfitters")
    response = client.post(
        f"/api/leads/{cold['id']}/push",
        json={"integration_type": "slack"},
    )
    assert response.status_code == 400
    assert "Hot" in response.json()["detail"]


def test_push_non_hot_lead_with_force_succeeds(client: TestClient) -> None:
    _, leads = _scored_setup(client)
    cold = _lead_by(leads, "Vault Outfitters")
    response = client.post(
        f"/api/leads/{cold['id']}/push",
        json={"integration_type": "slack", "force": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "mock_success"


def test_push_unknown_lead_returns_404(client: TestClient) -> None:
    response = client.post(
        f"/api/leads/{ZERO_UUID}/push",
        json={"integration_type": "slack"},
    )
    assert response.status_code == 404


def test_push_unsupported_integration_type_returns_400(client: TestClient) -> None:
    _, leads = _scored_setup(client)
    hot = _lead_by(leads, "Cascade Modular")
    response = client.post(
        f"/api/leads/{hot['id']}/push",
        json={"integration_type": "email"},
    )
    assert response.status_code == 400
    assert "slack" in response.json()["detail"].lower()


def test_failed_slack_push_persists_integration_push_row(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check #29: failed delivery still records IntegrationPush(status=failed)."""
    from app.services import integration_push as push_service

    def fake_send(_url: str | None, _payload: dict) -> tuple[str, str]:
        return "failed", "HTTP 500: Slack webhook rejected the request."

    monkeypatch.setattr(push_service, "send_slack_payload", fake_send)

    _, leads = _scored_setup(client)
    hot = _lead_by(leads, "Cascade Modular")

    response = client.post(
        f"/api/leads/{hot['id']}/push",
        json={"integration_type": "slack"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "hooks.slack.com" not in json.dumps(body)

    rows = (
        db_session.execute(
            select(IntegrationPush).where(IntegrationPush.lead_id == UUID(hot["id"]))
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == "failed"

    lead_detail = client.get(f"/api/leads/{hot['id']}").json()
    assert lead_detail["status"] != "pushed"


def test_push_creates_workflow_event(
    client: TestClient, db_session: Session
) -> None:
    _, leads = _scored_setup(client)
    hot = _lead_by(leads, "Cascade Modular")
    client.post(f"/api/leads/{hot['id']}/push", json={"integration_type": "slack"})

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(hot["id"]))
        )
        .scalars()
        .all()
    )
    pushed = [e for e in events if e.event_type == "lead_pushed"]
    assert len(pushed) == 1
    assert pushed[0].event_data["integration_type"] == "slack"
    assert pushed[0].event_data["status"] == "mock_success"


# ---------------- push history -----------------

def test_get_lead_pushes_returns_history(client: TestClient) -> None:
    _, leads = _scored_setup(client)
    hot = _lead_by(leads, "Cascade Modular")
    client.post(f"/api/leads/{hot['id']}/push", json={"integration_type": "slack"})
    # second push to ensure history ordering
    client.post(f"/api/leads/{hot['id']}/push", json={"integration_type": "slack"})

    response = client.get(f"/api/leads/{hot['id']}/pushes")
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 2
    assert all(row["integration_type"] == "slack" for row in history)


def test_get_lead_pushes_never_exposes_webhook_secrets(client: TestClient) -> None:
    """Manual check #9: push history must not leak SLACK_WEBHOOK_URL or hook URLs."""
    _, leads = _scored_setup(client)
    hot = _lead_by(leads, "Cascade Modular")
    client.post(f"/api/leads/{hot['id']}/push", json={"integration_type": "slack"})
    history_blob = json.dumps(client.get(f"/api/leads/{hot['id']}/pushes").json())
    assert "SLACK_WEBHOOK_URL" not in history_blob
    assert "hooks.slack.com" not in history_blob


def test_get_lead_pushes_unknown_lead_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/leads/{ZERO_UUID}/pushes")
    assert response.status_code == 404


def test_get_lead_pushes_empty_returns_empty_list(client: TestClient) -> None:
    _, leads = _scored_setup(client)
    hot = _lead_by(leads, "Cascade Modular")
    response = client.get(f"/api/leads/{hot['id']}/pushes")
    assert response.status_code == 200
    assert response.json() == []


# ---------------- batch push-hot -----------------

def test_batch_push_hot_pushes_all_hot_leads(client: TestClient) -> None:
    batch_id, _ = _scored_setup(client)
    response = client.post(
        f"/api/batches/{batch_id}/push-hot",
        json={"integration_type": "slack"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["batch_id"] == batch_id
    assert body["hot_leads_found"] == 2
    assert body["pushed"] == 2
    assert body["skipped"] == 0
    assert body["failed"] == 0
    assert len(body["results"]) == 2
    assert {r["status"] for r in body["results"]} == {"mock_success"}


def test_batch_push_hot_skips_already_pushed(client: TestClient) -> None:
    batch_id, _ = _scored_setup(client)
    client.post(f"/api/batches/{batch_id}/push-hot", json={"integration_type": "slack"})

    second = client.post(
        f"/api/batches/{batch_id}/push-hot",
        json={"integration_type": "slack"},
    ).json()
    assert second["pushed"] == 0
    assert second["skipped"] == 2
    assert all(r["status"] == "skipped" for r in second["results"])
    assert all("already pushed" in (r["reason"] or "") for r in second["results"])


def test_batch_push_hot_force_re_pushes(client: TestClient) -> None:
    batch_id, _ = _scored_setup(client)
    client.post(f"/api/batches/{batch_id}/push-hot", json={"integration_type": "slack"})

    forced = client.post(
        f"/api/batches/{batch_id}/push-hot",
        json={"integration_type": "slack", "force": True},
    ).json()
    assert forced["pushed"] == 2
    assert forced["skipped"] == 0


def test_batch_push_hot_unknown_batch_returns_404(client: TestClient) -> None:
    response = client.post(
        f"/api/batches/{ZERO_UUID}/push-hot",
        json={"integration_type": "slack"},
    )
    assert response.status_code == 404


def test_batch_push_hot_unsupported_integration_returns_400(client: TestClient) -> None:
    batch_id, _ = _scored_setup(client)
    response = client.post(
        f"/api/batches/{batch_id}/push-hot",
        json={"integration_type": "email"},
    )
    assert response.status_code == 400


def test_batch_push_hot_emits_workflow_event(
    client: TestClient, db_session: Session
) -> None:
    batch_id, _ = _scored_setup(client)
    client.post(f"/api/batches/{batch_id}/push-hot", json={"integration_type": "slack"})

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.batch_id == UUID(batch_id))
        )
        .scalars()
        .all()
    )
    matches = [e for e in events if e.event_type == "batch_hot_leads_pushed"]
    assert len(matches) == 1
    data = matches[0].event_data
    assert data["integration_type"] == "slack"
    assert data["hot_leads_found"] == 2
    assert data["pushed"] == 2
