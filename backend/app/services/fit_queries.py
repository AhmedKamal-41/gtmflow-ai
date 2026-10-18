"""The ONE definition of "a lead's current ranking" and of "current
readiness", shared by every reader: single-lead and batch fit endpoints,
GET /api/leads sort/filter, the inbox, the dashboard, batch jobs, the
assistant, draft review and Slack delivery.

Definitions:

* Ranking profile: the target preferences of the ACTIVE seller-profile
  revision (`current_ranking_profile`). No active revision, or one that
  states no target customers, means nothing is ranked -- there is no
  fallback rubric.
* Current row: `scorer_version`, `normalization_version` and
  `targeting_hash` equal the ranking profile's. Rows ranked against other
  preferences (an earlier revision's, or the retired demo rubric's) stay in
  the table as history but are never current: their lead reads as
  "outdated" until it is ranked again. A new revision that keeps the same
  target preferences keeps the same hash, so its scores stay current.
* Latest: ordered by `created_at DESC, id DESC` (`id` is the unique
  tie-breaker for rows written in the same batch).
* Current readiness: recomputed from live lead / draft / review state,
  never read back from the snapshot stored at scoring time.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session

from app.models import AIOutput, AIOutputReview, Lead, LeadFitScore
from app.scoring import fit
from app.services.seller_profiles import active_seller_profile

OUTREACH_OUTPUT_TYPE = "outreach_email"

RANKING_READY = "ready"
RANKING_NO_PROFILE = "no_active_profile"
RANKING_SETUP_REQUIRED = "setup_required"


class _Active:
    """Sentinel: resolve the ranking profile from the active seller revision."""


ACTIVE = _Active()


def current_ranking_profile(session: Session) -> fit.RankingProfile | None:
    row = active_seller_profile(session)
    if row is None:
        return None
    return fit.ranking_profile_from_content(
        row.profile,
        source=fit.SOURCE_ACTIVE,
        profile_id=str(row.id),
        seller_profile_id=row.id,
        version=str(row.version),
    )


def builtin_demo_ranking_profile() -> fit.RankingProfile:
    from app.services.seller_profiles import GTMFLOW_DEMO_PROFILE

    return fit.ranking_profile_from_content(
        GTMFLOW_DEMO_PROFILE.model_dump(mode="json"),
        source=fit.SOURCE_BUILTIN_DEMO,
        profile_id="builtin-demo",
        seller_profile_id=None,
        version="demo",
    )


def ranking_state(profile: fit.RankingProfile | None) -> str:
    if profile is None:
        return RANKING_NO_PROFILE
    return RANKING_READY if profile.is_configured else RANKING_SETUP_REQUIRED


def _resolve(session: Session, profile) -> fit.RankingProfile | None:
    return current_ranking_profile(session) if isinstance(profile, _Active) else profile


def current_version_clause(profile: fit.RankingProfile | None):
    if profile is None or not profile.is_configured:
        return false()
    return and_(
        LeadFitScore.scorer_version == fit.SCORER_VERSION,
        LeadFitScore.normalization_version == fit.NORMALIZATION_VERSION,
        LeadFitScore.targeting_hash == profile.targeting_hash,
    )


LATEST_FIT_ORDER = (LeadFitScore.created_at.desc(), LeadFitScore.id.desc())


def latest_fit_subquery(profile: fit.RankingProfile | None, lead_ids: list[UUID] | None = None,
                        *, current_only: bool = True):
    """One row per lead: that lead's latest current row (or, with
    `current_only=False`, its latest row of any version). A window function
    over one scan, not a correlated subquery per lead."""
    rn = func.row_number().over(
        partition_by=LeadFitScore.lead_id, order_by=LATEST_FIT_ORDER
    ).label("rn")
    stmt = select(LeadFitScore, rn)
    if current_only:
        stmt = stmt.where(current_version_clause(profile))
    if lead_ids is not None:
        stmt = stmt.where(LeadFitScore.lead_id.in_(lead_ids))
    ranked = stmt.subquery()
    return select(ranked).where(ranked.c.rn == 1).subquery()


def latest_fit_scores_by_lead(
    session: Session, lead_ids: list[UUID], profile=ACTIVE
) -> dict[UUID, LeadFitScore]:
    """Bounded bulk lookup of current rows: one query per page of leads."""
    profile = _resolve(session, profile)
    if not lead_ids or profile is None or not profile.is_configured:
        return {}
    latest = latest_fit_subquery(profile, lead_ids)
    rows = session.execute(
        select(LeadFitScore).join(latest, LeadFitScore.id == latest.c.id)
    ).scalars().all()
    return {row.lead_id: row for row in rows}


def latest_fit_score(session: Session, lead_id: UUID, profile=ACTIVE) -> LeadFitScore | None:
    return latest_fit_scores_by_lead(session, [lead_id], profile).get(lead_id)


def latest_any_fit_scores_by_lead(session: Session, lead_ids: list[UUID]) -> dict[UUID, LeadFitScore]:
    """Each lead's newest row of ANY version -- to show an outdated ranking
    as outdated (never as current)."""
    if not lead_ids:
        return {}
    latest = latest_fit_subquery(None, lead_ids, current_only=False)
    rows = session.execute(
        select(LeadFitScore).join(latest, LeadFitScore.id == latest.c.id)
    ).scalars().all()
    return {row.lead_id: row for row in rows}


def ranking_counts(session: Session, profile=ACTIVE, batch_id: UUID | None = None) -> dict[str, int]:
    """Distinct leads: total, ranked under the current preferences, ranked
    only under other (outdated) preferences, never ranked."""
    profile = _resolve(session, profile)
    lead_filter = [Lead.batch_id == batch_id] if batch_id is not None else []
    lead_ids = select(Lead.id).where(*lead_filter)
    total = session.scalar(select(func.count()).select_from(Lead).where(*lead_filter)) or 0
    any_rows = session.scalar(
        select(func.count(func.distinct(LeadFitScore.lead_id))).where(LeadFitScore.lead_id.in_(lead_ids))
    ) or 0
    current = session.scalar(
        select(func.count(func.distinct(LeadFitScore.lead_id)))
        .where(LeadFitScore.lead_id.in_(lead_ids), current_version_clause(profile))
    ) or 0
    return {"total_leads": total, "current": current, "outdated": any_rows - current,
            "never_ranked": total - any_rows}


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
            AIOutput.purpose == "operational",
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
    """Live readiness + eligibility for a page of leads in a bounded number
    of queries: the active seller revision plus the shared review state
    (app/services/draft_review.py: current drafts, their latest reviews,
    current fit rows). Slack delivery enforces the same review state, so
    what readiness reports and what delivery allows can't diverge.
    Eligibility reads `lead.status` / `lead.batch.status` as loaded."""
    from app.services.draft_review import review_states

    active = active_seller_profile(session)
    if active is None:
        seller_state = fit.SELLER_STATE_NONE
    elif active.profile.get("profile_kind") == "demo":
        seller_state = fit.SELLER_STATE_DEMO
    else:
        seller_state = fit.SELLER_STATE_SELLER
    states = review_states(session, leads)
    out: dict[UUID, tuple[fit.ReadinessResult, fit.EligibilityResult]] = {}
    for lead in leads:
        state = states[lead.id]
        eligibility = fit.compute_eligibility(lead)
        readiness = fit.compute_readiness(
            lead,
            has_contact_email=bool(lead.contact_email),
            latest_outreach_exists=state.draft is not None,
            latest_outreach_review_decision=state.review.decision if state.review else None,
            eligibility_excluded=eligibility.excluded,
            seller_profile_state=seller_state,
            latest_outreach_uses_active_profile=(
                state.draft is not None
                and active is not None
                and state.draft.seller_profile_id == active.id
            ),
            outreach_review_blockers=state.delivery_blockers,
            email_review_blockers=state.email_blockers,
        )
        out[lead.id] = (readiness, eligibility)
    return out


def current_readiness(
    session: Session, lead: Lead
) -> tuple[fit.ReadinessResult, fit.EligibilityResult]:
    return current_readiness_by_lead(session, [lead])[lead.id]


# ----------------------------------------------------------------- summary

def batch_fit_summary(session: Session, batch_id: UUID | None, profile=ACTIVE) -> dict[str, Any]:
    """Distinct-lead counts from each lead's latest current row -- a lead
    ranked five times counts once, in the band of its latest score.
    `score_rows` (all current rows, history included) is reported alongside
    so the difference is visible. `batch_id=None` summarizes every lead."""
    profile = _resolve(session, profile)
    lead_filter = [Lead.batch_id == batch_id] if batch_id is not None else []
    batch_lead_ids = select(Lead.id).where(*lead_filter)
    latest = latest_fit_subquery(profile)
    band_rows = session.execute(
        select(latest.c.band, func.count(), func.sum(latest.c.fit_score))
        .where(latest.c.lead_id.in_(batch_lead_ids))
        .group_by(latest.c.band)
    ).all()
    counts = {band: 0 for band in fit.BANDS}
    scored = 0
    score_sum = 0
    for band, count, band_sum in band_rows:
        counts[band] = counts.get(band, 0) + count
        scored += count
        score_sum += band_sum or 0
    score_rows = session.scalar(
        select(func.count())
        .select_from(LeadFitScore)
        .where(current_version_clause(profile), LeadFitScore.lead_id.in_(batch_lead_ids))
    ) or 0
    totals = ranking_counts(session, profile, batch_id)
    return {
        "batch_id": batch_id,
        "ranking_state": ranking_state(profile),
        "profile_id": profile.profile_id if profile else None,
        "profile_version": profile.version if profile else None,
        "targeting_hash": profile.targeting_hash if profile else None,
        "total_leads": totals["total_leads"],
        "scored_leads": scored,
        "unscored_leads": totals["total_leads"] - scored,
        "outdated_leads": totals["outdated"],
        "score_rows": score_rows,
        "strong_match": counts[fit.BAND_STRONG],
        "partial_match": counts[fit.BAND_PARTIAL],
        "weak_match": counts[fit.BAND_WEAK],
        "insufficient_evidence": counts[fit.BAND_INSUFFICIENT_EVIDENCE],
        "hot": counts[fit.BAND_STRONG],
        "warm": counts[fit.BAND_PARTIAL],
        "cold": counts[fit.BAND_WEAK],
        "average_fit_score": round(score_sum / scored, 1) if scored else None,
    }


def newer_outreach_clause(output: AIOutput):
    """`AIOutput` rows for the same lead that sort AFTER `output` under the
    `created_at DESC, id DESC` rule -- i.e. drafts that supersede it."""
    return and_(
        AIOutput.lead_id == output.lead_id,
        AIOutput.output_type == OUTREACH_OUTPUT_TYPE,
        AIOutput.purpose == "operational",
        or_(
            AIOutput.created_at > output.created_at,
            and_(AIOutput.created_at == output.created_at, AIOutput.id > output.id),
        ),
    )
