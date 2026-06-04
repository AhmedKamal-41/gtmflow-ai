"""Adoption + ROI metrics computed against the live database.

Numbers here are explainable in one English sentence each. Time-saved is a
demo estimate (5 minutes per processed lead) and is labeled as such on the
frontend, never claim real revenue impact.

Two push counters are deliberately exposed:
  * ``leads_pushed``: count of successful push *rows* (success +
    mock_success). Pushing the same lead twice increments this by 2.
  * ``unique_leads_pushed``: distinct leads with at least one successful
    push. Pushing the same lead twice keeps this at 1.

The frontend shows both with a one-line explanation of the distinction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app.models import AIOutput, IntegrationPush, Lead, LeadScore, WorkflowEvent

OUTREACH_OUTPUT_TYPE = "outreach_email"
APPROVED_EVENT = "outreach_approved"
REJECTED_EVENT = "outreach_rejected"
SUCCESS_PUSH_STATUSES = ("success", "mock_success")
FAILED_PUSH_STATUS = "failed"

MINUTES_SAVED_PER_PROCESSED_LEAD = 5


def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def _count(session: Session, model: Any, *where: Any) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    value = session.scalar(stmt)
    return int(value or 0)


def compute_dashboard(session: Session) -> dict[str, Any]:
    total_leads_uploaded = _count(session, Lead)
    total_leads_processed = _count(session, LeadScore)

    hot_leads = _count(session, LeadScore, LeadScore.priority == "Hot")
    warm_leads = _count(session, LeadScore, LeadScore.priority == "Warm")
    cold_leads = _count(session, LeadScore, LeadScore.priority == "Cold")

    outreach_generated = _count(
        session, AIOutput, AIOutput.output_type == OUTREACH_OUTPUT_TYPE
    )
    outreach_approved = _count(
        session, WorkflowEvent, WorkflowEvent.event_type == APPROVED_EVENT
    )
    outreach_rejected = _count(
        session, WorkflowEvent, WorkflowEvent.event_type == REJECTED_EVENT
    )
    approval_rate = _pct(outreach_approved, outreach_generated)

    leads_pushed = _count(
        session,
        IntegrationPush,
        IntegrationPush.status.in_(SUCCESS_PUSH_STATUSES),
    )
    unique_leads_pushed = int(
        session.scalar(
            select(func.count(distinct(IntegrationPush.lead_id))).where(
                IntegrationPush.status.in_(SUCCESS_PUSH_STATUSES)
            )
        )
        or 0
    )
    total_push_attempts = _count(session, IntegrationPush)
    failed_push_count = _count(
        session, IntegrationPush, IntegrationPush.status == FAILED_PUSH_STATUS
    )
    push_success_rate = _pct(leads_pushed, total_push_attempts)

    estimated_time_saved_minutes = (
        total_leads_processed * MINUTES_SAVED_PER_PROCESSED_LEAD
    )
    estimated_time_saved_hours = round(estimated_time_saved_minutes / 60, 2)

    avg_score_raw = session.scalar(select(func.avg(LeadScore.total_score)))
    average_lead_score = (
        round(float(avg_score_raw), 2) if avg_score_raw is not None else 0.0
    )

    missing_data_count = _count(
        session,
        Lead,
        or_(
            Lead.contact_email.is_(None),
            Lead.contact_email == "",
            Lead.website.is_(None),
            Lead.website == "",
        ),
    )
    missing_data_rate = _pct(missing_data_count, total_leads_uploaded)

    automation_coverage = _pct(total_leads_processed, total_leads_uploaded)

    return {
        "total_leads_uploaded": total_leads_uploaded,
        "total_leads_processed": total_leads_processed,
        "hot_leads": hot_leads,
        "warm_leads": warm_leads,
        "cold_leads": cold_leads,
        "outreach_generated": outreach_generated,
        "outreach_approved": outreach_approved,
        "outreach_rejected": outreach_rejected,
        "approval_rate": approval_rate,
        "leads_pushed": leads_pushed,
        "unique_leads_pushed": unique_leads_pushed,
        "push_success_rate": push_success_rate,
        "failed_push_count": failed_push_count,
        "estimated_time_saved_minutes": estimated_time_saved_minutes,
        "estimated_time_saved_hours": estimated_time_saved_hours,
        "average_lead_score": average_lead_score,
        "missing_data_rate": missing_data_rate,
        "automation_coverage": automation_coverage,
    }
