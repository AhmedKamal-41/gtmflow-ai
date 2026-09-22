"""Phase 4 Parts C/D/E: API surface for the v2 deterministic company-fit
scorer. Entirely additive -- does not call or alter anything in
app/api/scoring.py (the legacy v1 endpoints), and never mutates
Lead.status / LeadBatch.status / AIOutputReview (Part D.3).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import AIOutput, AIOutputReview, Lead, LeadBatch, LeadFitScore
from app.schemas.lead_fit_score import (
    BatchFitScoreSummary,
    FitProfileResponse,
    LeadFitScoreResponse,
)
from app.schemas.pagination import Page
from app.scoring import fit
from app.services.pagination import pagination_params, paginate

router = APIRouter(tags=["fit-scoring"])

OUTREACH_OUTPUT_TYPE = "outreach_email"


def _readiness_inputs(session: Session, lead: Lead) -> dict[str, Any]:
    """One query for the latest outreach draft, one for its latest review
    -- bounded per lead, not per-criterion."""
    latest_output = session.execute(
        select(AIOutput)
        .where(AIOutput.lead_id == lead.id, AIOutput.output_type == OUTREACH_OUTPUT_TYPE)
        .order_by(AIOutput.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    review_decision: str | None = None
    if latest_output is not None:
        latest_review = session.execute(
            select(AIOutputReview)
            .where(AIOutputReview.ai_output_id == latest_output.id)
            .order_by(AIOutputReview.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest_review is not None:
            review_decision = latest_review.decision

    return {
        "has_contact_email": bool(lead.contact_email),
        "latest_outreach_exists": latest_output is not None,
        "latest_outreach_review_decision": review_decision,
    }


def _score_and_optionally_persist(
    session: Session, lead: Lead, *, persist: bool
) -> LeadFitScoreResponse:
    """Freshly computed end to end -- readiness here is always current
    (this IS the computation), unlike the bulk batch listing endpoint
    below which reads a stored snapshot."""
    fit_result = fit.compute_fit(lead)
    readiness_inputs = _readiness_inputs(session, lead)
    eligibility = fit.compute_eligibility(lead)
    readiness = fit.compute_readiness(
        lead,
        eligibility_excluded=eligibility.excluded,
        **readiness_inputs,
    )

    row_id: UUID | None = None
    if persist:
        row = LeadFitScore(
            lead_id=lead.id,
            scorer_version=fit_result.scorer_version,
            profile_id=fit_result.profile_id,
            profile_version=fit_result.profile_version,
            normalization_version=fit_result.normalization_version,
            input_fingerprint=fit_result.input_fingerprint,
            fit_score=fit_result.fit_score,
            evidence_coverage_pct=fit_result.evidence_coverage_pct,
            band=fit_result.band,
            criteria=fit_result.criteria_as_dicts(),
            readiness=readiness.to_dict(),
            eligibility_excluded=eligibility.excluded,
            eligibility_reasons=eligibility.reasons,
            computed_at=fit_result.computed_at,
            computation_ms=fit_result.computation_ms,
        )
        session.add(row)
        session.flush()
        row_id = row.id

    return LeadFitScoreResponse(
        id=row_id,
        lead_id=lead.id,
        scorer_version=fit_result.scorer_version,
        profile_id=fit_result.profile_id,
        profile_version=fit_result.profile_version,
        normalization_version=fit_result.normalization_version,
        input_fingerprint=fit_result.input_fingerprint,
        fit_score=fit_result.fit_score,
        max_fit_score=fit_result.max_fit_score,
        evidence_coverage_pct=fit_result.evidence_coverage_pct,
        band=fit_result.band,
        criteria=fit_result.criteria_as_dicts(),
        readiness=readiness.to_dict(),
        readiness_is_current=True,
        eligibility=eligibility.to_dict(),
        computed_at=fit_result.computed_at,
        computation_ms=fit_result.computation_ms,
    )


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
    response = _score_and_optionally_persist(session, lead, persist=True)
    session.commit()
    return response


@router.get(
    "/api/leads/{lead_id}/fit-score",
    response_model=LeadFitScoreResponse,
)
def get_latest_lead_fit_score(
    lead_id: UUID, session: Session = Depends(get_session)
) -> LeadFitScoreResponse:
    """Returns the most recently PERSISTED fit/band/coverage/criteria for
    this lead's current profile version, exactly as they were computed --
    but recomputes readiness and eligibility fresh (Part D.4: a stored
    eligibility/readiness snapshot must not be trusted after the lead or
    its batch/outreach state has since changed)."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    stored = session.execute(
        select(LeadFitScore)
        .where(
            LeadFitScore.lead_id == lead_id,
            LeadFitScore.scorer_version == fit.SCORER_VERSION,
            LeadFitScore.profile_id == fit.PROFILE_ID,
            LeadFitScore.profile_version == fit.PROFILE_VERSION,
        )
        .order_by(LeadFitScore.created_at.desc(), LeadFitScore.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if stored is None:
        raise HTTPException(status_code=404, detail="Lead has not been fit-scored yet")

    readiness_inputs = _readiness_inputs(session, lead)
    eligibility = fit.compute_eligibility(lead)
    readiness = fit.compute_readiness(
        lead, eligibility_excluded=eligibility.excluded, **readiness_inputs
    )

    return LeadFitScoreResponse(
        id=stored.id,
        lead_id=lead.id,
        scorer_version=stored.scorer_version,
        profile_id=stored.profile_id,
        profile_version=stored.profile_version,
        normalization_version=stored.normalization_version,
        input_fingerprint=stored.input_fingerprint,
        fit_score=stored.fit_score,
        max_fit_score=fit.MAX_FIT_SCORE,
        evidence_coverage_pct=stored.evidence_coverage_pct,
        band=stored.band,
        criteria=stored.criteria,
        readiness=readiness.to_dict(),
        readiness_is_current=True,
        eligibility=eligibility.to_dict(),
        computed_at=stored.computed_at,
        computation_ms=stored.computation_ms,
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
    response_model=BatchFitScoreSummary,
    status_code=status.HTTP_200_OK,
)
def score_batch_fit(batch_id: UUID, session: Session = Depends(get_session)) -> BatchFitScoreSummary:
    """Scores every lead in the batch and persists one row each. Bounded to
    this batch's leads. Deliberately does NOT touch LeadBatch.status or
    Lead.status (Part D.3) -- a batch stuck 'partial' stays 'partial', and
    scoring never marks anything contactable."""
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    leads = list(
        session.execute(select(Lead).where(Lead.batch_id == batch_id)).scalars().all()
    )

    counts = {
        fit.BAND_STRONG: 0,
        fit.BAND_PARTIAL: 0,
        fit.BAND_WEAK: 0,
        fit.BAND_INSUFFICIENT_EVIDENCE: 0,
    }
    score_sum = 0
    for lead in leads:
        response = _score_and_optionally_persist(session, lead, persist=True)
        score_sum += response.fit_score
        counts[response.band] += 1

    session.commit()
    average = round(score_sum / len(leads), 1) if leads else 0.0

    return BatchFitScoreSummary(
        batch_id=batch_id,
        scored_leads=len(leads),
        strong_match=counts[fit.BAND_STRONG],
        partial_match=counts[fit.BAND_PARTIAL],
        weak_match=counts[fit.BAND_WEAK],
        insufficient_evidence=counts[fit.BAND_INSUFFICIENT_EVIDENCE],
        average_fit_score=average,
    )


def _latest_fit_scores_by_lead(
    session: Session, lead_ids: list[UUID]
) -> dict[UUID, LeadFitScore]:
    """One query for however many leads are on the current page -- the
    bounded bulk lookup Part E asks for instead of one fit-score request
    per lead. Picks each lead's most recent row for the CURRENT profile
    version via a ROW_NUMBER window, not a correlated subquery per lead."""
    if not lead_ids:
        return {}

    rn = (
        func.row_number()
        .over(
            partition_by=LeadFitScore.lead_id,
            order_by=(LeadFitScore.created_at.desc(), LeadFitScore.id.desc()),
        )
        .label("rn")
    )
    ranked = (
        select(LeadFitScore, rn)
        .where(
            LeadFitScore.lead_id.in_(lead_ids),
            LeadFitScore.scorer_version == fit.SCORER_VERSION,
            LeadFitScore.profile_id == fit.PROFILE_ID,
            LeadFitScore.profile_version == fit.PROFILE_VERSION,
        )
        .subquery()
    )
    stmt = select(LeadFitScore).select_from(ranked).join(
        LeadFitScore, LeadFitScore.id == ranked.c.id
    ).where(ranked.c.rn == 1)
    rows = session.execute(stmt).scalars().all()
    return {row.lead_id: row for row in rows}


@router.get(
    "/api/batches/{batch_id}/fit-scores",
    response_model=Page[LeadFitScoreResponse],
)
def list_batch_fit_scores(
    batch_id: UUID,
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[LeadFitScoreResponse]:
    """Paginated, bounded bulk lookup of each lead's latest fit score in a
    batch (Part E) -- one query for the page's fit rows, not N requests."""
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    limit, offset = pagination
    lead_stmt = (
        select(Lead)
        .where(Lead.batch_id == batch_id)
        .order_by(Lead.created_at.desc(), Lead.id.desc())
    )
    total = session.scalar(select(func.count()).select_from(lead_stmt.subquery())) or 0
    leads = session.execute(lead_stmt.limit(limit).offset(offset)).scalars().all()

    scores_by_lead = _latest_fit_scores_by_lead(session, [lead.id for lead in leads])

    items: list[LeadFitScoreResponse] = []
    for lead in leads:
        stored = scores_by_lead.get(lead.id)
        if stored is None:
            continue
        # Eligibility is recomputed fresh per lead (Part D.4) -- cheap here
        # because `lead.batch` resolves from the session identity map
        # (already loaded once above as `batch`) rather than a new query,
        # so this does not reintroduce N+1. `readiness` is shown as stored
        # (may lag if outreach/review state changed since scoring) --
        # `readiness_is_current=False` makes that explicit to callers
        # instead of leaving them to assume it's live. Opening the lead
        # individually via GET /api/leads/{id}/fit-score always recomputes
        # readiness fresh (readiness_is_current=True there).
        eligibility = fit.compute_eligibility(lead)
        items.append(
            LeadFitScoreResponse(
                id=stored.id,
                lead_id=lead.id,
                scorer_version=stored.scorer_version,
                profile_id=stored.profile_id,
                profile_version=stored.profile_version,
                normalization_version=stored.normalization_version,
                input_fingerprint=stored.input_fingerprint,
                fit_score=stored.fit_score,
                max_fit_score=fit.MAX_FIT_SCORE,
                evidence_coverage_pct=stored.evidence_coverage_pct,
                band=stored.band,
                criteria=stored.criteria,
                readiness=stored.readiness,
                readiness_is_current=False,
                eligibility=eligibility.to_dict(),
                computed_at=stored.computed_at,
                computation_ms=stored.computation_ms,
            )
        )

    return Page[LeadFitScoreResponse](
        items=items, total=total, limit=limit, offset=offset,
        has_more=(offset + len(leads)) < total,
    )
