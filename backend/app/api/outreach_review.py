"""Outreach draft review: approve / reject the exact draft and content shown.

Every approve/reject call names the ``ai_output_id`` and the
``content_hash`` of the content displayed. The shared service
(app/services/draft_review.py) enforces, in order: the output exists; it
belongs to this lead; it is an operational outreach draft (not a summary or
a training-annotation candidate); its content is what was displayed; and no
newer draft supersedes it. An identical repeat is an idempotent no-op; a
changed decision (approve -> reject) is its own row and event.

approval_rate can still exceed 100% on approve -> reject -> approve; the
metric redesign is Phase 11 scope (docs/upgrade/decisions.md).

Reviewer identity is the fixed, honestly unauthenticated label
``local-demo-unauthenticated``; request bodies can't supply a name.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import AIOutputReview, Lead
from app.models.ai_output_review import REVIEW_KIND_OPERATIONAL_OUTREACH
from app.schemas.outreach_review import (
    ApproveOutreachRequest,
    OutreachReviewResponse,
    RejectOutreachRequest,
    ReviewRead,
    ReviewStateRead,
    SourceInfo,
)
from app.schemas.pagination import Page
from app.scoring import fit
from app.services.draft_review import (
    DECISION_APPROVED,
    DECISION_REJECTED,
    REVIEWER_LABEL,  # noqa: F401 -- kept importable for existing callers
    ReviewError,
    apply_review,
    resolve_reviewable_draft,
    review_state,
)
from app.services.pagination import paginate, pagination_params

APPROVED_EVENT = "outreach_approved"
REJECTED_EVENT = "outreach_rejected"

router = APIRouter(tags=["outreach-review"])


def _require_lead(session: Session, lead_id: UUID) -> Lead:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _review(session: Session, lead_id: UUID, output_id: UUID, displayed_hash: str,
            decision: str, reason: str | None) -> OutreachReviewResponse:
    lead = _require_lead(session, lead_id)
    try:
        output = resolve_reviewable_draft(session, lead, output_id, displayed_hash)
    except ReviewError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    review, was_replay = apply_review(
        session, lead=lead, output=output, decision=decision, reason=reason
    )
    session.commit()
    session.refresh(review)
    verb = "approved" if decision == DECISION_APPROVED else "rejected"
    return OutreachReviewResponse(
        lead_id=lead.id,
        ai_output_id=output.id,
        review_id=review.id,
        event_type=APPROVED_EVENT if decision == DECISION_APPROVED else REJECTED_EVENT,
        message=f"Outreach already {verb} (no change)." if was_replay else f"Outreach {verb}.",
        idempotent_replay=was_replay,
    )


@router.post(
    "/api/leads/{lead_id}/approve-outreach",
    response_model=OutreachReviewResponse,
    status_code=status.HTTP_200_OK,
)
def approve_outreach(
    lead_id: UUID,
    request: ApproveOutreachRequest,
    session: Session = Depends(get_session),
) -> OutreachReviewResponse:
    return _review(session, lead_id, request.ai_output_id, request.content_hash, DECISION_APPROVED, None)


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
    return _review(session, lead_id, request.ai_output_id, request.content_hash, DECISION_REJECTED, request.reason)


def _source_info(lead: Lead) -> SourceInfo:
    snapshot = lead.source_snapshot
    batch_source = lead.batch.source if lead.batch is not None else None
    if snapshot is None:
        return SourceInfo(
            batch_source=batch_source,
            freshness_note=(
                "Facts come from an uploaded file; their date and accuracy "
                "were not verified by this application."
            ),
        )
    acquired = snapshot.reported_acquisition_date.isoformat() if snapshot.reported_acquisition_date else None
    return SourceInfo(
        batch_source=batch_source,
        provider=snapshot.provider,
        source_snapshot_id=snapshot.id,
        reported_acquisition_date=acquired,
        retrieved_at=snapshot.retrieved_at,
        license=snapshot.retrieved_license,
        freshness_note=(
            f"Facts come from a {snapshot.provider} snapshot reported as acquired "
            f"{acquired or 'on an unknown date'}. They may be outdated and were "
            "not re-verified; nothing in this record shows current interest or need."
        ),
    )


@router.get("/api/leads/{lead_id}/review-state", response_model=ReviewStateRead)
def get_review_state(lead_id: UUID, session: Session = Depends(get_session)) -> ReviewStateRead:
    """Current draft, its review status, and every reason an approval would
    not (or no longer) authorize delivery -- the same state Slack delivery
    enforces."""
    lead = _require_lead(session, lead_id)
    state = review_state(session, lead)
    codes = list(dict.fromkeys(state.delivery_blockers + state.email_blockers))
    return ReviewStateRead(
        lead_id=lead.id,
        status=state.status,
        draft_id=state.draft.id if state.draft else None,
        draft_content_hash=state.draft_content_hash,
        draft_origin=state.draft.origin if state.draft else None,
        draft_parent_output_id=state.draft.parent_output_id if state.draft else None,
        approval_applicable=state.approval_applicable,
        delivery_blockers=state.delivery_blockers,
        email_blockers=state.email_blockers,
        blocker_explanations={c: fit._GAP_EXPLANATIONS.get(c, c) for c in codes},
        latest_review=ReviewRead.model_validate(state.review) if state.review else None,
        source=_source_info(lead),
    )


@router.get("/api/leads/{lead_id}/reviews", response_model=Page[ReviewRead])
def list_lead_reviews(
    lead_id: UUID,
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[ReviewRead]:
    """Full operational review history, newest first -- including
    historical approvals that no longer authorize anything."""
    _require_lead(session, lead_id)
    limit, offset = pagination
    stmt = (
        select(AIOutputReview)
        .where(
            AIOutputReview.lead_id == lead_id,
            AIOutputReview.review_kind == REVIEW_KIND_OPERATIONAL_OUTREACH,
        )
        .order_by(AIOutputReview.created_at.desc(), AIOutputReview.id.desc())
    )
    return paginate(session, stmt, limit=limit, offset=offset, schema=ReviewRead)
