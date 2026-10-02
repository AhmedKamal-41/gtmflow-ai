"""Phase 4 Parts C/D/E: API surface for the v2 deterministic company-fit
scorer. Additive -- does not call or alter anything in app/api/scoring.py
(the legacy v1 endpoints), and never mutates Lead.status / LeadBatch.status
/ AIOutputReview, never generates or approves a draft, never sends (Part
D.3).

Every read of "the current fit score" or "current readiness" goes through
app/services/fit_queries.py, so single-lead, bulk, summary, and list
endpoints can't disagree about which stored row is current.

Two kinds of readiness appear in responses, deliberately kept apart:

* `readiness` / `eligibility` -- CURRENT, recomputed from live lead,
  batch, draft, and review state on every read (`readiness_is_current` is
  always true).
* `at_scoring` -- the HISTORICAL readiness/eligibility snapshot stored
  with the score row, i.e. what was true when it was
  computed. Useful for auditing; never used to gate anything.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Lead, LeadBatch
from app.schemas.lead_fit_score import (
    BatchFitScoreRunSummary,
    BatchFitSummary,
    CurrentReadinessResponse,
    FitProfileResponse,
    LeadFitScoreResponse,
)
from app.schemas.pagination import Page
from app.scoring import fit
from app.services import fit_queries
from app.services.fit_scoring import fit_score_response, score_leads
from app.services.pagination import pagination_params

router = APIRouter(tags=["fit-scoring"])

# Leads scored per DB transaction by the batch endpoint -- bounded memory
# and bounded transaction size regardless of batch size.
SCORE_CHUNK_SIZE = 500


@router.post(
    "/api/leads/{lead_id}/fit-score",
    response_model=LeadFitScoreResponse,
    status_code=status.HTTP_201_CREATED,
)
def score_lead_fit(lead_id: UUID, session: Session = Depends(get_session)) -> LeadFitScoreResponse:
    """Computes and PERSISTS a new versioned row. Rescoring never mutates
    or replaces a prior row -- both remain queryable (Part D.1/D.6)."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    [(_, response, error)] = score_leads(session, [lead], persist=True)
    if error is not None:
        raise error
    session.commit()
    assert response is not None
    return response


@router.get(
    "/api/leads/{lead_id}/fit-score",
    response_model=LeadFitScoreResponse,
)
def get_latest_lead_fit_score(
    lead_id: UUID, session: Session = Depends(get_session)
) -> LeadFitScoreResponse:
    """The latest applicable stored fit/band/coverage/criteria, exactly as
    computed, plus CURRENT readiness/eligibility (Part D.4: a stored
    snapshot must not be trusted after lead/batch/outreach state changed)."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    stored = fit_queries.latest_fit_score(session, lead_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Lead has not been fit-scored yet")
    readiness, eligibility = fit_queries.current_readiness(session, lead)
    return fit_score_response(
        lead=lead, row=stored, fit_result=None, readiness=readiness, eligibility=eligibility
    )


@router.get(
    "/api/leads/{lead_id}/readiness",
    response_model=CurrentReadinessResponse,
)
def get_current_readiness(
    lead_id: UUID, session: Session = Depends(get_session)
) -> CurrentReadinessResponse:
    """Current outreach readiness and routing eligibility, independent of
    whether the lead has ever been fit-scored -- these describe live state,
    not a score."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    readiness, eligibility = fit_queries.current_readiness(session, lead)
    return CurrentReadinessResponse(
        lead_id=lead.id,
        readiness=readiness.to_dict(),
        eligibility=eligibility.to_dict(),
    )


@router.get("/api/scoring/fit-profile", response_model=FitProfileResponse)
def get_fit_profile() -> FitProfileResponse:
    return FitProfileResponse(
        profile_id=fit.PROFILE_ID,
        profile_version=fit.PROFILE_VERSION,
        scorer_version=fit.SCORER_VERSION,
        normalization_version=fit.NORMALIZATION_VERSION,
        description=(
            "Broad demonstration profile -- NOT a calibrated ideal-customer "
            "profile. Awards points for exact industry/country membership "
            "only; every other signal (website, contact details, founded "
            "year, name keywords, dataset provider) carries zero weight "
            "until a real seller profile is configured (Phase 5)."
        ),
        industry_match_values=sorted(fit.INDUSTRY_MATCH_VALUES),
        industry_weight=fit.INDUSTRY_WEIGHT,
        country_match_values=sorted(fit.COUNTRY_MATCH_VALUES),
        country_weight=fit.COUNTRY_WEIGHT,
        size_weight=fit.SIZE_WEIGHT,
        zero_weight_criteria=list(fit.ZERO_WEIGHT_CRITERIA),
        max_fit_score=fit.MAX_FIT_SCORE,
        coverage_threshold_pct=fit.COVERAGE_THRESHOLD_PCT,
        band_strong_min=fit.BAND_STRONG_MIN,
        band_partial_min=fit.BAND_PARTIAL_MIN,
    )


@router.post(
    "/api/batches/{batch_id}/fit-score",
    response_model=BatchFitScoreRunSummary,
    status_code=status.HTTP_200_OK,
)
def score_batch_fit(batch_id: UUID, session: Session = Depends(get_session)) -> BatchFitScoreRunSummary:
    """Scores every lead in the batch in chunks of SCORE_CHUNK_SIZE, one
    committed transaction per chunk. Leads whose latest applicable row has
    the same input fingerprint are skipped (reported, not rescored), so
    pressing the button twice doesn't pile up identical history rows.
    Never touches LeadBatch.status or Lead.status (Part D.3) -- a partial
    batch stays partial and nothing becomes contactable by being scored.

    The response's run counts describe THIS run; `summary` is the batch's
    distinct-lead state afterwards (see GET /api/batches/{id}/fit-summary).
    """
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    lead_ids = list(
        session.execute(
            select(Lead.id).where(Lead.batch_id == batch_id).order_by(Lead.id)
        ).scalars()
    )
    scored = skipped = failed = 0
    for start in range(0, len(lead_ids), SCORE_CHUNK_SIZE):
        chunk_ids = lead_ids[start : start + SCORE_CHUNK_SIZE]
        leads = list(
            session.execute(select(Lead).where(Lead.id.in_(chunk_ids))).scalars()
        )
        chunk_scored = 0
        for _, response, error in score_leads(
            session, leads, persist=True, skip_unchanged=True
        ):
            if error is not None:
                failed += 1
            elif response is None:
                skipped += 1
            else:
                chunk_scored += 1
        try:
            session.commit()
        except Exception:  # a failed chunk must not be reported as scored
            session.rollback()
            failed += chunk_scored
        else:
            scored += chunk_scored

    return BatchFitScoreRunSummary(
        batch_id=batch_id,
        attempted=len(lead_ids),
        newly_scored=scored,
        skipped_unchanged=skipped,
        failed=failed,
        summary=BatchFitSummary(**fit_queries.batch_fit_summary(session, batch_id)),
    )


@router.get(
    "/api/batches/{batch_id}/fit-summary",
    response_model=BatchFitSummary,
)
def get_batch_fit_summary(
    batch_id: UUID, session: Session = Depends(get_session)
) -> BatchFitSummary:
    """Distinct-lead band counts from each lead's latest applicable score --
    rescoring never inflates these."""
    if session.get(LeadBatch, batch_id) is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return BatchFitSummary(**fit_queries.batch_fit_summary(session, batch_id))


@router.get(
    "/api/batches/{batch_id}/readiness",
    response_model=Page[CurrentReadinessResponse],
)
def list_batch_readiness(
    batch_id: UUID,
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[CurrentReadinessResponse]:
    """Current readiness/eligibility for EVERY lead on one page of the
    batch (scored or not), same order as GET /api/leads?batch_id=... --
    three queries per page (leads, latest drafts, their latest reviews)."""
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    limit, offset = pagination
    total = session.scalar(
        select(func.count()).select_from(Lead).where(Lead.batch_id == batch_id)
    ) or 0
    leads = list(
        session.execute(
            select(Lead)
            .where(Lead.batch_id == batch_id)
            .order_by(Lead.created_at.desc(), Lead.id.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    by_lead = fit_queries.current_readiness_by_lead(session, leads)
    items = [
        CurrentReadinessResponse(
            lead_id=lead.id,
            readiness=by_lead[lead.id][0].to_dict(),
            eligibility=by_lead[lead.id][1].to_dict(),
        )
        for lead in leads
    ]
    return Page[CurrentReadinessResponse](
        items=items, total=total, limit=limit, offset=offset,
        has_more=(offset + len(leads)) < total,
    )


@router.get(
    "/api/batches/{batch_id}/fit-scores",
    response_model=Page[LeadFitScoreResponse],
)
def list_batch_fit_scores(
    batch_id: UUID,
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[LeadFitScoreResponse]:
    """Paginated bulk lookup over the batch's leads, in the same order as
    GET /api/leads?batch_id=... (created_at DESC, id DESC). Pagination is
    over LEADS, so `total` is the batch's lead count and a page's `items`
    omits leads on that page that have never been fit-scored. A constant
    number of queries per page: leads, latest fit rows, latest drafts,
    their latest reviews. Readiness/eligibility in each item are current.
    """
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    limit, offset = pagination
    lead_stmt = (
        select(Lead)
        .where(Lead.batch_id == batch_id)
        .order_by(Lead.created_at.desc(), Lead.id.desc())
    )
    total = session.scalar(
        select(func.count()).select_from(Lead).where(Lead.batch_id == batch_id)
    ) or 0
    leads = list(session.execute(lead_stmt.limit(limit).offset(offset)).scalars())

    scores_by_lead = fit_queries.latest_fit_scores_by_lead(session, [lead.id for lead in leads])
    scored_leads = [lead for lead in leads if lead.id in scores_by_lead]
    readiness_by_lead = fit_queries.current_readiness_by_lead(session, scored_leads)

    items: list[LeadFitScoreResponse] = []
    for lead in scored_leads:
        readiness, eligibility = readiness_by_lead[lead.id]
        items.append(
            fit_score_response(
                lead=lead,
                row=scores_by_lead[lead.id],
                fit_result=None,
                readiness=readiness,
                eligibility=eligibility,
            )
        )

    return Page[LeadFitScoreResponse](
        items=items, total=total, limit=limit, offset=offset,
        has_more=(offset + len(leads)) < total,
    )
