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
    readiness: ReadinessRead
    readiness_is_current: bool
    eligibility: EligibilityRead
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


class BatchFitScoreSummary(BaseModel):
    """Result of POST /api/batches/{id}/fit-score: bounded, side-effect-free
    (no Lead/LeadBatch status mutation -- Part D.3) scoring of every lead in
    a batch."""

    batch_id: UUID
    scored_leads: int
    strong_match: int
    partial_match: int
    weak_match: int
    insufficient_evidence: int
    average_fit_score: float
