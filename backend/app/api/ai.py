from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import AIOutput, Lead
from app.schemas.ai_output import AIOutputRead
from app.services.ai_generation import (
    generate_outreach_for_lead,
    generate_summary_for_lead,
)

router = APIRouter(prefix="/api/leads", tags=["ai"])


@router.post(
    "/{lead_id}/generate-summary",
    response_model=AIOutputRead,
    status_code=status.HTTP_200_OK,
)
def post_generate_summary(
    lead_id: UUID,
    session: Session = Depends(get_session),
) -> AIOutput:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    output = generate_summary_for_lead(session, lead)
    session.commit()
    session.refresh(output)
    return output


@router.post(
    "/{lead_id}/generate-outreach",
    response_model=AIOutputRead,
    status_code=status.HTTP_200_OK,
)
def post_generate_outreach(
    lead_id: UUID,
    session: Session = Depends(get_session),
) -> AIOutput:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    output = generate_outreach_for_lead(session, lead)
    session.commit()
    session.refresh(output)
    return output


@router.get(
    "/{lead_id}/ai-outputs",
    response_model=list[AIOutputRead],
)
def list_lead_ai_outputs(
    lead_id: UUID,
    session: Session = Depends(get_session),
) -> list[AIOutput]:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    rows = (
        session.execute(
            select(AIOutput)
            .where(AIOutput.lead_id == lead_id)
            .order_by(AIOutput.created_at.desc())
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get(
    "/{lead_id}/latest-ai-output",
    response_model=AIOutputRead,
)
def get_latest_ai_output(
    lead_id: UUID,
    output_type: str = Query(
        ...,
        description="company_summary or outreach_email",
    ),
    session: Session = Depends(get_session),
) -> AIOutput:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    row = session.execute(
        select(AIOutput)
        .where(
            AIOutput.lead_id == lead_id,
            AIOutput.output_type == output_type,
        )
        .order_by(AIOutput.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No AI output of type '{output_type}' for this lead",
        )
    return row
