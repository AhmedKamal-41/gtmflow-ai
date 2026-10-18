"""Ranking persistence shared by HTTP endpoints, background jobs, the demo
and the scoring CLI.

The pure rubric stays in app.scoring.fit. This service ranks leads against
one RankingProfile (by default the active seller revision's target
preferences) and writes each score and its audit event in one savepoint,
without changing workflow dispositions, drafts, reviews, or delivery
state. Callers commit bounded chunks.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead, LeadFitScore, WorkflowEvent
from app.schemas.lead_fit_score import (
    CriteriaSummary,
    HistoricalAssessment,
    LeadFitScoreResponse,
)
from app.scoring import fit
from app.services import fit_queries

SCORE_CHUNK_SIZE = 500


def require_ranking_profile(session: Session, profile=fit_queries.ACTIVE) -> fit.RankingProfile:
    """The profile to rank with, or RankingSetupRequired with a message the
    UI can show as-is. Never falls back to another rubric."""
    resolved = fit_queries._resolve(session, profile)
    if resolved is None:
        raise fit.RankingSetupRequired(
            "No seller profile is active. Activate a seller profile that describes "
            "your target customers before ranking leads."
        )
    if not resolved.is_configured:
        raise fit.RankingSetupRequired(
            f"The active seller profile (version {resolved.version}) has no target customers. "
            "Add industries, locations, company sizes, contact roles or keywords, then save "
            "and activate it to rank leads."
        )
    return resolved


def _row_is_current(row: LeadFitScore, current: fit.RankingProfile | None) -> bool:
    return (
        current is not None
        and current.is_configured
        and row.targeting_hash == current.targeting_hash
        and row.scorer_version == fit.SCORER_VERSION
        and row.normalization_version == fit.NORMALIZATION_VERSION
    )


def fit_score_response(
    *,
    lead: Lead,
    row: LeadFitScore | None,
    fit_result: fit.FitResult | None,
    readiness: fit.ReadinessResult,
    eligibility: fit.EligibilityResult,
    current: fit.RankingProfile | None = None,
) -> LeadFitScoreResponse:
    """Build a response from either a stored row or a fresh (unpersisted)
    computation. Fit/coverage/band/criteria come from the row when there is
    one; readiness/eligibility are always the current values passed in.
    `current` is the active ranking profile, used to label a stored row as
    current or outdated (a fresh result is current by construction)."""
    if row is not None:
        criteria = list(row.criteria)
        return LeadFitScoreResponse(
            id=row.id,
            lead_id=lead.id,
            scorer_version=row.scorer_version,
            profile_id=row.profile_id,
            profile_version=row.profile_version,
            seller_profile_id=row.seller_profile_id,
            targeting_hash=row.targeting_hash,
            normalization_version=row.normalization_version,
            input_fingerprint=row.input_fingerprint,
            fit_score=row.fit_score,
            max_fit_score=fit.MAX_FIT_SCORE,
            evidence_coverage_pct=row.evidence_coverage_pct,
            band=row.band,
            priority=fit.priority_for_band(row.band),
            is_current=_row_is_current(row, current),
            explanation=fit.explain(criteria, row.band),
            summary=CriteriaSummary(**fit.summarize_criteria(criteria)),
            criteria=criteria,
            readiness=readiness.to_dict(),
            readiness_is_current=True,
            eligibility=eligibility.to_dict(),
            at_scoring=HistoricalAssessment(
                readiness=row.readiness,
                eligibility_excluded=row.eligibility_excluded,
                eligibility_reasons=row.eligibility_reasons,
                computed_at=row.computed_at,
            ),
            computed_at=row.computed_at,
            computation_ms=row.computation_ms,
        )
    assert fit_result is not None
    criteria = fit_result.criteria_as_dicts()
    return LeadFitScoreResponse(
        id=None,
        lead_id=lead.id,
        scorer_version=fit_result.scorer_version,
        profile_id=fit_result.profile_id,
        profile_version=fit_result.profile_version,
        seller_profile_id=fit_result.seller_profile_id,
        targeting_hash=fit_result.targeting_hash,
        normalization_version=fit_result.normalization_version,
        input_fingerprint=fit_result.input_fingerprint,
        fit_score=fit_result.fit_score,
        max_fit_score=fit_result.max_fit_score,
        evidence_coverage_pct=fit_result.evidence_coverage_pct,
        band=fit_result.band,
        priority=fit_result.priority,
        is_current=True,
        explanation=fit.explain(criteria, fit_result.band),
        summary=CriteriaSummary(**fit.summarize_criteria(criteria)),
        criteria=criteria,
        readiness=readiness.to_dict(),
        readiness_is_current=True,
        eligibility=eligibility.to_dict(),
        at_scoring=None,
        computed_at=fit_result.computed_at,
        computation_ms=fit_result.computation_ms,
    )


def score_leads(
    session: Session,
    leads: list[Lead],
    *,
    persist: bool,
    skip_unchanged: bool = False,
    profile=fit_queries.ACTIVE,
) -> list[tuple[Lead, LeadFitScoreResponse | None, Exception | None]]:
    """Rank a bounded list of leads against `profile` (default: the active
    seller revision's target preferences). Raises RankingSetupRequired
    before touching anything when there is nothing to rank against.

    Returns, per lead, `(lead, response, error)`:
      * `response` set, `error` None -- ranked (and persisted if `persist`);
      * both None -- skipped: its latest current row already has this
        exact input fingerprint, so ranking again would only duplicate history;
      * `error` set -- that lead failed; its savepoint was rolled back and
        the other leads are unaffected.

    Writes a score and its audit event atomically. The caller owns the transaction.
    """
    profile = require_ranking_profile(session, profile)
    if persist and leads:
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            # Legacy sqlite3 does not BEGIN for a SELECT or SAVEPOINT.
            # Without an outer BEGIN, releasing a savepoint commits the
            # score before the caller commits its chunk.
            if not connection.connection.driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
    readiness_by_lead = fit_queries.current_readiness_by_lead(session, leads)
    existing = (
        fit_queries.latest_fit_scores_by_lead(session, [lead.id for lead in leads], profile)
        if skip_unchanged
        else {}
    )
    results: list[tuple[Lead, LeadFitScoreResponse | None, Exception | None]] = []
    for lead in leads:
        readiness, eligibility = readiness_by_lead[lead.id]
        try:
            fit_result = fit.compute_fit(lead, profile)
            prior = existing.get(lead.id)
            if prior is not None and prior.input_fingerprint == fit_result.input_fingerprint:
                results.append((lead, None, None))
                continue
            row: LeadFitScore | None = None
            if persist:
                with session.begin_nested():
                    row = LeadFitScore(
                        lead_id=lead.id,
                        scorer_version=fit_result.scorer_version,
                        profile_id=fit_result.profile_id,
                        profile_version=fit_result.profile_version,
                        seller_profile_id=fit_result.seller_profile_id,
                        targeting_hash=fit_result.targeting_hash,
                        normalization_version=fit_result.normalization_version,
                        input_fingerprint=fit_result.input_fingerprint,
                        fit_score=fit_result.fit_score,
                        evidence_coverage_pct=fit_result.evidence_coverage_pct,
                        band=fit_result.band,
                        criteria=fit_result.criteria_as_dicts(),
                        readiness=readiness.to_dict(),
                        eligibility_excluded=eligibility.excluded,
                        eligibility_reasons=eligibility.reasons,
                        computed_at=fit_result.computed_at,
                        computation_ms=fit_result.computation_ms,
                    )
                    session.add(row)
                    session.flush()
                    session.add(WorkflowEvent(
                        lead_id=lead.id,
                        batch_id=lead.batch_id,
                        event_type="lead_fit_scored",
                        event_data={
                            "lead_fit_score_id": str(row.id),
                            "scorer_version": row.scorer_version,
                            "profile_id": row.profile_id,
                            "profile_version": row.profile_version,
                            "targeting_hash": row.targeting_hash,
                            "normalization_version": row.normalization_version,
                            "input_fingerprint": row.input_fingerprint,
                            "fit_score": row.fit_score,
                            "band": row.band,
                            "priority": fit_result.priority,
                            "evidence_coverage_pct": row.evidence_coverage_pct,
                        },
                    ))
                    session.flush()
            results.append(
                (
                    lead,
                    fit_score_response(
                        lead=lead,
                        row=row,
                        fit_result=fit_result,
                        readiness=readiness,
                        eligibility=eligibility,
                        current=profile,
                    ),
                    None,
                )
            )
        except Exception as e:  # noqa: BLE001 -- per-lead isolation
            results.append((lead, None, e))
    return results


def rank_in_chunks(
    session: Session,
    *,
    batch_id: UUID | None = None,
    profile=fit_queries.ACTIVE,
    chunk_size: int | None = None,
) -> dict[str, Any]:
    """Rank every lead (of one batch, or the whole workspace) in chunks of
    `chunk_size`, one committed transaction per chunk. Leads whose latest
    current row has the same input fingerprint are skipped, so running it
    twice writes nothing new. Raises RankingSetupRequired up front."""
    profile = require_ranking_profile(session, profile)
    chunk_size = chunk_size or SCORE_CHUNK_SIZE
    stmt = select(Lead.id).order_by(Lead.id)
    if batch_id is not None:
        stmt = stmt.where(Lead.batch_id == batch_id)
    lead_ids = list(session.execute(stmt).scalars())
    scored = skipped = failed = 0
    for start in range(0, len(lead_ids), chunk_size):
        chunk_ids = lead_ids[start : start + chunk_size]
        leads = list(session.execute(select(Lead).where(Lead.id.in_(chunk_ids))).scalars())
        chunk_scored = 0
        for _, response, error in score_leads(
            session, leads, persist=True, skip_unchanged=True, profile=profile
        ):
            if error is not None:
                failed += 1
            elif response is None:
                skipped += 1
            else:
                chunk_scored += 1
        try:
            session.commit()
        except Exception:  # a failed chunk must not be reported as scored
            session.rollback()
            failed += chunk_scored
        else:
            scored += chunk_scored
    return {
        "batch_id": batch_id,
        "profile_id": profile.profile_id,
        "profile_version": profile.version,
        "targeting_hash": profile.targeting_hash,
        "attempted": len(lead_ids),
        "newly_scored": scored,
        "skipped_unchanged": skipped,
        "failed": failed,
    }
