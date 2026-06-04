"""Lead -> Slack push orchestration.

Caller is responsible for the request-level validation gates (lead exists,
integration_type=slack, lead is scored, lead is Hot OR force=true). This
module assumes those gates have already passed and focuses on:

  * gathering the latest AI summary + outreach as context
  * building the Slack payload
  * dispatching via the slack integration helper
  * persisting the IntegrationPush row, WorkflowEvent, and Lead.status update
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.slack import (
    build_lead_url,
    build_slack_payload,
    send_slack_payload,
)
from app.models import AIOutput, IntegrationPush, Lead, WorkflowEvent

SLACK = "slack"
SUCCESS_STATUSES = ("success", "mock_success")


def _latest_output_content(
    session: Session, lead_id: UUID, output_type: str
) -> dict[str, Any] | None:
    row = session.execute(
        select(AIOutput)
        .where(
            AIOutput.lead_id == lead_id,
            AIOutput.output_type == output_type,
        )
        .order_by(AIOutput.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return dict(row.content) if isinstance(row.content, dict) else None


def lead_has_successful_slack_push(session: Session, lead_id: UUID) -> bool:
    row = session.execute(
        select(IntegrationPush.id)
        .where(
            IntegrationPush.lead_id == lead_id,
            IntegrationPush.integration_type == SLACK,
            IntegrationPush.status.in_(SUCCESS_STATUSES),
        )
        .limit(1)
    ).first()
    return row is not None


def push_lead_to_slack(session: Session, lead: Lead) -> IntegrationPush:
    """Build the payload, send (or mock), persist. Caller must have validated.

    Returns the persisted IntegrationPush (not yet committed).
    """
    assert lead.score is not None, "push_lead_to_slack requires a scored lead"

    summary = _latest_output_content(session, lead.id, "company_summary")
    outreach = _latest_output_content(session, lead.id, "outreach_email")

    detected_pains: list[str] = []
    fit_reasoning: str | None = lead.score.reasoning
    if summary:
        raw_pains = summary.get("detected_pain_points")
        if isinstance(raw_pains, list):
            detected_pains = [str(p) for p in raw_pains if p]
        if summary.get("fit_reasoning"):
            fit_reasoning = str(summary["fit_reasoning"])

    outreach_subject: str | None = None
    call_note: str | None = None
    if outreach:
        if outreach.get("subject"):
            outreach_subject = str(outreach["subject"])
        if outreach.get("call_note"):
            call_note = str(outreach["call_note"])

    payload = build_slack_payload(
        lead_id=lead.id,
        company_name=lead.company_name,
        score=lead.score.total_score,
        priority=lead.score.priority,
        industry=lead.industry,
        contact_name=lead.contact_name,
        contact_title=lead.contact_title,
        contact_email=lead.contact_email,
        fit_reasoning=fit_reasoning,
        detected_pain_points=detected_pains or None,
        outreach_subject=outreach_subject,
        call_note=call_note,
        lead_url=build_lead_url(lead.id),
    )

    status, response_text = send_slack_payload(settings.slack_webhook_url, payload)

    push = IntegrationPush(
        lead_id=lead.id,
        integration_type=SLACK,
        payload=payload,
        status=status,
        response_text=response_text,
    )
    session.add(push)

    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type="lead_pushed",
            event_data={
                "integration_type": SLACK,
                "status": status,
                "priority": lead.score.priority,
                "score": lead.score.total_score,
            },
        )
    )

    if status in SUCCESS_STATUSES:
        lead.status = "pushed"

    return push
