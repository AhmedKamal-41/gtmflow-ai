from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class ApproveOutreachRequest(BaseModel):
    """Body for POST /api/leads/{id}/approve-outreach.

    ``ai_output_id`` is required: the caller must say exactly which draft it
    is approving. This is what makes a stale browser tab unable to silently
    approve a different (newer) draft than the one it has rendered -- see
    docs/upgrade/audit.md C.2.
    """

    ai_output_id: UUID


class RejectOutreachRequest(BaseModel):
    """Body for POST /api/leads/{id}/reject-outreach."""

    ai_output_id: UUID
    reason: str | None = None


class OutreachReviewResponse(BaseModel):
    """Response for POST approve/reject endpoints."""

    lead_id: UUID
    ai_output_id: UUID
    review_id: UUID
    event_type: str
    message: str
    # True when this call matched the current decision already on record for
    # this exact output and made no new change (idempotent retry).
    idempotent_replay: bool = False
