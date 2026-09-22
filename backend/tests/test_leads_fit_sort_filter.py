"""Phase 4 Part E: GET /api/leads supports server-side sort/filter by the
v2 fit score/band, applied before pagination, and never breaks on unscored
leads or on a lead rescored multiple times.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Lead, LeadBatch

CSV_NO_STRUCTURED_COUNTRY = (
    "company_name,industry\n"
    "Ambiguous Health Co,Hospital & Health Care\n"  # industry match, country unknown -> insufficient_evidence
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
    """Five leads spanning every band:
      - Strong Match Co: industry+country match -> 100, strong_match
      - Partial Match Co: industry match, country mismatch -> 60, partial_match
      - Weak Match Co: industry mismatch, country match -> 40, weak_match
      - Ambiguous Health Co: industry match, no structured country (CSV) -> 60 pts but insufficient_evidence
      - Never Scored Co: left unscored
    """
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
    # Strong (100), Partial (60), and Ambiguous (60 pts, despite
    # insufficient_evidence band) all clear the raw-points threshold;
    # Weak (40) and the unscored lead do not.
    assert returned_ids == {
        by_name["Strong Match Co"], by_name["Partial Match Co"], by_name["Ambiguous Health Co"],
    }
    assert body["total"] == 3


def test_sort_by_fit_score_desc_places_unscored_last(
    client: TestClient, db_session: Session
) -> None:
    by_name = _setup(client, db_session)
    response = client.get("/api/leads?sort=fit_score_desc&limit=50")
    body = response.json()
    assert body["total"] == 5
    ids_in_order = [item["id"] for item in body["items"]]
    assert ids_in_order[0] == by_name["Strong Match Co"]  # 100
    # Partial Match Co and Ambiguous Health Co are tied at 60 -- either
    # relative order is fine, but both must precede Weak (40) and both
    # must come from the scored set.
    assert set(ids_in_order[1:3]) == {by_name["Partial Match Co"], by_name["Ambiguous Health Co"]}
    assert ids_in_order[3] == by_name["Weak Match Co"]  # 40
    assert ids_in_order[4] == by_name["Never Scored Co"]  # unscored -> last


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
    assert ids_in_order[0] == by_name["Weak Match Co"]  # 40, lowest scored
    assert set(ids_in_order[1:3]) == {by_name["Partial Match Co"], by_name["Ambiguous Health Co"]}
    assert ids_in_order[3] == by_name["Strong Match Co"]  # 100, highest scored
    assert ids_in_order[4] == by_name["Never Scored Co"]  # unscored -> still last


def test_totals_remain_correct_when_pagination_returns_fewer_items(
    client: TestClient, db_session: Session
) -> None:
    _setup(client, db_session)
    # 3 leads clear min_fit_score=60, but ask for only 2 per page.
    page1 = client.get("/api/leads?min_fit_score=60&limit=2&offset=0").json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True

    page2 = client.get("/api/leads?min_fit_score=60&limit=2&offset=2").json()
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
