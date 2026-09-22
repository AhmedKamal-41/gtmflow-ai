"""Outreach draft review: approve / reject an exact, identified AIOutput.

Every approve/reject call must name the specific ``ai_output_id`` it is
acting on (Part D of the Phase 2 upgrade). The server then enforces, in
order:

  1. The output exists.
  2. The output belongs to *this* lead (a review can't reach across leads).
  3. The output is actually an outreach draft (not a summary or anything
     else masquerading as outreach).
  4. The output is not stale -- no newer outreach draft exists for this lead.
     This is what stops a stale browser tab from approving a draft it isn't
     currently displaying: if the tab is behind, its request now fails
     loudly (409) instead of silently landing on the wrong draft.
  5. If the exact same decision is already on record for this exact output,
     the call is treated as an idempotent retry: no duplicate review row, no
     duplicate WorkflowEvent. This is a PARTIAL fix for the approval_rate
     double-counting bug (docs/upgrade/audit.md E.1): it stops a duplicate
     *identical* request (e.g. a network retry) from inflating the count,
     but a genuine decision change on the same output (approve -> reject ->
     approve) still produces two "outreach_approved" WorkflowEvent rows for
     one generated draft, and the dashboard's approval_rate can still exceed
     100% -- reproduced in docs/upgrade/decisions.md's Phase 3 findings. The
     full fix (redefining the metric itself, e.g. counting distinct
     currently-approved outputs rather than raw approve events) is Phase 11
     scope, deliberately not done here.

See docs/upgrade/decisions.md for why `reviewer_label` is a fixed,
honestly-unauthenticated constant rather than a browser-supplied name.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import AIOutput, AIOutputReview, Lead, WorkflowEvent
from app.models.ai_output_review import REVIEW_KIND_OPERATIONAL_OUTREACH
from app.schemas.outreach_review import (
    ApproveOutreachRequest,
    OutreachReviewResponse,
    RejectOutreachRequest,
)
from app.scoring.lead_scoring import DISQUALIFIED_STATUSES
from app.services.fit_queries import newer_outreach_clause

OUTREACH_OUTPUT_TYPE = "outreach_email"
APPROVED_EVENT = "outreach_approved"
REJECTED_EVENT = "outreach_rejected"
APPROVED_STATUS = "outreach_approved"
REJECTED_STATUS = "outreach_rejected"
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"

# This application has no authentication (docs/upgrade/audit.md F.1). Every
# review created through the current UI/API is honestly labeled as such
# rather than accepting a browser-supplied display name and presenting it as
# an identity.
REVIEWER_LABEL = "local-demo-unauthenticated"

router = APIRouter(tags=["outreach-review"])


def _require_lead(session: Session, lead_id: UUID) -> Lead:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _resolve_output_for_review(
    session: Session, lead: Lead, ai_output_id: UUID
) -> AIOutput:
    output = session.get(AIOutput, ai_output_id)
    if output is None:
        raise HTTPException(
            status_code=404,
            detail=f"AI output {ai_output_id} not found.",
        )
    if output.lead_id != lead.id:
        raise HTTPException(
            status_code=400,
            detail=(
                "This output belongs to a different lead. Refusing to "
                "review it as this lead's outreach."
            ),
        )
    if output.output_type != OUTREACH_OUTPUT_TYPE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Output type '{output.output_type}' is not outreach and "
                "cannot be approved or rejected as outreach."
            ),
        )
    # "Newer" uses the same (created_at, id) order as GET latest-ai-output,
    # so a draft sharing a timestamp with another can't be both "the
    # latest" there and "superseded" here (or neither).
    newer_exists = session.execute(
        select(AIOutput.id).where(newer_outreach_clause(output)).limit(1)
    ).first()
    if newer_exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This draft has been superseded by a newer outreach draft "
                "for this lead. Refresh and review the latest version."
            ),
        )
    return output


def _latest_review_for_output(
    session: Session, ai_output_id: UUID
) -> AIOutputReview | None:
    return session.execute(
        select(AIOutputReview)
        .where(AIOutputReview.ai_output_id == ai_output_id)
        .order_by(AIOutputReview.created_at.desc(), AIOutputReview.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _apply_review(
    session: Session,
    *,
    lead: Lead,
    output: AIOutput,
    decision: str,
    event_type: str,
    lead_status: str,
    event_data: dict[str, Any],
) -> tuple[AIOutputReview, bool]:
    """Create a review row + WorkflowEvent, or no-op if this is an exact
    repeat of the current decision for this output (idempotent retry).

    Returns (review, was_idempotent_replay).

    Phase 3 fix: a blocked lead's status (do_not_contact / disqualified /
    unsubscribed) is a hard disposition, not a workflow stage -- reviewing
    its outreach must not overwrite it, the same invariant Phase 2 already
    established for scoring (see app/api/scoring.py). Before this fix,
    approving outreach on a blocked lead flipped Lead.status to
    "outreach_approved", which silently defeated the Phase 2 push-time
    status check (the lead was no longer *reported* as blocked, even though
    nothing about its disposition changed) -- reproduced and confirmed in
    docs/upgrade/decisions.md's Phase 3 findings before this fix landed.
    """
    latest = _latest_review_for_output(session, output.id)
    if lead.status not in DISQUALIFIED_STATUSES:
        lead.status = lead_status
    if latest is not None and latest.decision == decision:
        return latest, True

    review = AIOutputReview(
        lead_id=lead.id,
        ai_output_id=output.id,
        decision=decision,
        reason=event_data.get("reason"),
        review_kind=REVIEW_KIND_OPERATIONAL_OUTREACH,
        reviewer_label=REVIEWER_LABEL,
        legacy_unlinked=False,
    )
    session.add(review)
    session.add(WorkflowEvent(lead_id=lead.id, event_type=event_type, event_data=event_data))
    return review, False


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
    lead = _require_lead(session, lead_id)
    output = _resolve_output_for_review(session, lead, request.ai_output_id)

    review, was_replay = _apply_review(
        session,
        lead=lead,
        output=output,
        decision=DECISION_APPROVED,
        event_type=APPROVED_EVENT,
        lead_status=APPROVED_STATUS,
        event_data={
            "ai_output_id": str(output.id),
            "output_type": OUTREACH_OUTPUT_TYPE,
            "lead_id": str(lead.id),
        },
    )
    session.commit()
    session.refresh(review)

    return OutreachReviewResponse(
        lead_id=lead.id,
        ai_output_id=output.id,
        review_id=review.id,
        event_type=APPROVED_EVENT,
        message="Outreach already approved (no change)." if was_replay else "Outreach approved.",
        idempotent_replay=was_replay,
    )


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
    lead = _require_lead(session, lead_id)
    output = _resolve_output_for_review(session, lead, request.ai_output_id)

    review, was_replay = _apply_review(
        session,
        lead=lead,
        output=output,
        decision=DECISION_REJECTED,
        event_type=REJECTED_EVENT,
        lead_status=REJECTED_STATUS,
        event_data={
            "ai_output_id": str(output.id),
            "output_type": OUTREACH_OUTPUT_TYPE,
            "lead_id": str(lead.id),
            "reason": request.reason,
        },
    )
    session.commit()
    session.refresh(review)

    return OutreachReviewResponse(
        lead_id=lead.id,
        ai_output_id=output.id,
        review_id=review.id,
        event_type=REJECTED_EVENT,
        message="Outreach already rejected (no change)." if was_replay else "Outreach rejected.",
        idempotent_replay=was_replay,
    )
