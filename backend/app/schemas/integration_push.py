from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    # Phase 11 delivery ledger (None on rows from before Phase 11).
    approved_output_id: UUID | None = None
    approved_content_hash: str | None = None
    attempt: int | None = None
    delivery_mode: str | None = None
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    outcome_code: str | None = None
    resolution: str | None = None
    resolution_note: str | None = None
    resolved_at: datetime | None = None
    # Set on push responses: True when an earlier delivery of the same
    # approved draft was returned and nothing was sent.
    replay: bool = False


class PushRequest(BaseModel):
    """Body shape for POST /api/leads/{id}/push and /api/batches/{id}/push-hot.

    ``force`` bypasses only the Hot-score threshold (single lead) and, on the
    batch route, re-pushes leads already delivered (unchanged behaviour).
    ``redeliver`` (single lead, Phase 11) explicitly sends an already
    delivered approved draft again; without it a repeat push is a replay."""

    integration_type: str = "slack"
    force: bool = False
    redeliver: bool = False


class DeliveryResolution(BaseModel):
    """Phase 11: an operator's finding for a delivery whose outcome is unknown."""

    model_config = ConfigDict(extra="forbid")

    resolution: Literal["confirmed_delivered", "confirmed_not_delivered"]
    note: str | None = Field(default=None, max_length=500)


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
    blocked: int = 0
    # Phase 11: leads not sent because a delivery is in progress or its
    # outcome is unknown (never resent automatically).
    uncertain: int = 0
    results: list[BatchPushResult]
