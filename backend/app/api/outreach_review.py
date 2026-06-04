from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import AIOutput, Lead, WorkflowEvent
from app.schemas.outreach_review import (
    OutreachReviewResponse,
    RejectOutreachRequest,
)

OUTREACH_OUTPUT_TYPE = "outreach_email"
APPROVED_EVENT = "outreach_approved"
REJECTED_EVENT = "outreach_rejected"
APPROVED_STATUS = "outreach_approved"
REJECTED_STATUS = "outreach_rejected"

router = APIRouter(tags=["outreach-review"])


def _latest_outreach(session: Session, lead_id: UUID) -> AIOutput | None:
    return session.execute(
        select(AIOutput)
        .where(
            AIOutput.lead_id == lead_id,
            AIOutput.output_type == OUTREACH_OUTPUT_TYPE,
        )
        .order_by(AIOutput.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _require_lead_and_outreach(
    session: Session, lead_id: UUID
) -> tuple[Lead, AIOutput]:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    latest = _latest_outreach(session, lead_id)
    if latest is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No outreach output exists for this lead. Generate outreach "
                "before approving or rejecting."
            ),
        )
    return lead, latest


@router.post(
    "/api/leads/{lead_id}/approve-outreach",
    response_model=OutreachReviewResponse,
    status_code=status.HTTP_200_OK,
)
def approve_outreach(
    lead_id: UUID,
    session: Session = Depends(get_session),
) -> OutreachReviewResponse:
    lead, latest = _require_lead_and_outreach(session, lead_id)

    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type=APPROVED_EVENT,
            event_data={
                "ai_output_id": str(latest.id),
                "output_type": OUTREACH_OUTPUT_TYPE,
                "lead_id": str(lead.id),
            },
        )
    )
    lead.status = APPROVED_STATUS
    session.commit()

    return OutreachReviewResponse(
        lead_id=lead.id,
        ai_output_id=latest.id,
        event_type=APPROVED_EVENT,
        message="Outreach approved.",
    )


@router.post(
    "/api/leads/{lead_id}/reject-outreach",
    response_model=OutreachReviewResponse,
    status_code=status.HTTP_200_OK,
)
def reject_outreach(
    lead_id: UUID,
    request: RejectOutreachRequest,
    session: Session = Depends(get_session),
) -> OutreachReviewResponse:
    lead, latest = _require_lead_and_outreach(session, lead_id)

    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type=REJECTED_EVENT,
            event_data={
                "ai_output_id": str(latest.id),
                "output_type": OUTREACH_OUTPUT_TYPE,
                "lead_id": str(lead.id),
                "reason": request.reason,
            },
        )
    )
    lead.status = REJECTED_STATUS
    session.commit()

    return OutreachReviewResponse(
        lead_id=lead.id,
        ai_output_id=latest.id,
        event_type=REJECTED_EVENT,
        message="Outreach rejected.",
    )
