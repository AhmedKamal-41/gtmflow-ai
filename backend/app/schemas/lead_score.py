from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LeadScoreBase(BaseModel):
    total_score: int
    priority: str
    score_breakdown: dict[str, Any] | None = None
    reasoning: str | None = None


class LeadScoreCreate(LeadScoreBase):
    lead_id: UUID


class LeadScoreRead(LeadScoreBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    created_at: datetime
    updated_at: datetime


class LeadScoreResponse(BaseModel):
    """API response shape for /api/leads/{id}/score (POST and GET)."""

    lead_id: UUID
    total_score: int
    priority: str
    score_breakdown: dict[str, Any]
    matched_signals: dict[str, list[str]]
    reasoning: str


class BatchScoreSummary(BaseModel):
    """API response shape for POST /api/batches/{id}/score."""

    batch_id: UUID
    scored_leads: int
    hot: int
    warm: int
    cold: int
    average_score: float
