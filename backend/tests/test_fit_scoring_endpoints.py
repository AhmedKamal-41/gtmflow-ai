from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead, LeadFitScore

CSV_INDUSTRY_ONLY = (
    "company_name,industry,contact_email\n"
    "Riverside Real Estate,Real Estate,ops@riverside.com\n"
)


def _upload(client: TestClient, csv: str = CSV_INDUSTRY_ONLY, batch_name: str = "fit-test") -> dict:
    files = {"file": ("leads.csv", csv.encode("utf-8"), "text/csv")}
    data = {"batch_name": batch_name}
    return client.post("/api/batches/upload", files=files, data=data).json()


def _first_lead_id(client: TestClient, batch_id: str) -> str:
    leads = client.get(f"/api/leads?batch_id={batch_id}").json()["items"]
    assert leads, "no leads found in batch"
    return leads[0]["id"]


def _make_pdl_style_lead(db_session: Session, batch_id: str, **overrides) -> Lead:
    from app.models import LeadBatch
    import uuid

    if batch_id is None:
        batch = LeadBatch(name="pdl-style", source="pdl", status="uploaded")
        db_session.add(batch)
        db_session.flush()
        batch_id = batch.id

    lead = Lead(
        id=uuid.uuid4(),
        batch_id=batch_id,
        company_name=overrides.pop("company_name", "Full Match Health Co"),
        industry=overrides.pop("industry", "Hospital & Health Care"),
        cleaned_data=overrides.pop("cleaned_data", {"country": "United States"}),
        source="pdl_import",
        status=overrides.pop("status", "new"),
        **overrides,
    )
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


def test_post_fit_score_creates_and_persists(client: TestClient, db_session: Session) -> None:
    upload = _upload(client)
    lead_id = _first_lead_id(client, upload["batch_id"])

    response = client.post(f"/api/leads/{lead_id}/fit-score")
    assert response.status_code == 201
    body = response.json()
    assert body["lead_id"] == lead_id
    assert body["profile_id"] == "demo-us-sectors-v1"
    # Industry matches (Real Estate), country unknown (CSV lead, no
    # structured field) -> 60/100 fit, 60% coverage -> insufficient_evidence.
    assert body["fit_score"] == 60
    assert body["evidence_coverage_pct"] == 60.0
    assert body["band"] == "insufficient_evidence"
    industry_criterion = next(c for c in body["criteria"] if c["name"] == "industry")
    assert industry_criterion["result"] == "match"
    country_criterion = next(c for c in body["criteria"] if c["name"] == "country")
    assert country_criterion["result"] == "unknown"

    rows = db_session.execute(
        select(LeadFitScore).where(LeadFitScore.lead_id == UUID(lead_id))
    ).scalars().all()
    assert len(rows) == 1


def test_get_fit_score_404_before_scoring_then_200_after(client: TestClient) -> None:
    upload = _upload(client)
    lead_id = _first_lead_id(client, upload["batch_id"])

    before = client.get(f"/api/leads/{lead_id}/fit-score")
    assert before.status_code == 404

    client.post(f"/api/leads/{lead_id}/fit-score")
    after = client.get(f"/api/leads/{lead_id}/fit-score")
    assert after.status_code == 200
    assert after.json()["fit_score"] == 60


def test_rescoring_inserts_new_row_not_overwrite(client: TestClient, db_session: Session) -> None:
    upload = _upload(client)
    lead_id = _first_lead_id(client, upload["batch_id"])

    first = client.post(f"/api/leads/{lead_id}/fit-score").json()
    second = client.post(f"/api/leads/{lead_id}/fit-score").json()
    assert first["id"] != second["id"]

    rows = db_session.execute(
        select(LeadFitScore).where(LeadFitScore.lead_id == UUID(lead_id))
    ).scalars().all()
    assert len(rows) == 2
    # Deterministic: identical inputs/versions -> identical result content.
    assert first["fit_score"] == second["fit_score"]
    assert first["input_fingerprint"] == second["input_fingerprint"]


def test_full_match_pdl_style_lead_scores_100_strong_match(
    client: TestClient, db_session: Session
) -> None:
    lead = _make_pdl_style_lead(db_session, batch_id=None)
    response = client.post(f"/api/leads/{lead.id}/fit-score")
    assert response.status_code == 201
    body = response.json()
    assert body["fit_score"] == 100
    assert body["evidence_coverage_pct"] == 100.0
    assert body["band"] == "strong_match"


def test_fit_scoring_never_mutates_lead_status(client: TestClient, db_session: Session) -> None:
    upload = _upload(client)
    lead_id = _first_lead_id(client, upload["batch_id"])

    lead_before = client.get(f"/api/leads/{lead_id}").json()
    assert lead_before["status"] == "new"

    client.post(f"/api/leads/{lead_id}/fit-score")

    lead_after = client.get(f"/api/leads/{lead_id}").json()
    assert lead_after["status"] == "new"  # untouched by v2 scoring


def test_blocked_lead_full_fit_score_still_excluded_from_routing(
    client: TestClient, db_session: Session
) -> None:
    """A high fit score must never imply contactability -- Part C.4."""
    lead = _make_pdl_style_lead(db_session, batch_id=None, status="do_not_contact")

    response = client.post(f"/api/leads/{lead.id}/fit-score")
    body = response.json()
    assert body["fit_score"] == 100
    assert body["band"] == "strong_match"
    assert body["eligibility"]["excluded"] is True
    assert any("do_not_contact" in r for r in body["eligibility"]["reasons"])

    # Rescoring must not have cleared the blocked disposition.
    refreshed = db_session.get(Lead, lead.id)
    assert refreshed.status == "do_not_contact"

    # And the actual push pipeline still refuses it, even with force=true.
    push_score = client.post(f"/api/leads/{lead.id}/score")  # legacy v1 score required by push gate
    assert push_score.status_code == 200
    push = client.post(
        f"/api/leads/{lead.id}/push", json={"integration_type": "slack", "force": True}
    )
    assert push.status_code == 400


def test_partial_batch_fit_score_flags_eligibility_and_batch_endpoint_leaves_status_partial(
    client: TestClient, monkeypatch, db_session: Session
) -> None:
    from app.api import batches as batches_module

    monkeypatch.setattr(batches_module, "CSV_MAX_ROWS", 1)
    csv_text = "company_name,industry\nReal Estate Co,Real Estate\nSecond Co,Real Estate\n"
    files = {"file": ("leads.csv", csv_text.encode("utf-8"), "text/csv")}
    upload_response = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "partial-fit"}
    )
    assert upload_response.status_code == 422
    batch_id = upload_response.json()["detail"].split("batch ")[1].split(" ")[0]

    batch_before = client.get(f"/api/batches/{batch_id}").json()
    assert batch_before["status"] == "partial"

    summary_response = client.post(f"/api/batches/{batch_id}/fit-score")
    assert summary_response.status_code == 200
    run = summary_response.json()
    assert run["attempted"] == 1
    assert run["newly_scored"] == 1
    assert run["summary"]["scored_leads"] == 1

    batch_after = client.get(f"/api/batches/{batch_id}").json()
    assert batch_after["status"] == "partial"  # untouched by v2 batch scoring

    lead_id = _first_lead_id(client, batch_id)
    fit_score = client.get(f"/api/leads/{lead_id}/fit-score").json()
    assert fit_score["eligibility"]["excluded"] is True
    assert any("partial" in r for r in fit_score["eligibility"]["reasons"])


def test_fit_profile_endpoint_exposes_versioned_rubric(client: TestClient) -> None:
    response = client.get("/api/scoring/fit-profile")
    assert response.status_code == 200
    body = response.json()
    assert body["profile_id"] == "demo-us-sectors-v1"
    assert body["industry_weight"] == 60
    assert body["country_weight"] == 40
    assert body["size_weight"] == 0
    assert body["max_fit_score"] == 100
    assert set(body["industry_match_values"]) == {
        "hospital & health care", "medical practice", "real estate",
    }


def test_batch_fit_score_summary_counts_and_bulk_list_endpoint(
    client: TestClient, db_session: Session
) -> None:
    upload = _upload(client)
    batch_id = upload["batch_id"]

    run = client.post(f"/api/batches/{batch_id}/fit-score").json()
    assert run["newly_scored"] == 1
    summary = run["summary"]
    assert summary["scored_leads"] == 1
    assert summary["insufficient_evidence"] == 1  # industry-only match, no structured country

    listing = client.get(f"/api/batches/{batch_id}/fit-scores").json()
    assert listing["total"] == 1
    assert len(listing["items"]) == 1
    assert listing["items"][0]["fit_score"] == 60


def test_unscored_leads_do_not_break_bulk_listing_pagination(client: TestClient) -> None:
    """Part E: unscored leads must be supported, not crash or corrupt
    pagination totals -- an unscored lead is simply absent from `items`
    while `total` still honestly reflects every lead in the batch."""
    upload = _upload(client)
    batch_id = upload["batch_id"]
    # Deliberately never call fit-score.
    listing = client.get(f"/api/batches/{batch_id}/fit-scores").json()
    assert listing["total"] == 1
    assert listing["items"] == []
