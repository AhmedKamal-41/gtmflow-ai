from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.lead import Lead
    from app.models.workflow_event import WorkflowEvent


class LeadBatch(Base):
    __tablename__ = "lead_batches"
    __table_args__ = (
        # Phase 3 closeout: the actual convergence guarantee for "same
        # verified source + same selection -> same batch" (including
        # concurrent attempts). `name` is freely editable and was never
        # meant to be an identity key -- the original PDL importer's
        # name-prefix lookup (first 12 chars of the logical key embedded in
        # the batch name) is replaced by this persisted, full, uniquely
        # constrained column. NULL for every CSV/demo batch, unrestricted
        # (multiple NULLs are distinct under a unique constraint on both
        # Postgres and SQLite).
        UniqueConstraint("logical_key", name="uq_lead_batches_logical_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logical_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Phase 4 closeout Part A.4: sha256 of the uploaded CSV's bytes, set on
    # every CSV-upload batch (NULL for PDL/demo batches). Not unique --
    # uploading the same file twice after a *complete* upload legitimately
    # creates a second batch (unchanged behavior, out of this phase's
    # scope). What this DOES enable: retrying an upload that left a batch
    # `"partial"` is detected by matching this hash against an existing
    # `status="partial"` batch, so the retry resumes (skips already-
    # committed rows) instead of creating a new batch and re-inserting
    # rows that already landed. See app/api/batches.py's upload_batch.
    upload_content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(64), default="csv", nullable=False)
    total_leads: Mapped[int] = mapped_column(default=0, nullable=False)
    processed_leads: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="uploaded", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    leads: Mapped[list["Lead"]] = relationship(
        "Lead", back_populates="batch"
    )
    workflow_events: Mapped[list["WorkflowEvent"]] = relationship(
        "WorkflowEvent", back_populates="batch"
    )
