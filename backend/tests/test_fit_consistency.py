"""Phase 4 closeout: every reader of "a lead's current v2 fit score" (single
GET, batch bulk listing, batch summary, GET /api/leads filter/sort) agrees
on WHICH stored row is current, including rows from other profile
versions and rows sharing a timestamp; current readiness is distinguished
from the snapshot stored at scoring time; and scoring has no side effects
on dispositions, drafts, reviews, or sends.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AIOutput,
    AIOutputReview,
    IntegrationPush,
    Lead,
    LeadBatch,
    LeadFitScore,
    WorkflowEvent,
)
from app.scoring import fit


def _batch(db_session: Session, status: str = "uploaded") -> LeadBatch:
    batch = LeadBatch(name="consistency", source="pdl", status=status)
    db_session.add(batch)
    db_session.commit()
    return batch


def _lead(
    db_session: Session,
    batch: LeadBatch,
    name: str,
    *,
    industry: str = "Hospital & Health Care",
    country: str | None = "United States",
    status: str = "new",
    contact_email: str | None = None,
) -> Lead:
    lead = Lead(
        id=uuid.uuid4(),
        batch_id=batch.id,
        company_name=name,
        industry=industry,
        cleaned_data={"country": country} if country is not None else {},
        source="pdl_import",
        status=status,
        contact_email=contact_email,
    )
    db_session.add(lead)
    db_session.commit()
    return lead


def _row(lead: Lead, *, band: str, fit_score: int, created_at: datetime, **versions) -> LeadFitScore:
    return LeadFitScore(
        id=versions.pop("id", uuid.uuid4()),
        lead_id=lead.id,
        scorer_version=versions.get("scorer_version", fit.SCORER_VERSION),
        profile_id=versions.get("profile_id", fit.PROFILE_ID),
        profile_version=versions.get("profile_version", fit.PROFILE_VERSION),
        normalization_version=versions.get("normalization_version", fit.NORMALIZATION_VERSION),
        input_fingerprint="f" * 64,
        fit_score=fit_score,
        evidence_coverage_pct=100.0,
        band=band,
        criteria=[],
        readiness={
            "outbound_email": {"status": "not_ready", "gaps": [], "gap_explanations": {}},
            "internal_slack_handoff": {"status": "ready", "gaps": [], "gap_explanations": {}},
        },
        eligibility_excluded=False,
        eligibility_reasons=[],
        computed_at=created_at,
        computation_ms=0.1,
        created_at=created_at,
    )


def _ids(body: dict) -> list[str]:
    return [item["id"] for item in body["items"]]


# ------------------------------------------------------ latest-row selection

def test_band_change_on_rescore_uses_latest_row_everywhere(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session)
    lead = _lead(db_session, batch, "Moving Co")
    assert client.post(f"/api/leads/{lead.id}/fit-score").json()["band"] == "strong_match"

    lead.cleaned_data = {"country": "Canada"}
    db_session.commit()
    assert client.post(f"/api/leads/{lead.id}/fit-score").json()["band"] == "partial_match"

    # Historical strong_match row must not make it match a strong filter.
    assert client.get("/api/leads?fit_band=strong_match").json()["total"] == 0
    assert _ids(client.get("/api/leads?fit_band=partial_match").json()) == [str(lead.id)]
    assert client.get(f"/api/leads/{lead.id}/fit-score").json()["band"] == "partial_match"

    summary = client.get(f"/api/batches/{batch.id}/fit-summary").json()
    assert summary["scored_leads"] == 1  # distinct leads, not rows
    assert summary["score_rows"] == 2
    assert summary["strong_match"] == 0
    assert summary["partial_match"] == 1
    assert summary["average_fit_score"] == 60.0


def test_rows_from_other_versions_are_history_not_current(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session)
    lead = _lead(db_session, batch, "Old Profile Co")
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            _row(lead, band="strong_match", fit_score=100, created_at=now, profile_version="0"),
            _row(lead, band="strong_match", fit_score=100, created_at=now,
                 normalization_version="fit-norm-v0"),
        ]
    )
    db_session.commit()

    assert client.get(f"/api/leads/{lead.id}/fit-score").status_code == 404
    assert client.get("/api/leads?fit_band=strong_match").json()["total"] == 0
    assert client.get("/api/leads?min_fit_score=0").json()["total"] == 0
    assert client.get(f"/api/batches/{batch.id}/fit-scores").json()["items"] == []
    summary = client.get(f"/api/batches/{batch.id}/fit-summary").json()
    assert summary["scored_leads"] == 0
    assert summary["unscored_leads"] == 1
    assert summary["score_rows"] == 0


def test_equal_timestamps_resolve_to_the_same_row_in_every_reader(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session)
    lead = _lead(db_session, batch, "Tied Co")
    same = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    low_id = uuid.UUID(int=1)
    high_id = uuid.UUID(int=2)
    db_session.add_all(
        [
            _row(lead, band="weak_match", fit_score=40, created_at=same, id=low_id),
            _row(lead, band="partial_match", fit_score=60, created_at=same, id=high_id),
        ]
    )
    db_session.commit()

    # id DESC breaks the tie: the higher id is current, in every reader.
    single = client.get(f"/api/leads/{lead.id}/fit-score").json()
    assert single["id"] == str(high_id)
    bulk = client.get(f"/api/batches/{batch.id}/fit-scores").json()
    assert [item["id"] for item in bulk["items"]] == [str(high_id)]
    assert _ids(client.get("/api/leads?fit_band=partial_match").json()) == [str(lead.id)]
    assert client.get("/api/leads?fit_band=weak_match").json()["total"] == 0
    summary = client.get(f"/api/batches/{batch.id}/fit-summary").json()
    assert (summary["partial_match"], summary["weak_match"]) == (1, 0)

    # Deterministic across repeated identical queries.
    for _ in range(3):
        assert client.get(f"/api/leads/{lead.id}/fit-score").json()["id"] == str(high_id)


def test_invalid_fit_band_is_rejected(client: TestClient) -> None:
    assert client.get("/api/leads?fit_band=Hot").status_code == 400


# ----------------------------------------------------------------- sorting

def _sort_fixture(client: TestClient, db_session: Session) -> tuple[LeadBatch, dict[str, str]]:
    batch = _batch(db_session)
    leads = {
        "strong": _lead(db_session, batch, "S", country="United States"),
        "partial": _lead(db_session, batch, "P", country="Canada"),
        "weak": _lead(db_session, batch, "W", industry="Retail", country="Canada"),
        "unscored_a": _lead(db_session, batch, "U1"),
        "unscored_b": _lead(db_session, batch, "U2"),
    }
    for key in ("strong", "partial", "weak"):
        client.post(f"/api/leads/{leads[key].id}/fit-score")
    return batch, {k: str(v.id) for k, v in leads.items()}


def _walk(client: TestClient, url: str, page: int = 2) -> tuple[list[str], set[int]]:
    ids: list[str] = []
    totals: set[int] = set()
    offset = 0
    while True:
        body = client.get(f"{url}&limit={page}&offset={offset}").json()
        totals.add(body["total"])
        ids.extend(_ids(body))
        if not body["has_more"]:
            return ids, totals
        offset += page


def test_unscored_last_in_both_directions_across_pages(
    client: TestClient, db_session: Session
) -> None:
    batch, ids = _sort_fixture(client, db_session)
    unscored = {ids["unscored_a"], ids["unscored_b"]}

    desc, totals = _walk(client, f"/api/leads?batch_id={batch.id}&sort=fit_score_desc")
    assert totals == {5}
    assert desc[:3] == [ids["strong"], ids["partial"], ids["weak"]]
    assert set(desc[3:]) == unscored

    asc, totals = _walk(client, f"/api/leads?batch_id={batch.id}&sort=fit_score_asc")
    assert totals == {5}
    assert asc[:3] == [ids["weak"], ids["partial"], ids["strong"]]
    assert set(asc[3:]) == unscored

    # Stable: the unscored tail is in the same order both times and on repeat.
    assert desc[3:] == asc[3:]
    assert _walk(client, f"/api/leads?batch_id={batch.id}&sort=fit_score_desc")[0] == desc


def test_filter_then_paginate_keeps_unique_totals_after_rescoring(
    client: TestClient, db_session: Session
) -> None:
    batch, ids = _sort_fixture(client, db_session)
    for _ in range(3):
        client.post(f"/api/leads/{ids['strong']}/fit-score")
        client.post(f"/api/leads/{ids['partial']}/fit-score")

    walked, totals = _walk(
        client, f"/api/leads?batch_id={batch.id}&min_fit_score=50&sort=fit_score_desc", page=1
    )
    assert totals == {2}
    assert walked == [ids["strong"], ids["partial"]]


# ------------------------------------------------ current vs historical

@pytest.mark.usefixtures("active_seller_profile")
def test_listing_readiness_is_current_and_snapshot_is_labeled_historical(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session)
    lead = _lead(db_session, batch, "Drafted Co", contact_email="ops@drafted.test")
    client.post(f"/api/leads/{lead.id}/fit-score")

    draft = client.post(f"/api/leads/{lead.id}/generate-outreach").json()
    approve = client.post(
        f"/api/leads/{lead.id}/approve-outreach", json={"ai_output_id": draft["id"]}
    )
    assert approve.status_code == 200, approve.text

    item = client.get(f"/api/batches/{batch.id}/fit-scores").json()["items"][0]
    assert item["readiness_is_current"] is True
    current_gaps = item["readiness"]["outbound_email"]["gaps"]
    assert "no_outreach_draft" not in current_gaps
    assert "draft_not_reviewed" not in current_gaps
    # Active real-kind seller revision + contact email + an approved draft
    # generated from exactly that revision: nothing blocks outbound email.
    assert item["readiness"]["outbound_email"]["status"] == "ready"
    # The stored snapshot still says what was true at scoring time.
    assert "no_outreach_draft" in item["at_scoring"]["readiness"]["outbound_email"]["gaps"]

    # Single-lead read agrees with the listing.
    single = client.get(f"/api/leads/{lead.id}/fit-score").json()
    assert single["readiness"] == item["readiness"]


def test_eligibility_reflects_a_block_applied_after_scoring(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session)
    lead = _lead(db_session, batch, "Later Blocked Co")
    client.post(f"/api/leads/{lead.id}/fit-score")
    lead.status = "do_not_contact"
    db_session.commit()

    item = client.get(f"/api/batches/{batch.id}/fit-scores").json()["items"][0]
    assert item["eligibility"]["excluded"] is True
    assert item["at_scoring"]["eligibility_excluded"] is False
    assert item["readiness"]["internal_slack_handoff"]["status"] == "not_ready"


def test_batch_readiness_covers_every_lead_on_the_page(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session)
    scored = _lead(db_session, batch, "Scored Co")
    blocked = _lead(db_session, batch, "Blocked Co", status="unsubscribed")
    _lead(db_session, batch, "Plain Co")
    client.post(f"/api/leads/{scored.id}/fit-score")

    page = client.get(f"/api/batches/{batch.id}/readiness?limit=2").json()
    assert page["total"] == 3
    assert len(page["items"]) == 2 and page["has_more"] is True
    rest = client.get(f"/api/batches/{batch.id}/readiness?limit=2&offset=2").json()
    by_lead = {item["lead_id"]: item for item in page["items"] + rest["items"]}
    assert len(by_lead) == 3  # scored and unscored alike
    assert by_lead[str(blocked.id)]["eligibility"]["excluded"] is True
    assert by_lead[str(scored.id)]["eligibility"]["excluded"] is False
    # Same order as the lead list.
    leads = client.get(f"/api/leads?batch_id={batch.id}&limit=2").json()
    assert [i["lead_id"] for i in page["items"]] == [lead["id"] for lead in leads["items"]]


def test_current_readiness_endpoint_works_for_unscored_lead(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session, status="partial")
    lead = _lead(db_session, batch, "Never Scored Co")
    body = client.get(f"/api/leads/{lead.id}/readiness").json()
    assert body["eligibility"]["excluded"] is True
    assert body["readiness"]["outbound_email"]["status"] == "not_ready"
    assert client.get(f"/api/leads/{uuid.uuid4()}/readiness").status_code == 404


# -------------------------------------------------------------- side effects

def test_scoring_audit_records_do_not_inflate_operational_metrics(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session)
    lead = _lead(db_session, batch, "Audited Co")
    first = client.post(f"/api/leads/{lead.id}/fit-score").json()
    second = client.post(f"/api/leads/{lead.id}/fit-score").json()
    events = list(db_session.scalars(
        select(WorkflowEvent).where(WorkflowEvent.event_type == "lead_fit_scored")
    ))
    assert {event.event_data["lead_fit_score_id"] for event in events} == {
        first["id"], second["id"],
    }
    assert all(event.lead_id == lead.id and event.batch_id == batch.id for event in events)
    assert all(event.event_data["profile_id"] == fit.PROFILE_ID for event in events)
    skipped = client.post(f"/api/batches/{batch.id}/fit-score").json()
    assert skipped["skipped_unchanged"] == 1
    assert db_session.scalar(select(func.count()).select_from(WorkflowEvent)) == 2
    metrics = client.get("/api/metrics/dashboard").json()
    assert metrics["fit_scored_leads"] == 1
    assert metrics["total_leads_processed"] == 0
    assert metrics["outreach_generated"] == metrics["outreach_approved"] == 0


def test_batch_commit_failure_counts_failed_rows_and_continues(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    from app.api import fit_scoring

    batch = _batch(db_session)
    for i in range(3):
        _lead(db_session, batch, f"Chunk Co {i}")
    monkeypatch.setattr(fit_scoring, "SCORE_CHUNK_SIZE", 2)
    real_commit = Session.commit
    calls = 0

    def fail_first_commit(session):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("chunk commit failed")
        return real_commit(session)

    monkeypatch.setattr(Session, "commit", fail_first_commit)
    response = client.post(f"/api/batches/{batch.id}/fit-score")
    assert response.status_code == 200, response.text
    run = response.json()
    assert (run["attempted"], run["newly_scored"], run["failed"]) == (3, 1, 2)
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(LeadFitScore)) == 1
    assert db_session.scalar(select(func.count()).select_from(WorkflowEvent)) == 1
    retry = client.post(f"/api/batches/{batch.id}/fit-score").json()
    assert (retry["newly_scored"], retry["skipped_unchanged"], retry["failed"]) == (2, 1, 0)


def test_batch_scoring_has_no_side_effects_beyond_score_rows(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session, status="partial")
    blocked = _lead(db_session, batch, "Blocked Co", status="disqualified")
    _lead(db_session, batch, "Open Co")

    run = client.post(f"/api/batches/{batch.id}/fit-score").json()
    assert (run["attempted"], run["newly_scored"], run["failed"]) == (2, 2, 0)

    db_session.expire_all()
    assert db_session.get(LeadBatch, batch.id).status == "partial"
    assert db_session.get(Lead, blocked.id).status == "disqualified"
    for model in (AIOutput, AIOutputReview, IntegrationPush):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0

    # Pressing it again writes nothing new (inputs unchanged) and counts
    # stay distinct-lead.
    again = client.post(f"/api/batches/{batch.id}/fit-score").json()
    assert (again["newly_scored"], again["skipped_unchanged"]) == (0, 2)
    assert again["summary"]["scored_leads"] == 2
    assert again["summary"]["score_rows"] == 2


# ------------------------------------------------ draft identity tie-break

def test_drafts_sharing_a_timestamp_have_one_current_draft(
    client: TestClient, db_session: Session
) -> None:
    batch = _batch(db_session)
    lead = _lead(db_session, batch, "Twin Drafts Co")
    same = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    content = {
        "subject": "s", "email_body": "b", "personalization_points": [],
        "call_note": "", "confidence": "high",
    }
    low = AIOutput(id=uuid.UUID(int=10), lead_id=lead.id, output_type="outreach_email",
                   content=content, model_used="mock", prompt_version="v1", created_at=same)
    high = AIOutput(id=uuid.UUID(int=11), lead_id=lead.id, output_type="outreach_email",
                    content=content, model_used="mock", prompt_version="v1", created_at=same)
    db_session.add_all([low, high])
    db_session.commit()

    latest = client.get(
        f"/api/leads/{lead.id}/latest-ai-output?output_type=outreach_email"
    ).json()
    assert latest["id"] == str(high.id)

    stale = client.post(
        f"/api/leads/{lead.id}/approve-outreach", json={"ai_output_id": str(low.id)}
    )
    assert stale.status_code == 409
    current = client.post(
        f"/api/leads/{lead.id}/approve-outreach", json={"ai_output_id": str(high.id)}
    )
    assert current.status_code == 200, current.text
