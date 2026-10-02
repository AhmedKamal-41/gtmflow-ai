from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Lead, LeadBatch, LeadScore, WorkflowEvent
from app.models.lead_batch import INCOMPLETE_BATCH_STATUSES
from app.schemas.lead_score import BatchScoreSummary, LeadScoreResponse
from app.schemas.pagination import Page
from app.scoring.lead_scoring import DISQUALIFIED_STATUSES, score_lead
from app.services.legacy_scoring import apply_legacy_score as _apply_score
from app.services.pagination import pagination_params

router = APIRouter(tags=["scoring"])


def _response_from_result(lead_id: UUID, result: dict[str, Any]) -> LeadScoreResponse:
    return LeadScoreResponse(
        lead_id=lead_id,
        total_score=result["total_score"],
        priority=result["priority"],
        score_breakdown=result["score_breakdown"],
        matched_signals=result["matched_signals"],
        reasoning=result["reasoning"],
    )


@router.post(
    "/api/leads/{lead_id}/score",
    response_model=LeadScoreResponse,
    status_code=status.HTTP_200_OK,
)
def score_one_lead(
    lead_id: UUID,
    session: Session = Depends(get_session),
) -> LeadScoreResponse:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    result = _apply_score(session, lead)
    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type="lead_scored",
            event_data={
                "total_score": result["total_score"],
                "priority": result["priority"],
            },
        )
    )
    session.commit()
    return _response_from_result(lead_id, result)


@router.get(
    "/api/leads/{lead_id}/score",
    response_model=LeadScoreResponse,
)
def get_lead_score(
    lead_id: UUID,
    session: Session = Depends(get_session),
) -> LeadScoreResponse:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.score is None:
        raise HTTPException(status_code=404, detail="Lead has not been scored yet")

    return _response_from_stored(lead.score)


def _response_from_stored(score: LeadScore) -> LeadScoreResponse:
    stored = dict(score.score_breakdown or {})
    matched = stored.pop(
        "matched_signals",
        {"industry_terms": [], "title_terms": [], "pain_point_terms": []},
    )
    return LeadScoreResponse(
        lead_id=score.lead_id,
        total_score=score.total_score,
        priority=score.priority,
        score_breakdown=stored,
        matched_signals=matched,
        reasoning=score.reasoning or "",
    )


@router.get(
    "/api/batches/{batch_id}/scores",
    response_model=Page[LeadScoreResponse],
)
def list_batch_scores(
    batch_id: UUID,
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[LeadScoreResponse]:
    """Legacy v1 scores for one page of the batch's leads, in the same order
    as GET /api/leads?batch_id=... (created_at DESC, id DESC) -- one bounded
    lookup per page instead of one GET /api/leads/{id}/score per lead.
    Pagination is over leads: `total` is the batch's lead count and leads
    on the page that were never scored are simply absent from `items`."""
    if session.get(LeadBatch, batch_id) is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    limit, offset = pagination
    total = session.scalar(
        select(func.count()).select_from(Lead).where(Lead.batch_id == batch_id)
    ) or 0
    page_ids = list(
        session.execute(
            select(Lead.id)
            .where(Lead.batch_id == batch_id)
            .order_by(Lead.created_at.desc(), Lead.id.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    scores: dict[UUID, LeadScore] = {}
    if page_ids:
        scores = {
            row.lead_id: row
            for row in session.execute(
                select(LeadScore).where(LeadScore.lead_id.in_(page_ids))
            ).scalars()
        }
    items = [_response_from_stored(scores[i]) for i in page_ids if i in scores]
    return Page[LeadScoreResponse](
        items=items, total=total, limit=limit, offset=offset,
        has_more=(offset + len(page_ids)) < total,
    )


@router.post(
    "/api/batches/{batch_id}/score",
    response_model=BatchScoreSummary,
    status_code=status.HTTP_200_OK,
)
def score_one_batch(
    batch_id: UUID,
    session: Session = Depends(get_session),
) -> BatchScoreSummary:
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    leads = list(
        session.execute(select(Lead).where(Lead.batch_id == batch_id)).scalars().all()
    )

    hot = warm = cold = 0
    score_sum = 0
    for lead in leads:
        result = _apply_score(session, lead)
        score_sum += result["total_score"]
        band = result["priority"]
        if band == "Hot":
            hot += 1
        elif band == "Warm":
            warm += 1
        else:
            cold += 1

    # "partial"/"uploading" is a disposition (this batch's CSV import didn't fully
    # commit), not a workflow stage scoring should overwrite -- same
    # invariant already applied to blocked Lead.status (see
    # app/api/scoring.py's _apply_score, app/api/outreach_review.py).
    # Scoring the rows that DID commit is still fine and still happens
    # above; only the batch-level status flag is protected here.
    if leads and batch.status not in INCOMPLETE_BATCH_STATUSES:
        batch.status = "scored"
    average = round(score_sum / len(leads), 1) if leads else 0.0

    session.add(
        WorkflowEvent(
            batch_id=batch.id,
            event_type="batch_scored",
            event_data={
                "scored_leads": len(leads),
                "hot": hot,
                "warm": warm,
                "cold": cold,
                "average_score": average,
            },
        )
    )
    session.commit()

    return BatchScoreSummary(
        batch_id=batch_id,
        scored_leads=len(leads),
        hot=hot,
        warm=warm,
        cold=cold,
        average_score=average,
    )
