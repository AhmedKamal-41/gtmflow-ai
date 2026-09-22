from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import IntegrationPush, Lead, LeadBatch, WorkflowEvent
from app.schemas.integration_push import (
    BatchPushResult,
    BatchPushSummary,
    IntegrationPushRead,
    PushRequest,
)
from app.schemas.pagination import Page
from app.services.integration_push import (
    SLACK,
    BlockedLeadError,
    lead_has_successful_slack_push,
    push_lead_to_slack,
)
from app.services.pagination import pagination_params, paginate

router = APIRouter(tags=["push"])


def _validate_integration_type(integration_type: str) -> None:
    if (integration_type or "").strip().lower() != SLACK:
        raise HTTPException(
            status_code=400,
            detail="Unsupported integration_type. Only 'slack' is supported in Phase 6.",
        )


def _require_complete_batch(lead: Lead) -> None:
    """Part C.4 of the Phase 3 closeout: a batch whose CSV upload only
    partially committed (some chunks failed/were never reached, e.g. the
    row-count limit was hit mid-stream) must be excluded from dispatch
    through every push entry point -- not overridable by force=true, same
    precedence as the blocked-lead-status check. A lead's own batch may be
    None only in tests that construct a Lead without one; treated as
    complete (nothing to exclude) in that case.
    """
    batch = lead.batch
    if batch is not None and batch.status == "partial":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Batch {batch.id} is only partially imported (status='partial') "
                "and is excluded from Slack routing until the import completes. "
                "This cannot be overridden with force=true."
            ),
        )


@router.post(
    "/api/leads/{lead_id}/push",
    response_model=IntegrationPushRead,
    status_code=status.HTTP_200_OK,
)
def push_one_lead(
    lead_id: UUID,
    request: PushRequest,
    session: Session = Depends(get_session),
) -> IntegrationPush:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    _validate_integration_type(request.integration_type)
    _require_complete_batch(lead)

    if lead.score is None:
        raise HTTPException(
            status_code=400,
            detail="Lead must be scored before it can be pushed.",
        )

    if lead.score.priority != "Hot" and not request.force:
        raise HTTPException(
            status_code=400,
            detail="Only Hot leads can be pushed unless force=true.",
        )

    try:
        push = push_lead_to_slack(session, lead)
    except BlockedLeadError as e:
        # Persist the audit WorkflowEvent the service already added, then
        # report the block. This is not overridable by force=true.
        session.commit()
        raise HTTPException(
            status_code=400,
            detail=(
                f"Lead status is '{e.lead_status}' and blocks outreach "
                "delivery. This cannot be overridden with force=true."
            ),
        ) from e
    session.commit()
    session.refresh(push)
    return push


@router.post(
    "/api/batches/{batch_id}/push-hot",
    response_model=BatchPushSummary,
    status_code=status.HTTP_200_OK,
)
def push_batch_hot_leads(
    batch_id: UUID,
    request: PushRequest,
    session: Session = Depends(get_session),
) -> BatchPushSummary:
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    _validate_integration_type(request.integration_type)
    if batch.status == "partial":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Batch {batch.id} is only partially imported (status='partial') "
                "and is excluded from Slack routing until the import completes. "
                "This cannot be overridden with force=true."
            ),
        )

    leads_in_batch = list(
        session.execute(
            select(Lead).where(Lead.batch_id == batch_id)
        ).scalars().all()
    )
    hot_leads = [
        lead
        for lead in leads_in_batch
        if lead.score is not None and lead.score.priority == "Hot"
    ]

    pushed = skipped = failed = blocked = 0
    results: list[BatchPushResult] = []

    for lead in hot_leads:
        if not request.force and lead_has_successful_slack_push(session, lead.id):
            skipped += 1
            results.append(
                BatchPushResult(
                    lead_id=lead.id,
                    company_name=lead.company_name,
                    status="skipped",
                    reason="already pushed (use force=true to re-push)",
                )
            )
            continue

        try:
            push = push_lead_to_slack(session, lead)
        except BlockedLeadError as e:
            # Not overridable by force=true: distinct from "skipped" (already
            # pushed) and from "failed" (attempted delivery, transport error).
            # No IntegrationPush row exists for this lead/attempt.
            blocked += 1
            results.append(
                BatchPushResult(
                    lead_id=lead.id,
                    company_name=lead.company_name,
                    status="blocked",
                    reason=(
                        f"lead status is '{e.lead_status}'; blocked from "
                        "outreach delivery, not overridable by force=true"
                    ),
                )
            )
            continue
        session.flush()  # populate push.id

        if push.status in ("success", "mock_success"):
            pushed += 1
            results.append(
                BatchPushResult(
                    lead_id=lead.id,
                    company_name=lead.company_name,
                    status=push.status,
                    push_id=push.id,
                )
            )
        else:
            failed += 1
            results.append(
                BatchPushResult(
                    lead_id=lead.id,
                    company_name=lead.company_name,
                    status="failed",
                    push_id=push.id,
                    reason=(push.response_text or "")[:200] or None,
                )
            )

    session.add(
        WorkflowEvent(
            batch_id=batch.id,
            event_type="batch_hot_leads_pushed",
            event_data={
                "integration_type": SLACK,
                "hot_leads_found": len(hot_leads),
                "pushed": pushed,
                "skipped": skipped,
                "failed": failed,
                "blocked": blocked,
            },
        )
    )
    session.commit()

    return BatchPushSummary(
        batch_id=batch_id,
        hot_leads_found=len(hot_leads),
        pushed=pushed,
        skipped=skipped,
        failed=failed,
        blocked=blocked,
        results=results,
    )


@router.get(
    "/api/leads/{lead_id}/pushes",
    response_model=Page[IntegrationPushRead],
)
def list_lead_pushes(
    lead_id: UUID,
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[IntegrationPushRead]:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    limit, offset = pagination
    stmt = (
        select(IntegrationPush)
        .where(IntegrationPush.lead_id == lead_id)
        .order_by(IntegrationPush.created_at.desc(), IntegrationPush.id.desc())
    )
    return paginate(session, stmt, limit=limit, offset=offset, schema=IntegrationPushRead)
