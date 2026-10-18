"""GET /api/leads supports server-side sort/filter by the current ranking
score/band (against the active seller profile's target customers), applied
before pagination, and never breaks on unranked leads or on a lead ranked
multiple times.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Lead, LeadBatch
from tests.conftest import US_SECTOR_PREFERENCES, activate_preferences

CSV_NO_STRUCTURED_COUNTRY = (
    "company_name,industry\n"
    "Ambiguous Health Co,\n"  # no industry, no location -> 0 points, insufficient_evidence
    "Never Scored Co,Real Estate\n"  # left unscored deliberately
)


def _make_pdl_lead(
    db_session: Session, batch: LeadBatch, *, company_name: str, industry: str, country: str
) -> Lead:
    lead = Lead(
        id=uuid.uuid4(),
        batch_id=batch.id,
        company_name=company_name,
        industry=industry,
        cleaned_data={"country": country},
        source="pdl_import",
        status="new",
    )
    db_session.add(lead)
    db_session.flush()
    return lead


def _setup(client: TestClient, db_session: Session) -> dict[str, str]:
    """Five leads spanning every band, ranked against US_SECTOR_PREFERENCES:
      - Strong Match Co: industry+country match -> 100, strong_match
      - Partial Match Co: industry match, country mismatch -> 64, partial_match
      - Weak Match Co: industry mismatch, country match -> 36, weak_match
      - Ambiguous Health Co: nothing to compare (CSV) -> 0, insufficient_evidence
      - Never Scored Co: left unranked
    """
    activate_preferences(client, US_SECTOR_PREFERENCES)
    batch = LeadBatch(name="fit-fixtures", source="pdl", status="uploaded")
    db_session.add(batch)
    db_session.flush()

    strong = _make_pdl_lead(
        db_session, batch, company_name="Strong Match Co",
        industry="Hospital & Health Care", country="United States",
    )
    partial = _make_pdl_lead(
        db_session, batch, company_name="Partial Match Co",
        industry="Real Estate", country="Canada",
    )
    weak = _make_pdl_lead(
        db_session, batch, company_name="Weak Match Co",
        industry="Retail", country="United States",
    )
    db_session.commit()

    files = {"file": ("leads.csv", CSV_NO_STRUCTURED_COUNTRY.encode("utf-8"), "text/csv")}
    upload = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "fit-fixtures-csv"}
    ).json()
    csv_leads = client.get(f"/api/leads?batch_id={upload['batch_id']}").json()["items"]
    csv_by_name = {lead["company_name"]: lead["id"] for lead in csv_leads}

    by_name = {
        "Strong Match Co": str(strong.id),
        "Partial Match Co": str(partial.id),
        "Weak Match Co": str(weak.id),
        "Ambiguous Health Co": csv_by_name["Ambiguous Health Co"],
        "Never Scored Co": csv_by_name["Never Scored Co"],
    }

    for name in ("Strong Match Co", "Partial Match Co", "Weak Match Co", "Ambiguous Health Co"):
        response = client.post(f"/api/leads/{by_name[name]}/fit-score")
        assert response.status_code == 201, response.text
    # 'Never Scored Co' deliberately left unscored.

    return by_name


def test_band_filter_includes_matching_and_excludes_other_bands(
    client: TestClient, db_session: Session
) -> None:
    by_name = _setup(client, db_session)

    strong = client.get("/api/leads?fit_band=strong_match&limit=50").json()
    assert strong["total"] == 1
    assert strong["items"][0]["id"] == by_name["Strong Match Co"]

    partial = client.get("/api/leads?fit_band=partial_match&limit=50").json()
    assert partial["total"] == 1
    assert partial["items"][0]["id"] == by_name["Partial Match Co"]

    weak = client.get("/api/leads?fit_band=weak_match&limit=50").json()
    assert weak["total"] == 1
    assert weak["items"][0]["id"] == by_name["Weak Match Co"]


def test_band_filter_excludes_unscored_leads(client: TestClient, db_session: Session) -> None:
    by_name = _setup(client, db_session)
    response = client.get("/api/leads?fit_band=insufficient_evidence&limit=50")
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == by_name["Ambiguous Health Co"]
    returned_ids = {item["id"] for item in body["items"]}
    assert by_name["Never Scored Co"] not in returned_ids


def test_min_fit_score_filter_excludes_unscored_and_low_scores(
    client: TestClient, db_session: Session
) -> None:
    by_name = _setup(client, db_session)
    response = client.get("/api/leads?min_fit_score=60&limit=50")
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    # Strong (100) and Partial (64) clear the threshold; Weak (36),
    # Ambiguous (0, needs data) and the unranked lead do not.
    assert returned_ids == {by_name["Strong Match Co"], by_name["Partial Match Co"]}
    assert body["total"] == 2


def test_sort_by_fit_score_desc_places_unscored_last(
    client: TestClient, db_session: Session
) -> None:
    by_name = _setup(client, db_session)
    response = client.get("/api/leads?sort=fit_score_desc&limit=50")
    body = response.json()
    assert body["total"] == 5
    ids_in_order = [item["id"] for item in body["items"]]
    assert ids_in_order == [
        by_name["Strong Match Co"],  # 100
        by_name["Partial Match Co"],  # 64
        by_name["Weak Match Co"],  # 36
        by_name["Ambiguous Health Co"],  # 0 (needs data)
        by_name["Never Scored Co"],  # unranked -> last
    ]


def test_sort_by_fit_score_asc_also_places_unscored_last(
    client: TestClient, db_session: Session
) -> None:
    """Ascending must mean 'lowest score first among SCORED leads' --
    unscored (NULL) leads are not a score of zero and must not sort to the
    front just because the direction flipped."""
    by_name = _setup(client, db_session)
    response = client.get("/api/leads?sort=fit_score_asc&limit=50")
    body = response.json()
    ids_in_order = [item["id"] for item in body["items"]]
    assert ids_in_order == [
        by_name["Ambiguous Health Co"], by_name["Weak Match Co"], by_name["Partial Match Co"],
        by_name["Strong Match Co"], by_name["Never Scored Co"],  # unranked -> still last
    ]


def test_totals_remain_correct_when_pagination_returns_fewer_items(
    client: TestClient, db_session: Session
) -> None:
    _setup(client, db_session)
    # 3 leads clear min_fit_score=30, but ask for only 2 per page.
    page1 = client.get("/api/leads?min_fit_score=30&limit=2&offset=0").json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True

    page2 = client.get("/api/leads?min_fit_score=30&limit=2&offset=2").json()
    assert page2["total"] == 3
    assert len(page2["items"]) == 1
    assert page2["has_more"] is False

    seen_ids = {item["id"] for item in page1["items"]} | {item["id"] for item in page2["items"]}
    assert len(seen_ids) == 3  # no duplicate, no dropped row across pages


def test_multiple_historical_scores_do_not_duplicate_lead_in_results(
    client: TestClient, db_session: Session
) -> None:
    by_name = _setup(client, db_session)
    strong_id = by_name["Strong Match Co"]

    # Rescore the same lead twice more -- three LeadFitScore rows now exist
    # for it, but it must still appear exactly once in list results.
    client.post(f"/api/leads/{strong_id}/fit-score")
    client.post(f"/api/leads/{strong_id}/fit-score")

    response = client.get("/api/leads?fit_band=strong_match&limit=50")
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == strong_id


def test_invalid_sort_value_rejected(client: TestClient) -> None:
    response = client.get("/api/leads?sort=not_a_real_sort")
    assert response.status_code == 400


def test_default_sort_and_no_filter_unchanged_behavior(
    client: TestClient, db_session: Session
) -> None:
    """Existing behavior (no fit params) must be completely unaffected."""
    _setup(client, db_session)
    response = client.get("/api/leads?limit=50")
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
