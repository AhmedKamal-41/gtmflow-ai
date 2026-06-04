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
from app.services.integration_push import (
    SLACK,
    lead_has_successful_slack_push,
    push_lead_to_slack,
)

router = APIRouter(tags=["push"])


def _validate_integration_type(integration_type: str) -> None:
    if (integration_type or "").strip().lower() != SLACK:
        raise HTTPException(
            status_code=400,
            detail="Unsupported integration_type. Only 'slack' is supported in Phase 6.",
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

    push = push_lead_to_slack(session, lead)
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

    pushed = skipped = failed = 0
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

        push = push_lead_to_slack(session, lead)
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
        results=results,
    )


@router.get(
    "/api/leads/{lead_id}/pushes",
    response_model=list[IntegrationPushRead],
)
def list_lead_pushes(
    lead_id: UUID,
    session: Session = Depends(get_session),
) -> list[IntegrationPush]:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    rows = (
        session.execute(
            select(IntegrationPush)
            .where(IntegrationPush.lead_id == lead_id)
            .order_by(IntegrationPush.created_at.desc())
        )
        .scalars()
        .all()
    )
    return list(rows)
