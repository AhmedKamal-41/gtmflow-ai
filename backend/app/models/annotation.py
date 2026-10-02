"""Phase 6: split assignment manifests and the training-annotation queue.

Kept structurally separate from operational outreach review
(`AIOutputReview`): an annotation never approves a draft for delivery, and
an operational approval never creates a training example.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

SPLIT_TRAIN = "train"
SPLIT_VALIDATION = "validation"
SPLIT_TEST = "test"

TASK_SUMMARY = "company_summary"
TASK_OUTREACH = "outreach_email"

DECISION_ACCEPTED = "accepted"
DECISION_CORRECTED = "corrected"
DECISION_SKIPPED = "skipped"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SplitManifest(Base):
    """One frozen, seeded company-group split assignment. Immutable: a
    manifest version is created once and never rewritten, so reviewed
    companies can't be silently reassigned."""

    __tablename__ = "split_manifests"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    seed: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(200), nullable=False)
    ratios: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    lead_count: Mapped[int] = mapped_column(Integer, nullable=False)
    group_count: Mapped[int] = mapped_column(Integer, nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class CompanySplitAssignment(Base):
    __tablename__ = "company_split_assignments"
    __table_args__ = (
        UniqueConstraint("manifest_version", "lead_id", name="uq_company_split_assignments_manifest_lead"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manifest_version: Mapped[str] = mapped_column(
        String(64), ForeignKey("split_manifests.version"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True
    )
    # Explicit name: the naming convention's would exceed Postgres's 63-char
    # identifier limit.
    company_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("company_identities.id", name="fk_split_assignments_company_identity_id"),
        nullable=True,
    )
    group_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    split: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class AnnotationCandidate(Base):
    """One (company, task) example reserved for human annotation.

    `source_output_id` is NULL until a candidate output is generated with an
    explicitly chosen provider; it is attached once and never replaced.
    """

    __tablename__ = "annotation_candidates"
    __table_args__ = (
        UniqueConstraint("queue", "lead_id", "task", name="uq_annotation_candidates_queue_lead_task"),
        UniqueConstraint("queue", "position", name="uq_annotation_candidates_queue_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_version: Mapped[str] = mapped_column(
        String(64), ForeignKey("split_manifests.version"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True
    )
    group_key: Mapped[str] = mapped_column(String(64), nullable=False)
    split: Mapped[str] = mapped_column(String(16), nullable=False)
    task: Mapped[str] = mapped_column(String(32), nullable=False)
    source_output_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_outputs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    source_output = relationship("AIOutput", foreign_keys=[source_output_id])
    lead = relationship("Lead")


class TrainingAnnotation(Base):
    """One human annotation decision on one candidate. Append-only: a
    changed decision is a new row; the latest row per candidate is
    authoritative. `submission_id` (client-generated) makes retries of the
    same submission idempotent."""

    __tablename__ = "training_annotations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, unique=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("annotation_candidates.id"), nullable=False, index=True
    )
    source_output_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_outputs.id"), nullable=False
    )
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    target_output_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_outputs.id"), nullable=True
    )
    target_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    factual_support: Mapped[str | None] = mapped_column(String(24), nullable=True)
    writing_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missing_info_handling: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_label: Mapped[str] = mapped_column(String(64), nullable=False)
    review_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    timing: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    candidate = relationship("AnnotationCandidate")
