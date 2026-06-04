from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WorkflowEvent

VALID_CSV = (
    "company_name,industry,contact_email\n"
    "Cascade Modular Homes,Housing,ops@cascade.com\n"
    "Vault Outfitters,Retail,hello@vault.com\n"
)

MIXED_CSV = (
    "company_name,industry\n"
    ",Tech\n"               # row 2 -- invalid (no company_name)
    "Real Co,Health\n"      # row 3 -- valid
)


def _upload(
    client: TestClient,
    raw: str | bytes,
    filename: str = "leads.csv",
    batch_name: str | None = None,
):
    body = raw.encode("utf-8") if isinstance(raw, str) else raw
    files = {"file": (filename, body, "text/csv")}
    data = {"batch_name": batch_name} if batch_name is not None else None
    return client.post("/api/batches/upload", files=files, data=data)


def test_upload_valid_csv_returns_201_and_creates_resources(client: TestClient) -> None:
    response = _upload(client, VALID_CSV, batch_name="Demo upload")
    assert response.status_code == 201
    body = response.json()
    assert body["batch_name"] == "Demo upload"
    assert body["total_rows"] == 2
    assert body["valid_rows"] == 2
    assert body["invalid_rows"] == 0
    assert body["errors"] == []
    batch_id = body["batch_id"]

    listed = client.get("/api/batches")
    assert listed.status_code == 200
    assert any(b["id"] == batch_id for b in listed.json())

    detail = client.get(f"/api/batches/{batch_id}")
    assert detail.status_code == 200
    assert detail.json()["total_leads"] == 2
    assert detail.json()["processed_leads"] == 2
    assert detail.json()["status"] == "uploaded"

    leads = client.get(f"/api/leads?batch_id={batch_id}")
    assert leads.status_code == 200
    leads_payload = leads.json()
    assert len(leads_payload) == 2
    companies = {lead["company_name"] for lead in leads_payload}
    assert companies == {"Cascade Modular Homes", "Vault Outfitters"}

    one_lead = client.get(f"/api/leads/{leads_payload[0]['id']}")
    assert one_lead.status_code == 200
    assert one_lead.json()["company_name"] == leads_payload[0]["company_name"]


def test_upload_returns_row_errors_for_mixed_csv(client: TestClient) -> None:
    response = _upload(client, MIXED_CSV)
    assert response.status_code == 201
    body = response.json()
    assert body["total_rows"] == 2
    assert body["valid_rows"] == 1
    assert body["invalid_rows"] == 1
    assert len(body["errors"]) == 1
    err = body["errors"][0]
    assert err["row_number"] == 2
    assert err["field"] == "company_name"
    assert "required" in err["message"]

    detail = client.get(f"/api/batches/{body['batch_id']}")
    assert detail.json()["status"] == "uploaded"  # at least one valid row


def test_upload_all_invalid_rows_marks_batch_failed(client: TestClient) -> None:
    raw = "company_name,industry\n,Tech\n,Health\n"
    response = _upload(client, raw)
    assert response.status_code == 201
    body = response.json()
    assert body["valid_rows"] == 0
    assert body["invalid_rows"] == 2

    detail = client.get(f"/api/batches/{body['batch_id']}")
    assert detail.json()["status"] == "failed"
    assert detail.json()["total_leads"] == 2
    assert detail.json()["processed_leads"] == 0


def test_upload_missing_required_column_returns_400(client: TestClient) -> None:
    response = _upload(client, "website,industry\nhttp://a.com,Tech\n")
    assert response.status_code == 400
    assert "company_name" in response.json()["detail"]


def test_upload_empty_file_returns_400(client: TestClient) -> None:
    response = _upload(client, b"")
    assert response.status_code == 400


def test_upload_non_csv_extension_returns_400(client: TestClient) -> None:
    response = _upload(client, "company_name\nA\n", filename="leads.txt")
    assert response.status_code == 400


def test_get_unknown_batch_returns_404(client: TestClient) -> None:
    response = client.get("/api/batches/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_unknown_lead_returns_404(client: TestClient) -> None:
    response = client.get("/api/leads/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_list_batches_orders_newest_first(client: TestClient) -> None:
    _upload(client, VALID_CSV, batch_name="first")
    _upload(client, VALID_CSV, batch_name="second")
    listed = client.get("/api/batches").json()
    assert [b["name"] for b in listed[:2]] == ["second", "first"]


def test_list_leads_without_batch_filter_returns_all(client: TestClient) -> None:
    r1 = _upload(client, VALID_CSV, batch_name="A").json()
    r2 = _upload(client, VALID_CSV, batch_name="B").json()
    leads = client.get("/api/leads").json()
    batch_ids = {lead["batch_id"] for lead in leads}
    assert {r1["batch_id"], r2["batch_id"]} <= batch_ids
    assert len(leads) == 4


def test_upload_creates_batch_uploaded_workflow_event(
    client: TestClient, db_session: Session
) -> None:
    response = _upload(client, VALID_CSV, batch_name="audit").json()
    batch_id = UUID(response["batch_id"])

    events = (
        db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.batch_id == batch_id)
        )
        .scalars()
        .all()
    )
    batch_uploaded = [e for e in events if e.event_type == "batch_uploaded"]
    assert len(batch_uploaded) == 1
    data = batch_uploaded[0].event_data
    assert data["total_rows"] == 2
    assert data["valid_rows"] == 2
    assert data["invalid_rows"] == 0


def test_header_only_csv_returns_failed_batch(client: TestClient) -> None:
    response = _upload(client, "company_name,industry\n", batch_name="empty")
    assert response.status_code == 201
    body = response.json()
    assert body["total_rows"] == 0
    assert body["valid_rows"] == 0
    assert body["invalid_rows"] == 0
    assert body["errors"] == []
    detail = client.get(f"/api/batches/{body['batch_id']}").json()
    assert detail["status"] == "failed"
