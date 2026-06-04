from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Lead, LeadBatch, LeadScore, WorkflowEvent
from app.schemas.lead_score import BatchScoreSummary, LeadScoreResponse
from app.scoring.lead_scoring import score_lead

router = APIRouter(tags=["scoring"])


def _apply_score(session: Session, lead: Lead) -> dict[str, Any]:
    """Run the scorer, upsert LeadScore, mark the lead as scored.

    matched_signals is merged into score_breakdown JSON for persistence;
    the API response splits them back out at the top level.
    """
    result = score_lead(lead)
    persisted_breakdown = {
        **result["score_breakdown"],
        "matched_signals": result["matched_signals"],
    }
    if lead.score is None:
        lead.score = LeadScore(
            lead_id=lead.id,
            total_score=result["total_score"],
            priority=result["priority"],
            score_breakdown=persisted_breakdown,
            reasoning=result["reasoning"],
        )
    else:
        lead.score.total_score = result["total_score"]
        lead.score.priority = result["priority"]
        lead.score.score_breakdown = persisted_breakdown
        lead.score.reasoning = result["reasoning"]
    lead.status = "scored"
    return result


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

    stored = dict(lead.score.score_breakdown or {})
    matched = stored.pop(
        "matched_signals",
        {"industry_terms": [], "title_terms": [], "pain_point_terms": []},
    )
    return LeadScoreResponse(
        lead_id=lead_id,
        total_score=lead.score.total_score,
        priority=lead.score.priority,
        score_breakdown=stored,
        matched_signals=matched,
        reasoning=lead.score.reasoning or "",
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

    if leads:
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
