from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.lead import Lead
    from app.models.source_snapshot import SourceSnapshot


class ImportRun(Base):
    """One execution of the (Phase 3) importer against one SourceSnapshot.

    ``error_summary`` is deliberately bounded (a capped list of the first N
    row-level errors, not an unbounded log dump) -- callers writing to it are
    responsible for keeping it small; this model doesn't enforce a size limit
    itself, matching the JSON-column style already used elsewhere (e.g.
    Lead.cleaned_data).
    """

    __tablename__ = "import_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_snapshots.id"),
        nullable=False,
        index=True,
    )
    config_seed: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Phase 3: deterministic hash of (source checksum, normalization mapping
    # version, target counts, seed, country filter) -- identifies "this
    # exact curated selection," independent of how many times it's been
    # attempted. Indexed (not unique) since multiple ImportRun rows
    # legitimately share one logical_key: each invocation is its own
    # auditable attempt (Part D.9); the anti-duplication guarantee itself
    # lives on Lead/CompanyIdentity's (source_snapshot_id, source_record_id)
    # unique constraints, not here.
    logical_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    total_records: Mapped[int | None] = mapped_column(nullable=True)
    imported_count: Mapped[int | None] = mapped_column(nullable=True)
    skipped_count: Mapped[int | None] = mapped_column(nullable=True)
    error_count: Mapped[int | None] = mapped_column(nullable=True)
    error_summary: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        "SourceSnapshot", back_populates="import_runs"
    )
    leads: Mapped[list["Lead"]] = relationship("Lead", back_populates="import_run")
