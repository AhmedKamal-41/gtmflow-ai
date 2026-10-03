"""The rep's workspace: the lead inbox and which model drafts right now."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.status import ai_status
from app.core.database import get_session
from app.services.inbox import query_inbox
from app.services.pagination import pagination_params

router = APIRouter(prefix="/api", tags=["workspace"])

Stage = Literal["needs_score", "needs_draft", "to_review", "outdated", "rejected", "approved", "sent"]


class InboxItem(BaseModel):
    id: UUID
    company_name: str
    contact_name: str | None
    contact_title: str | None
    industry: str | None
    batch_id: UUID
    batch_name: str | None
    priority: Literal["Hot", "Warm", "Cold"] | None
    score: int | None
    stage: Stage
    delivery_unknown: bool
    draft_model: str | None
    blocked: bool
    updated_at: datetime


class InboxPage(BaseModel):
    items: list[InboxItem]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


class AIStatus(BaseModel):
    mode: Literal["mock", "fine_tuned", "openai", "unavailable"]
    label: str
    detail: str
    fine_tuned_selected: bool
    fine_tuned_connected: bool
    fallback_enabled: bool
    base_model: str
    adapter: str


@router.get("/inbox", response_model=InboxPage)
def get_inbox(
    stage: Stage | None = None,
    priority: Literal["Hot", "Warm", "Cold"] | None = None,
    q: str | None = Query(None, max_length=100),
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> dict:
    """Every lead with its priority and workflow stage, Hot first. Stages:
    needs_score, needs_draft, to_review, outdated, rejected, approved, sent."""
    limit, offset = pagination
    return query_inbox(session, stage=stage, priority=priority, q=q, limit=limit, offset=offset)


@router.get("/ai/status", response_model=AIStatus)
async def get_ai_status() -> dict:
    """Which model writes drafts right now, and whether the fine-tuned
    model's server is reachable (checked with a short, cached probe)."""
    return await run_in_threadpool(ai_status)
