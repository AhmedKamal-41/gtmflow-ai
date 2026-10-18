from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class FitCriterionRead(BaseModel):
    """One criterion of a ranking. `weight` is the default weight (0 when
    the profile does not configure the criterion); `points` the weight
    earned (half for a partial size match). Rows from before profile-based
    ranking lack the newer fields, hence the defaults."""

    name: str
    label: str | None = None
    weight: int
    active: bool
    source_field: str
    raw_input: Any
    normalized_input: str | None
    result: str
    points: float
    explanation: str
    targets: list[str] = []
    matched_targets: list[str] = []


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


class CriteriaSummary(BaseModel):
    matched: list[str]
    partial: list[str]
    mismatched: list[str]
    missing: list[str]
    not_configured: list[str]


class LeadFitScoreResponse(BaseModel):
    """One lead's ranking against a seller profile's target customers:
    score, evidence coverage, band and priority, the per-criterion
    breakdown, current readiness/eligibility (contact completeness and
    permission to contact, kept separate from fit), and every identifier
    needed to reproduce or audit it. `is_current` is false for a ranking
    made against different target preferences than the active profile's."""

    id: UUID | None = None
    lead_id: UUID
    scorer_version: str
    profile_id: str
    profile_version: str
    seller_profile_id: UUID | None = None
    targeting_hash: str | None = None
    normalization_version: str
    input_fingerprint: str
    fit_score: int
    max_fit_score: int
    evidence_coverage_pct: float
    band: str
    priority: str | None
    is_current: bool
    explanation: str
    summary: CriteriaSummary
    criteria: list[FitCriterionRead]
    # CURRENT readiness/eligibility, recomputed on every read.
    readiness: ReadinessRead
    readiness_is_current: bool
    eligibility: EligibilityRead
    # The snapshot stored with this row (None for an unpersisted result).
    at_scoring: HistoricalAssessment | None = None
    computed_at: datetime
    computation_ms: float


class RankingStatusResponse(BaseModel):
    """GET /api/scoring/profile: what ranking compares leads with right now,
    the fixed rules, and how many leads are ranked, outdated or unranked.
    `state` is `ready`, `no_active_profile` or `setup_required` (the active
    profile states no target customers)."""

    state: str
    message: str
    profile: dict[str, Any] | None
    scorer_version: str
    normalization_version: str
    weights: dict[str, int]
    labels: dict[str, str]
    hot_min: int
    warm_min: int
    coverage_min_pct: float
    partial_credit: float
    total_leads: int
    current_rankings: int
    outdated_rankings: int
    never_ranked: int


class RankingRunSummary(BaseModel):
    """Result of a recalculation (workspace or one batch). Run counts
    describe this run; nothing but LeadFitScore rows and their audit events
    is written."""

    batch_id: UUID | None = None
    profile_id: str
    profile_version: str
    targeting_hash: str
    attempted: int
    newly_scored: int
    skipped_unchanged: int
    failed: int


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

    batch_id: UUID | None
    ranking_state: str
    profile_id: str | None
    profile_version: str | None
    targeting_hash: str | None
    total_leads: int
    scored_leads: int
    unscored_leads: int
    outdated_leads: int
    score_rows: int
    strong_match: int
    partial_match: int
    weak_match: int
    insufficient_evidence: int
    hot: int
    warm: int
    cold: int
    average_fit_score: float | None


class BatchFitScoreRunSummary(BaseModel):
    """Result of POST /api/batches/{id}/fit-score. Run counts describe this
    run; `summary` is the batch's distinct-lead state after it. Scoring
    writes LeadFitScore rows only -- no Lead/LeadBatch status change."""

    batch_id: UUID
    profile_id: str
    profile_version: str
    attempted: int
    newly_scored: int
    skipped_unchanged: int
    failed: int
    summary: BatchFitSummary
