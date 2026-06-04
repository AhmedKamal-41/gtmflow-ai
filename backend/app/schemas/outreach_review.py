from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class RejectOutreachRequest(BaseModel):
    """Body for POST /api/leads/{id}/reject-outreach."""

    reason: str | None = None


class OutreachReviewResponse(BaseModel):
    """Response for POST approve/reject endpoints."""

    lead_id: UUID
    ai_output_id: UUID
    event_type: str
    message: str
