"""Fit-score persistence shared by HTTP endpoints and the scoring CLI.

The pure rubric stays in app.scoring.fit. This service writes a score and
its audit event in one savepoint, without changing workflow dispositions,
drafts, reviews, or delivery state. Callers commit bounded chunks.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Lead, LeadFitScore, WorkflowEvent
from app.schemas.lead_fit_score import HistoricalAssessment, LeadFitScoreResponse
from app.scoring import fit
from app.services import fit_queries


def fit_score_response(
    *,
    lead: Lead,
    row: LeadFitScore | None,
    fit_result: fit.FitResult | None,
    readiness: fit.ReadinessResult,
    eligibility: fit.EligibilityResult,
) -> LeadFitScoreResponse:
    """Build a response from either a stored row or a fresh (unpersisted)
    computation. Fit/coverage/band/criteria come from the row when there is
    one; readiness/eligibility are always the current values passed in."""
    if row is not None:
        historical = HistoricalAssessment(
            readiness=row.readiness,
            eligibility_excluded=row.eligibility_excluded,
            eligibility_reasons=row.eligibility_reasons,
            computed_at=row.computed_at,
        )
        return LeadFitScoreResponse(
            id=row.id,
            lead_id=lead.id,
            scorer_version=row.scorer_version,
            profile_id=row.profile_id,
            profile_version=row.profile_version,
            normalization_version=row.normalization_version,
            input_fingerprint=row.input_fingerprint,
            fit_score=row.fit_score,
            max_fit_score=fit.MAX_FIT_SCORE,
            evidence_coverage_pct=row.evidence_coverage_pct,
            band=row.band,
            criteria=row.criteria,
            readiness=readiness.to_dict(),
            readiness_is_current=True,
            eligibility=eligibility.to_dict(),
            at_scoring=historical,
            computed_at=row.computed_at,
            computation_ms=row.computation_ms,
        )
    assert fit_result is not None
    return LeadFitScoreResponse(
        id=None,
        lead_id=lead.id,
        scorer_version=fit_result.scorer_version,
        profile_id=fit_result.profile_id,
        profile_version=fit_result.profile_version,
        normalization_version=fit_result.normalization_version,
        input_fingerprint=fit_result.input_fingerprint,
        fit_score=fit_result.fit_score,
        max_fit_score=fit_result.max_fit_score,
        evidence_coverage_pct=fit_result.evidence_coverage_pct,
        band=fit_result.band,
        criteria=fit_result.criteria_as_dicts(),
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
) -> list[tuple[Lead, LeadFitScoreResponse | None, Exception | None]]:
    """Score a bounded list of leads: one readiness query pair for the whole
    list, and (with `skip_unchanged`) one lookup of existing latest rows.

    Returns, per lead, `(lead, response, error)`:
      * `response` set, `error` None -- scored (and persisted if `persist`);
      * both None -- skipped: its latest applicable row already has this
        exact input fingerprint, so rescoring would only duplicate history;
      * `error` set -- that lead failed; its savepoint was rolled back and
        the other leads are unaffected.

    Writes a score and its audit event atomically. The caller owns the transaction.
    """
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
        fit_queries.latest_fit_scores_by_lead(session, [lead.id for lead in leads])
        if skip_unchanged
        else {}
    )
    results: list[tuple[Lead, LeadFitScoreResponse | None, Exception | None]] = []
    for lead in leads:
        readiness, eligibility = readiness_by_lead[lead.id]
        try:
            fit_result = fit.compute_fit(lead)
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
                            "normalization_version": row.normalization_version,
                            "input_fingerprint": row.input_fingerprint,
                            "fit_score": row.fit_score,
                            "band": row.band,
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
                    ),
                    None,
                )
            )
        except Exception as e:  # noqa: BLE001 -- per-lead isolation
            results.append((lead, None, e))
    return results


