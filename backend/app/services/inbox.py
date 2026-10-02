"""The rep's lead inbox: every lead with its priority and workflow stage.

Stages, in workflow order:
* needs_score  -- not scored yet
* needs_draft  -- scored, no outreach draft
* to_review    -- the current draft awaits a decision and is still current
* outdated     -- the draft's inputs changed (company facts, fit, seller
                  profile), so approving or sending it would be refused
* rejected     -- the current draft was rejected
* approved     -- approved, the approval authorizes delivery, not yet sent
* sent         -- a Slack delivery succeeded for this lead (real or mock)
`delivery_unknown` marks a lead whose latest delivery outcome is unknown and
needs an operator decision.

The draft state comes from review_states() -- the same function the lead
page and delivery use -- so the list never offers "ready to send" for a
lead that dispatch would refuse. The whole workspace is ranked in memory,
so this is bounded to MAX_LEADS (a single team's working set).
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.integration_push import PUSH_MOCK_SUCCESS, PUSH_SUCCESS, IntegrationPush
from app.models.lead import Lead
from app.models.lead_batch import LeadBatch
from app.models.lead_score import LeadScore
from app.scoring import fit
from app.scoring.lead_scoring import DISQUALIFIED_STATUSES
from app.services.draft_review import STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED, review_states

MAX_LEADS = 5000
STAGES = ("needs_score", "needs_draft", "to_review", "outdated", "rejected", "approved", "sent")
STALE_DRAFT_BLOCKERS = frozenset({fit.GAP_DRAFT_INPUTS_CHANGED, fit.GAP_DRAFT_NOT_FROM_ACTIVE_PROFILE,
                                  fit.GAP_APPROVAL_WITHOUT_CONTENT_IDENTITY})
PRIORITY_ORDER = {"Hot": 0, "Warm": 1, "Cold": 2, None: 3}


def _latest_scores(session: Session, lead_ids: list[UUID]) -> dict[UUID, LeadScore]:
    if not lead_ids:
        return {}
    rn = func.row_number().over(partition_by=LeadScore.lead_id,
                                order_by=(LeadScore.created_at.desc(), LeadScore.id.desc())).label("rn")
    ranked = select(LeadScore.id, rn).where(LeadScore.lead_id.in_(lead_ids)).subquery()
    rows = session.execute(select(LeadScore).join(ranked, LeadScore.id == ranked.c.id)
                           .where(ranked.c.rn == 1)).scalars().all()
    return {row.lead_id: row for row in rows}


def _push_summary(session: Session, lead_ids: list[UUID]) -> tuple[set[UUID], set[UUID]]:
    """(leads with a successful delivery, leads whose latest delivery is unknown)."""
    if not lead_ids:
        return set(), set()
    delivered = set(session.scalars(select(IntegrationPush.lead_id).where(
        IntegrationPush.lead_id.in_(lead_ids), IntegrationPush.status.in_((PUSH_SUCCESS, PUSH_MOCK_SUCCESS)))))
    rn = func.row_number().over(partition_by=IntegrationPush.lead_id,
                                order_by=(IntegrationPush.created_at.desc(), IntegrationPush.id.desc())).label("rn")
    ranked = select(IntegrationPush.lead_id, IntegrationPush.status, rn).where(
        IntegrationPush.lead_id.in_(lead_ids)).subquery()
    unknown = set(session.scalars(select(ranked.c.lead_id).where(ranked.c.rn == 1, ranked.c.status == "unknown")))
    return delivered, unknown


def build_inbox(session: Session) -> list[dict]:
    leads = session.execute(
        select(Lead, LeadBatch.name).join(LeadBatch, LeadBatch.id == Lead.batch_id)
        .order_by(Lead.created_at.desc(), Lead.id.desc()).limit(MAX_LEADS)
    ).all()
    lead_ids = [lead.id for lead, _ in leads]
    scores = _latest_scores(session, lead_ids)
    states = review_states(session, [lead for lead, _ in leads])
    delivered, unknown = _push_summary(session, lead_ids)
    rows = []
    for lead, batch_name in leads:
        score, state = scores.get(lead.id), states[lead.id]
        draft = state.draft
        stale = bool(STALE_DRAFT_BLOCKERS.intersection(state.delivery_blockers))
        if draft is None:
            stage = "sent" if lead.id in delivered else "needs_draft" if score is not None else "needs_score"
        elif state.status == STATUS_REJECTED:
            stage = "rejected"
        elif state.status == STATUS_APPROVED and lead.id in delivered:
            stage = "sent"
        elif stale:
            stage = "outdated"
        elif state.status == STATUS_PENDING:
            stage = "to_review"
        else:
            stage = "approved" if state.approval_applicable else "outdated"
        rows.append({
            "id": lead.id,
            "company_name": lead.company_name,
            "contact_name": lead.contact_name,
            "contact_title": lead.contact_title,
            "industry": lead.industry,
            "batch_id": lead.batch_id,
            "batch_name": batch_name,
            "priority": score.priority if score else None,
            "score": score.total_score if score else None,
            "stage": stage,
            "delivery_unknown": lead.id in unknown,
            "draft_model": draft.model_used if draft else None,
            "blocked": lead.status in DISQUALIFIED_STATUSES,
            "updated_at": lead.updated_at,
        })
    rows.sort(key=lambda r: (PRIORITY_ORDER.get(r["priority"], 3), -(r["score"] or -1), r["company_name"].lower()))
    return rows


def query_inbox(session: Session, *, stage: str | None, priority: str | None, q: str | None,
                limit: int, offset: int) -> dict:
    rows = build_inbox(session)
    counts = {name: 0 for name in STAGES}
    for row in rows:
        counts[row["stage"]] += 1
    hot = sum(1 for row in rows if row["priority"] == "Hot")
    needle = (q or "").strip().lower()
    filtered = [
        row for row in rows
        if (stage is None or row["stage"] == stage)
        and (priority is None or row["priority"] == priority)
        and (not needle or needle in row["company_name"].lower() or needle in (row["contact_name"] or "").lower())
    ]
    return {"items": filtered[offset:offset + limit], "total": len(filtered), "limit": limit, "offset": offset,
            "counts": {**counts, "all": len(rows), "hot": hot,
                       "delivery_unknown": sum(1 for row in rows if row["delivery_unknown"])}}
