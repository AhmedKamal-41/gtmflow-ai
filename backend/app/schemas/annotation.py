from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.ai_output import AIOutputRead

MAX_WALL_MS = 24 * 60 * 60 * 1000

TIMING_FLAGS = Literal[
    "was_hidden",
    "had_idle_gap",
    "resumed_after_failure",
    "content_changed_during_session",
]


class ReviewTiming(BaseModel):
    """Measured in the browser from actual UI activity only (see
    frontend/src/hooks/useReviewTimer.ts): `active_ms` counts visible,
    non-idle time; `wall_ms` is open-to-submit; hidden and idle time are
    reported separately. Never derived from database timestamps."""

    model_config = ConfigDict(extra="forbid")

    active_ms: int = Field(ge=0, le=MAX_WALL_MS)
    wall_ms: int = Field(ge=0, le=MAX_WALL_MS)
    hidden_ms: int = Field(ge=0, le=MAX_WALL_MS)
    idle_ms: int = Field(ge=0, le=MAX_WALL_MS)
    interaction_count: int = Field(ge=0, le=1_000_000)
    idle_threshold_ms: int = Field(ge=1000, le=30 * 60 * 1000)
    flags: list[TIMING_FLAGS] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def consistent(self) -> "ReviewTiming":
        if self.active_ms > self.wall_ms:
            raise ValueError("active_ms cannot exceed wall_ms")
        if self.hidden_ms > self.wall_ms or self.idle_ms > self.wall_ms:
            raise ValueError("hidden_ms and idle_ms cannot exceed wall_ms")
        return self


class AnnotationSubmit(BaseModel):
    """One human decision on the exact candidate output displayed.

    Assessments rate the ORIGINAL model output. `accepted` requires it to be
    fully supported; `corrected` stores `corrected_content` as a new
    immutable revision that becomes the target; `skipped` excludes the
    example and needs a reason. No reviewer name is accepted.
    """

    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    source_output_id: UUID
    source_content_hash: str = Field(min_length=64, max_length=64)
    decision: Literal["accepted", "corrected", "skipped"]
    corrected_content: dict[str, Any] | None = None
    factual_support: Literal["supported", "partially_supported", "unsupported"] | None = None
    writing_quality: int | None = Field(default=None, ge=1, le=5)
    missing_info_handling: Literal["good", "acceptable", "poor"] | None = None
    notes: str | None = Field(default=None, max_length=4000)
    skip_reason: str | None = Field(default=None, max_length=1000)
    timing: ReviewTiming | None = None

    @model_validator(mode="after")
    def decision_rules(self) -> "AnnotationSubmit":
        assessed = (self.factual_support, self.writing_quality, self.missing_info_handling)
        if self.decision in ("accepted", "corrected") and None in assessed:
            raise ValueError("factual_support, writing_quality and missing_info_handling are required")
        if self.decision == "accepted" and self.factual_support != "supported":
            raise ValueError("only a fully supported output can be accepted as the target; write a correction instead")
        if self.decision == "corrected" and not self.corrected_content:
            raise ValueError("corrected_content is required for a correction")
        if self.decision != "corrected" and self.corrected_content is not None:
            raise ValueError("corrected_content is only allowed with decision='corrected'")
        if self.decision == "skipped" and not (self.skip_reason or "").strip():
            raise ValueError("skip_reason is required when skipping")
        return self


class GenerateCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["mock", "openai"]


class TrainingAnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    submission_id: UUID
    candidate_id: UUID
    source_output_id: UUID
    source_content_hash: str
    decision: str
    target_output_id: UUID | None
    target_content_hash: str | None
    factual_support: str | None
    writing_quality: int | None
    missing_info_handling: str | None
    notes: str | None
    skip_reason: str | None
    reviewer_label: str
    review_mode: str
    timing: dict[str, Any] | None
    created_at: datetime


class CandidateSummary(BaseModel):
    id: UUID
    queue: str
    position: int
    task: str
    split: str
    group_key: str
    lead_id: UUID
    company_name: str
    status: str  # awaiting_generation | pending_review | accepted | corrected | skipped
    source_output_id: UUID | None
    is_mock: bool | None


class CandidateDetail(CandidateSummary):
    manifest_version: str
    lead_facts: dict[str, Any]
    source: dict[str, Any]
    source_output: AIOutputRead | None
    target_output: AIOutputRead | None
    latest_annotation: TrainingAnnotationRead | None
    annotation_count: int


class ProviderInfo(BaseModel):
    configured_provider: str
    model_revision: str | None
    is_mock: bool
    available: bool
    detail: str


class AnnotationSummary(BaseModel):
    queue: str
    manifest_version: str | None
    candidates: int
    unique_companies: int
    generated: int
    awaiting_generation: int
    pending_review: int
    reviewed_examples: int
    reviewed_unique_companies: int
    accepted: int
    corrected: int
    skipped: int
    mock_candidates: int
    by_task: dict[str, int]
    by_split: dict[str, int]
    experiment_targets: dict[str, int]
