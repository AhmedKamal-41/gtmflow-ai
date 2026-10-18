"""The rep's lead inbox: every lead with its priority and workflow stage.

Priority is the lead's current ranking against the active seller profile's
target customers (app/services/fit_queries.py): Hot / Warm / Cold, or
"needs data" (`priority` None with a score) when too little of the lead's
record could be compared. A lead ranked only against earlier target
preferences is `ranking_outdated` and has no priority until ranked again.

Stages, in workflow order:
* needs_score  -- no current ranking (never ranked, or outdated)
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
from app.models.lead import DISQUALIFIED_STATUSES
from app.models.lead_fit_score import LeadFitScore
from app.scoring import fit
from app.services import fit_queries
from app.services.draft_review import STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED, review_states

MAX_LEADS = 5000
STAGES = ("needs_score", "needs_draft", "to_review", "outdated", "rejected", "approved", "sent")
STALE_DRAFT_BLOCKERS = frozenset({fit.GAP_DRAFT_INPUTS_CHANGED, fit.GAP_DRAFT_NOT_FROM_ACTIVE_PROFILE,
                                  fit.GAP_APPROVAL_WITHOUT_CONTENT_IDENTITY})
PRIORITY_ORDER = {"Hot": 0, "Warm": 1, "Cold": 2, None: 3}


def ranking_sort_key(priority: str | None, score: int | None, has_ranking: bool) -> tuple[int, int]:
    """Hot, Warm, Cold, then "needs data", then unranked; higher score first."""
    tier = PRIORITY_ORDER[priority] if priority else (3 if has_ranking else 4)
    return tier, -(score if score is not None else -1)


def ranking_fields(row: LeadFitScore | None) -> dict:
    return {
        "priority": fit.priority_for_band(row.band) if row else None,
        "score": row.fit_score if row else None,
        "band": row.band if row else None,
        "evidence_coverage_pct": row.evidence_coverage_pct if row else None,
        "ranking_reason": fit.explain(row.criteria, row.band) if row else None,
    }


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


def build_inbox(session: Session, profile=fit_queries.ACTIVE) -> list[dict]:
    profile = fit_queries._resolve(session, profile)
    leads = session.execute(
        select(Lead, LeadBatch.name).join(LeadBatch, LeadBatch.id == Lead.batch_id)
        .order_by(Lead.created_at.desc(), Lead.id.desc()).limit(MAX_LEADS)
    ).all()
    lead_ids = [lead.id for lead, _ in leads]
    scores = fit_queries.latest_fit_scores_by_lead(session, lead_ids, profile)
    unranked = [i for i in lead_ids if i not in scores]
    outdated = set(fit_queries.latest_any_fit_scores_by_lead(session, unranked))
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
            **ranking_fields(score),
            "ranking_outdated": lead.id in outdated,
            "stage": stage,
            "delivery_unknown": lead.id in unknown,
            "draft_model": draft.model_used if draft else None,
            "blocked": lead.status in DISQUALIFIED_STATUSES,
            "updated_at": lead.updated_at,
        })
    rows.sort(key=lambda r: (*ranking_sort_key(r["priority"], r["score"], r["band"] is not None),
                             r["company_name"].lower()))
    return rows


def query_inbox(session: Session, *, stage: str | None, priority: str | None, q: str | None,
                limit: int, offset: int) -> dict:
    profile = fit_queries.current_ranking_profile(session)
    rows = build_inbox(session, profile)
    counts = {name: 0 for name in STAGES}
    for row in rows:
        counts[row["stage"]] += 1
    hot = sum(1 for row in rows if row["priority"] == "Hot")
    needs_data = sum(1 for row in rows if row["band"] == fit.BAND_INSUFFICIENT_EVIDENCE)
    needle = (q or "").strip().lower()
    filtered = [
        row for row in rows
        if (stage is None or row["stage"] == stage)
        and (priority is None or row["priority"] == priority
             or (priority == "needs_data" and row["band"] == fit.BAND_INSUFFICIENT_EVIDENCE))
        and (not needle or needle in row["company_name"].lower() or needle in (row["contact_name"] or "").lower())
    ]
    return {"items": filtered[offset:offset + limit], "total": len(filtered), "limit": limit, "offset": offset,
            "counts": {**counts, "all": len(rows), "hot": hot, "needs_data": needs_data,
                       "ranking_outdated": sum(1 for row in rows if row["ranking_outdated"]),
                       "delivery_unknown": sum(1 for row in rows if row["delivery_unknown"])},
            "ranking": {"state": fit_queries.ranking_state(profile),
                        "profile_version": profile.version if profile else None,
                        "profile_kind": profile.profile_kind if profile else None}}
