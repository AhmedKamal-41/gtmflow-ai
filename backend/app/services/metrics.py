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

Phase 11 (audit E.1, D.4/E.3):

* **Approval metrics use one cohort**: operational outreach drafts
  (``purpose='operational'``; human-edited revisions included, each being its
  own draft; annotation candidates excluded). Each draft counts once, by its
  LATEST operational review (created_at, id -- the same ordering the approval
  gate uses). approve -> reject -> approve on one draft is one approved
  draft, so ``approval_rate`` = approved drafts / drafts can never exceed
  100% -- by construction, not by clamping. Raw event counts stay available
  as ``approval_events`` / ``rejection_events`` for audit only.
* **Mock versus real** is reported for two independent dimensions:
  ``generation_by_mode`` (which model wrote the draft; a human revision
  inherits the mode of the draft it revises) and ``delivery_by_mode`` (mock
  webhook versus a real Slack webhook). Each breakdown sums to the matching
  top-level totals. ``data_mode`` says whether anything real is in the data.

The v2 company-fit counts (``fit_*``) are distinct LEADS, each counted
once in the band of its latest applicable score (see
app/services/fit_queries.py) -- rescoring adds history rows but never
moves these numbers unless a lead's latest band actually changed. They are
reported separately from the legacy Hot/Warm/Cold counts, never merged.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app.models import AIOutput, AIOutputReview, IntegrationPush, Lead, LeadScore, WorkflowEvent
from app.models.ai_output import PURPOSE_OPERATIONAL
from app.models.ai_output_review import REVIEW_KIND_OPERATIONAL_OUTREACH
from app.services.fit_queries import batch_fit_summary

OUTREACH_OUTPUT_TYPE = "outreach_email"
APPROVED_EVENT = "outreach_approved"
REJECTED_EVENT = "outreach_rejected"
SUCCESS_PUSH_STATUSES = ("success", "mock_success")
FAILED_PUSH_STATUS = "failed"
UNKNOWN_PUSH_STATUS = "unknown"
PENDING_PUSH_STATUS = "pending"
MODES = ("mock", "real", "unknown")

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


def _draft_modes(session: Session) -> dict[Any, str]:
    """Mode of every operational outreach draft: 'mock' for the mock
    generator, 'real' for a real model, 'unknown' for rows whose generator
    was never recorded. A human revision inherits its original draft's mode."""
    rows = session.execute(
        select(AIOutput.id, AIOutput.model_used, AIOutput.origin, AIOutput.parent_output_id)
        .where(AIOutput.output_type == OUTREACH_OUTPUT_TYPE, AIOutput.purpose == PURPOSE_OPERATIONAL)
    ).all()
    by_id = {r.id: r for r in rows}

    def mode(row, depth: int = 0) -> str:
        if row.origin == "human_edited":
            parent = by_id.get(row.parent_output_id)
            return mode(parent, depth + 1) if parent is not None and depth < 50 else "unknown"
        if row.model_used == "mock":
            return "mock"
        return "real" if row.model_used else "unknown"

    return {r.id: mode(r) for r in rows}


def _latest_decisions(session: Session) -> dict[Any, str]:
    """Latest operational review decision per output (approved/rejected)."""
    rn = func.row_number().over(
        partition_by=AIOutputReview.ai_output_id,
        order_by=(AIOutputReview.created_at.desc(), AIOutputReview.id.desc()),
    ).label("rn")
    ranked = (
        select(AIOutputReview.ai_output_id, AIOutputReview.decision, rn)
        .where(AIOutputReview.review_kind == REVIEW_KIND_OPERATIONAL_OUTREACH,
               AIOutputReview.ai_output_id.is_not(None))
        .subquery()
    )
    return {r.ai_output_id: r.decision for r in session.execute(
        select(ranked.c.ai_output_id, ranked.c.decision).where(ranked.c.rn == 1))}


def _push_mode(delivery_mode: str | None, status: str) -> str:
    if delivery_mode in ("mock", "real"):
        return delivery_mode
    # Rows from before Phase 11 recorded no mode; mock_success can only be
    # mock and success can only be real. A legacy failure is undeterminable.
    return {"mock_success": "mock", "success": "real"}.get(status, "unknown")


def compute_dashboard(session: Session) -> dict[str, Any]:
    total_leads_uploaded = _count(session, Lead)
    total_leads_processed = _count(session, LeadScore)

    hot_leads = _count(session, LeadScore, LeadScore.priority == "Hot")
    warm_leads = _count(session, LeadScore, LeadScore.priority == "Warm")
    cold_leads = _count(session, LeadScore, LeadScore.priority == "Cold")

    # One cohort: distinct operational outreach drafts, by latest review.
    draft_modes = _draft_modes(session)
    decisions = _latest_decisions(session)
    generation = {m: {"drafts_generated": 0, "drafts_approved": 0, "drafts_rejected": 0} for m in MODES}
    for output_id, mode in draft_modes.items():
        g = generation[mode]
        g["drafts_generated"] += 1
        decision = decisions.get(output_id)
        if decision == "approved":
            g["drafts_approved"] += 1
        elif decision == "rejected":
            g["drafts_rejected"] += 1
    for g in generation.values():
        g["drafts_pending_review"] = g["drafts_generated"] - g["drafts_approved"] - g["drafts_rejected"]
        g["approval_rate"] = _pct(g["drafts_approved"], g["drafts_generated"])
    outreach_generated = len(draft_modes)
    outreach_approved = sum(g["drafts_approved"] for g in generation.values())
    outreach_rejected = sum(g["drafts_rejected"] for g in generation.values())
    outreach_pending_review = outreach_generated - outreach_approved - outreach_rejected
    approval_rate = _pct(outreach_approved, outreach_generated)
    reviewed_approval_rate = _pct(outreach_approved, outreach_approved + outreach_rejected)
    approval_events = _count(session, WorkflowEvent, WorkflowEvent.event_type == APPROVED_EVENT)
    rejection_events = _count(session, WorkflowEvent, WorkflowEvent.event_type == REJECTED_EVENT)

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

    delivery = {m: {"attempts": 0, "delivered": 0, "failed": 0, "outcome_unknown": 0, "pending": 0}
                for m in MODES}
    delivered_leads: dict[str, set] = {m: set() for m in MODES}
    for lead_id, status, delivery_mode in session.execute(
            select(IntegrationPush.lead_id, IntegrationPush.status, IntegrationPush.delivery_mode)):
        mode = _push_mode(delivery_mode, status)
        d = delivery[mode]
        d["attempts"] += 1
        if status in SUCCESS_PUSH_STATUSES:
            d["delivered"] += 1
            delivered_leads[mode].add(lead_id)
        elif status == FAILED_PUSH_STATUS:
            d["failed"] += 1
        elif status == UNKNOWN_PUSH_STATUS:
            d["outcome_unknown"] += 1
        elif status == PENDING_PUSH_STATUS:
            d["pending"] += 1
    for mode, d in delivery.items():
        d["unique_leads_delivered"] = len(delivered_leads[mode])
        d["success_rate"] = _pct(d["delivered"], d["attempts"])
    push_unknown_count = sum(d["outcome_unknown"] for d in delivery.values())
    push_pending_count = sum(d["pending"] for d in delivery.values())
    real_seen = generation["real"]["drafts_generated"] or delivery["real"]["attempts"]
    mock_seen = generation["mock"]["drafts_generated"] or delivery["mock"]["attempts"]
    data_mode = ("mixed" if real_seen and mock_seen else "real_only" if real_seen
                 else "mock_only" if mock_seen else "empty")

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

    fit = batch_fit_summary(session, None)

    return {
        "fit_scored_leads": fit["scored_leads"],
        "fit_strong_match": fit["strong_match"],
        "fit_partial_match": fit["partial_match"],
        "fit_weak_match": fit["weak_match"],
        "fit_insufficient_evidence": fit["insufficient_evidence"],
        "total_leads_uploaded": total_leads_uploaded,
        "total_leads_processed": total_leads_processed,
        "hot_leads": hot_leads,
        "warm_leads": warm_leads,
        "cold_leads": cold_leads,
        "outreach_generated": outreach_generated,
        "outreach_approved": outreach_approved,
        "outreach_rejected": outreach_rejected,
        "outreach_pending_review": outreach_pending_review,
        "approval_rate": approval_rate,
        "reviewed_approval_rate": reviewed_approval_rate,
        "approval_events": approval_events,
        "rejection_events": rejection_events,
        "leads_pushed": leads_pushed,
        "unique_leads_pushed": unique_leads_pushed,
        "push_success_rate": push_success_rate,
        "failed_push_count": failed_push_count,
        "push_unknown_count": push_unknown_count,
        "push_pending_count": push_pending_count,
        "real_messages_delivered": delivery["real"]["delivered"],
        "mock_messages_delivered": delivery["mock"]["delivered"],
        "generation_by_mode": generation,
        "delivery_by_mode": delivery,
        "data_mode": data_mode,
        "estimated_time_saved_minutes": estimated_time_saved_minutes,
        "estimated_time_saved_hours": estimated_time_saved_hours,
        "average_lead_score": average_lead_score,
        "missing_data_rate": missing_data_rate,
        "automation_coverage": automation_coverage,
    }
