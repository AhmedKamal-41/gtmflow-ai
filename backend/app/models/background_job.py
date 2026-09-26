"""Phase 10: durable background jobs (docs/upgrade/audit.md F.5).

A job is one batch-level operation (fit scoring, legacy scoring, summary or
outreach generation, Slack push of Hot leads). Its work is split into one
`BackgroundJobItem` per lead, and each item's effect is committed in the SAME
transaction as the item's status -- so a worker that dies mid-job leaves
every item either fully done or still pending, and the next worker resumes
from the pending ones.

Ownership is a lease: a worker claims a job by a conditional UPDATE (only a
queued job, or a running job whose lease has expired, can be claimed), and
renews the lease after every item. A job reclaimed more than `max_attempts`
times (a crash loop) fails instead of running forever.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"  # every item processed; counts say how each ended
JOB_FAILED = "failed"        # stopped by a job-level error (config, missing batch, crash loop)
JOB_CANCELLED = "cancelled"
ACTIVE_JOB_STATUSES = (JOB_QUEUED, JOB_RUNNING)

ITEM_PENDING = "pending"
ITEM_SUCCEEDED = "succeeded"
ITEM_SKIPPED = "skipped"
ITEM_BLOCKED = "blocked"
ITEM_FAILED = "failed"
ITEM_FINAL_STATUSES = (ITEM_SUCCEEDED, ITEM_SKIPPED, ITEM_BLOCKED, ITEM_FAILED)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("lead_batches.id"), nullable=True, index=True
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JOB_QUEUED, index=True)
    # Set while the job is queued/running so the same work can't be queued
    # twice at once; cleared when it finishes (NULLs never collide).
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # claims
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_item_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    total_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Phase 12: who queued the job ("user:<name>"); NULL for jobs queued before.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["BackgroundJobItem"]] = relationship("BackgroundJobItem", back_populates="job")


class BackgroundJobItem(Base):
    __tablename__ = "background_job_items"
    __table_args__ = (UniqueConstraint("job_id", "lead_id", name="uq_background_job_items_job_lead"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("background_jobs.id"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ITEM_PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    job: Mapped[BackgroundJob] = relationship("BackgroundJob", back_populates="items")
