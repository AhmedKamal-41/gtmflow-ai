"""API surface for lead ranking against the active seller profile's target
customers (app/scoring/fit.py). Ranking never mutates Lead.status /
LeadBatch.status / AIOutputReview, never generates or approves a draft,
never sends.

Every read of "the current ranking" or "current readiness" goes through
app/services/fit_queries.py, so single-lead, bulk, summary, list, inbox and
delivery reads can't disagree about which stored row is current.

When no seller profile is active, or the active one states no target
customers, ranking endpoints answer 409 with the setup message -- they
never fall back to another rubric.

Two kinds of readiness appear in responses, deliberately kept apart:

* `readiness` / `eligibility` -- CURRENT, recomputed from live lead,
  batch, draft, and review state on every read.
* `at_scoring` -- the HISTORICAL snapshot stored with the score row.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Lead, LeadBatch, WorkflowEvent
from app.schemas.lead_fit_score import (
    BatchFitScoreRunSummary,
    BatchFitSummary,
    CurrentReadinessResponse,
    LeadFitScoreResponse,
    RankingRunSummary,
    RankingStatusResponse,
)
from app.schemas.pagination import Page
from app.scoring import fit
from app.services import fit_queries
from app.services.fit_scoring import fit_score_response, rank_in_chunks, score_leads
from app.services.pagination import pagination_params

router = APIRouter(tags=["fit-scoring"])

STATE_MESSAGES = {
    fit_queries.RANKING_READY: "Leads are ranked against the active seller profile's target customers.",
    fit_queries.RANKING_NO_PROFILE: (
        "No seller profile is active. Describe what you sell and who you want as customers, "
        "then activate the profile to rank leads."
    ),
    fit_queries.RANKING_SETUP_REQUIRED: (
        "The active seller profile has no target customers. Add industries, locations, company "
        "sizes, contact roles or keywords, then save and activate it to rank leads."
    ),
}


def _setup_required(error: fit.RankingSetupRequired) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


@router.post(
    "/api/leads/{lead_id}/fit-score",
    response_model=LeadFitScoreResponse,
    status_code=status.HTTP_201_CREATED,
)
def score_lead_fit(lead_id: UUID, session: Session = Depends(get_session)) -> LeadFitScoreResponse:
    """Ranks the lead against the active profile and PERSISTS a new row.
    Ranking again never mutates or replaces a prior row."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    try:
        [(_, response, error)] = score_leads(session, [lead], persist=True)
    except fit.RankingSetupRequired as e:
        raise _setup_required(e) from None
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
    """The lead's current ranking (`is_current` true). If it was only ever
    ranked against other target preferences, its latest such ranking is
    returned with `is_current` false -- shown as outdated, never used as
    current. 404 when it was never ranked."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    profile = fit_queries.current_ranking_profile(session)
    stored = fit_queries.latest_fit_score(session, lead_id, profile)
    if stored is None:
        stored = fit_queries.latest_any_fit_scores_by_lead(session, [lead_id]).get(lead_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Lead has not been ranked yet")
    readiness, eligibility = fit_queries.current_readiness(session, lead)
    return fit_score_response(
        lead=lead, row=stored, fit_result=None, readiness=readiness, eligibility=eligibility,
        current=profile,
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


@router.get("/api/scoring/profile", response_model=RankingStatusResponse)
def get_ranking_profile(session: Session = Depends(get_session)) -> RankingStatusResponse:
    """What leads are ranked against right now, the fixed rules (weights and
    thresholds), and how many leads are ranked, outdated or unranked."""
    profile = fit_queries.current_ranking_profile(session)
    state = fit_queries.ranking_state(profile)
    counts = fit_queries.ranking_counts(session, profile)
    return RankingStatusResponse(
        state=state,
        message=STATE_MESSAGES[state],
        profile=profile.describe() if profile is not None else None,
        scorer_version=fit.SCORER_VERSION,
        normalization_version=fit.NORMALIZATION_VERSION,
        weights=fit.WEIGHTS,
        labels=fit.LABELS,
        hot_min=fit.HOT_MIN,
        warm_min=fit.WARM_MIN,
        coverage_min_pct=fit.COVERAGE_MIN_PCT,
        partial_credit=fit.PARTIAL_CREDIT,
        total_leads=counts["total_leads"],
        current_rankings=counts["current"],
        outdated_rankings=counts["outdated"],
        never_ranked=counts["never_ranked"],
    )


@router.post("/api/scoring/recalculate", response_model=RankingRunSummary)
def recalculate_rankings(session: Session = Depends(get_session)) -> RankingRunSummary:
    """Rank every lead in the workspace against the active profile, in
    committed chunks. Leads already ranked with identical inputs are
    skipped, so it is safe to repeat; earlier rankings are kept as history."""
    try:
        summary = rank_in_chunks(session)
    except fit.RankingSetupRequired as e:
        raise _setup_required(e) from None
    session.add(WorkflowEvent(event_type="rankings_recalculated", event_data=summary))
    session.commit()
    return RankingRunSummary(**summary)


@router.post(
    "/api/batches/{batch_id}/fit-score",
    response_model=BatchFitScoreRunSummary,
    status_code=status.HTTP_200_OK,
)
def score_batch_fit(batch_id: UUID, session: Session = Depends(get_session)) -> BatchFitScoreRunSummary:
    """Ranks every lead in the batch against the active profile, one
    committed transaction per chunk. Leads whose latest current row has the
    same input fingerprint are skipped (reported, not ranked again). Never
    touches LeadBatch.status or Lead.status -- a partial batch stays
    partial and nothing becomes contactable by being ranked."""
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    try:
        run = rank_in_chunks(session, batch_id=batch_id)
    except fit.RankingSetupRequired as e:
        raise _setup_required(e) from None
    return BatchFitScoreRunSummary(
        batch_id=batch_id,
        profile_id=run["profile_id"],
        profile_version=run["profile_version"],
        attempted=run["attempted"],
        newly_scored=run["newly_scored"],
        skipped_unchanged=run["skipped_unchanged"],
        failed=run["failed"],
        summary=BatchFitSummary(**fit_queries.batch_fit_summary(session, batch_id)),
    )


@router.get(
    "/api/batches/{batch_id}/fit-summary",
    response_model=BatchFitSummary,
)
def get_batch_fit_summary(
    batch_id: UUID, session: Session = Depends(get_session)
) -> BatchFitSummary:
    """Distinct-lead band counts from each lead's latest current ranking --
    ranking again never inflates these."""
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
    omits leads on that page with no current ranking. A constant
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

    profile = fit_queries.current_ranking_profile(session)
    scores_by_lead = fit_queries.latest_fit_scores_by_lead(session, [lead.id for lead in leads], profile)
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
                current=profile,
            )
        )

    return Page[LeadFitScoreResponse](
        items=items, total=total, limit=limit, offset=offset,
        has_more=(offset + len(leads)) < total,
    )
