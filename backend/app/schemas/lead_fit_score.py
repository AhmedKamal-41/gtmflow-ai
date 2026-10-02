from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class FitCriterionRead(BaseModel):
    name: str
    weight: int
    active: bool
    source_field: str
    raw_input: Any
    normalized_input: str | None
    result: str
    points: int
    explanation: str


class ActionReadinessRead(BaseModel):
    status: str
    gaps: list[str]
    gap_explanations: dict[str, str]


class ReadinessRead(BaseModel):
    outbound_email: ActionReadinessRead
    internal_slack_handoff: ActionReadinessRead


class EligibilityRead(BaseModel):
    excluded: bool
    reasons: list[str]
    checked_at: datetime


class HistoricalAssessment(BaseModel):
    """What readiness/eligibility were when this score row was computed.
    Audit trail only -- never used to gate an action."""

    readiness: ReadinessRead
    eligibility_excluded: bool
    eligibility_reasons: list[str]
    computed_at: datetime


class LeadFitScoreResponse(BaseModel):
    """Full Part C output for one lead: fit + coverage + band, readiness,
    eligibility, and every version identifier needed to reproduce or audit
    the result (Part D.2)."""

    id: UUID | None = None
    lead_id: UUID
    scorer_version: str
    profile_id: str
    profile_version: str
    normalization_version: str
    input_fingerprint: str
    fit_score: int
    max_fit_score: int
    evidence_coverage_pct: float
    band: str
    criteria: list[FitCriterionRead]
    # CURRENT readiness/eligibility, recomputed on every read.
    readiness: ReadinessRead
    readiness_is_current: bool
    eligibility: EligibilityRead
    # The snapshot stored with this row (None for an unpersisted result).
    at_scoring: HistoricalAssessment | None = None
    computed_at: datetime
    computation_ms: float


class FitProfileResponse(BaseModel):
    """Static rubric definition -- Part E: UI labels this as broad
    demonstration criteria, not a claim of purchase probability."""

    profile_id: str
    profile_version: str
    scorer_version: str
    normalization_version: str
    description: str
    industry_match_values: list[str]
    industry_weight: int
    country_match_values: list[str]
    country_weight: int
    size_weight: int
    zero_weight_criteria: list[str]
    max_fit_score: int
    coverage_threshold_pct: float
    band_strong_min: int
    band_partial_min: int


class CurrentReadinessResponse(BaseModel):
    """GET /api/leads/{id}/readiness: live readiness and routing
    eligibility, available whether or not the lead was ever fit-scored."""

    lead_id: UUID
    readiness: ReadinessRead
    eligibility: EligibilityRead


class BatchFitSummary(BaseModel):
    """Distinct-lead view of a batch under the current profile version:
    each lead counted once, in the band of its LATEST applicable score.
    `score_rows` counts every applicable stored row (history included) so a
    reader can see rescoring happened without it inflating the lead counts."""

    batch_id: UUID
    total_leads: int
    scored_leads: int
    unscored_leads: int
    score_rows: int
    strong_match: int
    partial_match: int
    weak_match: int
    insufficient_evidence: int
    average_fit_score: float | None


class BatchFitScoreRunSummary(BaseModel):
    """Result of POST /api/batches/{id}/fit-score. Run counts describe this
    run; `summary` is the batch's distinct-lead state after it. Scoring
    writes LeadFitScore rows only -- no Lead/LeadBatch status change."""

    batch_id: UUID
    attempted: int
    newly_scored: int
    skipped_unchanged: int
    failed: int
    summary: BatchFitSummary
