"""Phase 4: the ONE definition of "a lead's current v2 fit score" and of
"current readiness", shared by every endpoint that reads them (single-lead
GET, batch bulk listing, batch summary, and GET /api/leads sort/filter).

Why one module: before this, each endpoint carried its own copy of the
version filter and "latest" ordering, and they had already started to drift
(one checked three version fields, another would have needed a fourth).
A lead must never be "strong_match" on the list page and something else on
its own page because two queries disagreed about which row is current.

Definitions:

* Applicable row: `scorer_version`, `profile_id`, `profile_version` AND
  `normalization_version` all equal the scorer's current constants. Rows
  from any other version stay in the table as history but are never
  "current" -- a lead scored only under an old profile reads as unscored.
* Latest: ordered by `created_at DESC, id DESC`. `id` is the unique
  tie-breaker for equal timestamps (rows written in the same bounded batch
  can share one); it's arbitrary-but-fixed, so the same data always
  resolves to the same row.
* Current readiness: recomputed from live lead / outreach-draft / review
  state in a bounded number of queries per page, never read back from the
  snapshot stored at scoring time (that snapshot is historical and is
  returned separately, labeled as such).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import AIOutput, AIOutputReview, Lead, LeadFitScore
from app.scoring import fit

OUTREACH_OUTPUT_TYPE = "outreach_email"


def current_version_clause():
    return and_(
        LeadFitScore.scorer_version == fit.SCORER_VERSION,
        LeadFitScore.profile_id == fit.PROFILE_ID,
        LeadFitScore.profile_version == fit.PROFILE_VERSION,
        LeadFitScore.normalization_version == fit.NORMALIZATION_VERSION,
    )


LATEST_FIT_ORDER = (LeadFitScore.created_at.desc(), LeadFitScore.id.desc())


def latest_fit_subquery(lead_ids: list[UUID] | None = None):
    """One row per lead: that lead's latest applicable fit score. A window
    function over one scan, not a correlated subquery per lead. Columns:
    every LeadFitScore column (id, lead_id, fit_score, band, ...)."""
    rn = func.row_number().over(
        partition_by=LeadFitScore.lead_id, order_by=LATEST_FIT_ORDER
    ).label("rn")
    stmt = select(LeadFitScore, rn).where(current_version_clause())
    if lead_ids is not None:
        stmt = stmt.where(LeadFitScore.lead_id.in_(lead_ids))
    ranked = stmt.subquery()
    return select(ranked).where(ranked.c.rn == 1).subquery()


def latest_fit_scores_by_lead(
    session: Session, lead_ids: list[UUID]
) -> dict[UUID, LeadFitScore]:
    """Bounded bulk lookup: one query for however many leads are on the
    current page."""
    if not lead_ids:
        return {}
    latest = latest_fit_subquery(lead_ids)
    rows = session.execute(
        select(LeadFitScore).join(latest, LeadFitScore.id == latest.c.id)
    ).scalars().all()
    return {row.lead_id: row for row in rows}


def latest_fit_score(session: Session, lead_id: UUID) -> LeadFitScore | None:
    return latest_fit_scores_by_lead(session, [lead_id]).get(lead_id)


# ---------------------------------------------------------------- readiness

def _latest_outreach_by_lead(
    session: Session, lead_ids: list[UUID]
) -> dict[UUID, AIOutput]:
    """Each lead's current outreach draft -- same `created_at DESC, id
    DESC` rule as GET /api/leads/{id}/latest-ai-output and the approve /
    reject supersession check, so readiness describes exactly the draft the
    review buttons act on."""
    if not lead_ids:
        return {}
    rn = func.row_number().over(
        partition_by=AIOutput.lead_id,
        order_by=(AIOutput.created_at.desc(), AIOutput.id.desc()),
    ).label("rn")
    ranked = (
        select(AIOutput.id, rn)
        .where(
            AIOutput.lead_id.in_(lead_ids),
            AIOutput.output_type == OUTREACH_OUTPUT_TYPE,
        )
        .subquery()
    )
    rows = session.execute(
        select(AIOutput).join(ranked, AIOutput.id == ranked.c.id).where(ranked.c.rn == 1)
    ).scalars().all()
    return {row.lead_id: row for row in rows}


def _latest_review_decision_by_output(
    session: Session, output_ids: list[UUID]
) -> dict[UUID, str]:
    if not output_ids:
        return {}
    rn = func.row_number().over(
        partition_by=AIOutputReview.ai_output_id,
        order_by=(AIOutputReview.created_at.desc(), AIOutputReview.id.desc()),
    ).label("rn")
    ranked = (
        select(AIOutputReview.ai_output_id, AIOutputReview.decision, rn)
        .where(AIOutputReview.ai_output_id.in_(output_ids))
        .subquery()
    )
    rows = session.execute(
        select(ranked.c.ai_output_id, ranked.c.decision).where(ranked.c.rn == 1)
    ).all()
    return {output_id: decision for output_id, decision in rows}


def current_readiness_by_lead(
    session: Session, leads: list[Lead]
) -> dict[UUID, tuple[fit.ReadinessResult, fit.EligibilityResult]]:
    """Live readiness + eligibility for a page of leads in two queries
    (latest drafts, then their latest reviews). Eligibility reads
    `lead.status` / `lead.batch.status` as currently loaded."""
    lead_ids = [lead.id for lead in leads]
    drafts = _latest_outreach_by_lead(session, lead_ids)
    decisions = _latest_review_decision_by_output(
        session, [d.id for d in drafts.values()]
    )
    out: dict[UUID, tuple[fit.ReadinessResult, fit.EligibilityResult]] = {}
    for lead in leads:
        draft = drafts.get(lead.id)
        eligibility = fit.compute_eligibility(lead)
        readiness = fit.compute_readiness(
            lead,
            has_contact_email=bool(lead.contact_email),
            latest_outreach_exists=draft is not None,
            latest_outreach_review_decision=decisions.get(draft.id) if draft else None,
            eligibility_excluded=eligibility.excluded,
        )
        out[lead.id] = (readiness, eligibility)
    return out


def current_readiness(
    session: Session, lead: Lead
) -> tuple[fit.ReadinessResult, fit.EligibilityResult]:
    return current_readiness_by_lead(session, [lead])[lead.id]


# ----------------------------------------------------------------- summary

def batch_fit_summary(session: Session, batch_id: UUID) -> dict[str, Any]:
    """Distinct-lead counts from each lead's latest applicable row -- a
    lead rescored five times counts once, in the band of its latest score.
    `score_rows` (all applicable rows, history included) is reported
    alongside so the difference is visible rather than silently hidden."""
    total_leads = session.scalar(
        select(func.count()).select_from(Lead).where(Lead.batch_id == batch_id)
    ) or 0
    batch_lead_ids = select(Lead.id).where(Lead.batch_id == batch_id)
    latest = latest_fit_subquery()
    band_rows = session.execute(
        select(latest.c.band, func.count(), func.sum(latest.c.fit_score))
        .where(latest.c.lead_id.in_(batch_lead_ids))
        .group_by(latest.c.band)
    ).all()
    counts = {
        fit.BAND_STRONG: 0,
        fit.BAND_PARTIAL: 0,
        fit.BAND_WEAK: 0,
        fit.BAND_INSUFFICIENT_EVIDENCE: 0,
    }
    scored = 0
    score_sum = 0
    for band, count, band_sum in band_rows:
        counts[band] = counts.get(band, 0) + count
        scored += count
        score_sum += band_sum or 0
    score_rows = session.scalar(
        select(func.count())
        .select_from(LeadFitScore)
        .where(current_version_clause(), LeadFitScore.lead_id.in_(batch_lead_ids))
    ) or 0
    return {
        "batch_id": batch_id,
        "total_leads": total_leads,
        "scored_leads": scored,
        "unscored_leads": total_leads - scored,
        "score_rows": score_rows,
        "strong_match": counts[fit.BAND_STRONG],
        "partial_match": counts[fit.BAND_PARTIAL],
        "weak_match": counts[fit.BAND_WEAK],
        "insufficient_evidence": counts[fit.BAND_INSUFFICIENT_EVIDENCE],
        "average_fit_score": round(score_sum / scored, 1) if scored else None,
    }


def newer_outreach_clause(output: AIOutput):
    """`AIOutput` rows for the same lead that sort AFTER `output` under the
    `created_at DESC, id DESC` rule -- i.e. drafts that supersede it."""
    return and_(
        AIOutput.lead_id == output.lead_id,
        AIOutput.output_type == OUTREACH_OUTPUT_TYPE,
        or_(
            AIOutput.created_at > output.created_at,
            and_(AIOutput.created_at == output.created_at, AIOutput.id > output.id),
        ),
    )
