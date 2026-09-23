from __future__ import annotations

from uuid import UUID

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApproveOutreachRequest(BaseModel):
    """Body for POST /api/leads/{id}/approve-outreach.

    ``ai_output_id`` is required: the caller must say exactly which draft it
    is approving. This is what makes a stale browser tab unable to silently
    approve a different (newer) draft than the one it has rendered -- see
    docs/upgrade/audit.md C.2.

    Phase 6: ``content_hash`` is the hash of the exact content displayed
    (``AIOutputRead.content_hash``). Extra fields -- e.g. a posted reviewer
    name -- are rejected: identity is the fixed unauthenticated label.
    """

    model_config = ConfigDict(extra="forbid")

    ai_output_id: UUID
    content_hash: str = Field(min_length=64, max_length=64)


class RejectOutreachRequest(BaseModel):
    """Body for POST /api/leads/{id}/reject-outreach. A rejection must say
    why (Phase 6)."""

    model_config = ConfigDict(extra="forbid")

    ai_output_id: UUID
    content_hash: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


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


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ai_output_id: UUID | None
    decision: str
    reason: str | None
    reviewer_label: str
    content_hash: str | None
    legacy_unlinked: bool
    created_at: datetime


class SourceInfo(BaseModel):
    """Where the company facts came from and how fresh they can be."""

    batch_source: str | None
    provider: str | None = None
    source_snapshot_id: UUID | None = None
    reported_acquisition_date: str | None = None
    retrieved_at: datetime | None = None
    license: str | None = None
    freshness_note: str


class ReviewStateRead(BaseModel):
    """The review workspace's single source of truth for one lead."""

    lead_id: UUID
    status: str  # no_draft | pending | approved | rejected
    draft_id: UUID | None
    draft_content_hash: str | None
    draft_origin: str | None
    draft_parent_output_id: UUID | None
    approval_applicable: bool
    delivery_blockers: list[str]
    email_blockers: list[str]
    blocker_explanations: dict[str, str]
    latest_review: ReviewRead | None
    source: SourceInfo
