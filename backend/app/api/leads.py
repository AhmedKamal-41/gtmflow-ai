from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Lead
from app.schemas.lead import LeadRead

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.get("", response_model=list[LeadRead])
def list_leads(
    batch_id: UUID | None = None,
    session: Session = Depends(get_session),
) -> list[Lead]:
    stmt = select(Lead).order_by(Lead.created_at.desc())
    if batch_id is not None:
        stmt = stmt.where(Lead.batch_id == batch_id)
    return list(session.execute(stmt).scalars().all())


@router.get("/{lead_id}", response_model=LeadRead)
def get_lead(lead_id: UUID, session: Session = Depends(get_session)) -> Lead:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead
