from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IntegrationPushBase(BaseModel):
    integration_type: str
    payload: dict[str, Any]
    status: str
    response_text: str | None = None


class IntegrationPushCreate(IntegrationPushBase):
    lead_id: UUID


class IntegrationPushRead(IntegrationPushBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    created_at: datetime


class PushRequest(BaseModel):
    """Body shape for POST /api/leads/{id}/push and /api/batches/{id}/push-hot."""

    integration_type: str = "slack"
    force: bool = False


class BatchPushResult(BaseModel):
    lead_id: UUID
    company_name: str
    status: str
    push_id: UUID | None = None
    reason: str | None = None


class BatchPushSummary(BaseModel):
    batch_id: UUID
    hot_leads_found: int
    pushed: int
    skipped: int
    failed: int
    results: list[BatchPushResult]
