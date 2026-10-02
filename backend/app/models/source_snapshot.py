from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.company_identity import CompanyIdentity
    from app.models.import_run import ImportRun
    from app.models.lead import Lead


class SourceSnapshot(Base):
    """One specific pull of an external dataset (e.g. one PDL/HF release).

    Deliberately keeps the *source's* claimed acquisition date
    (``reported_acquisition_date``) separate from ``retrieved_at`` (when we
    actually pulled it) -- these answer different questions and must never
    be collapsed into one field. ``checksum`` is nullable and only ever set
    when a checksum was actually computed against the retrieved file; a NULL
    here means "not computed," never "verified empty."

    Phase 3 closeout: ``uq_source_snapshots_provider_checksum`` is the real
    convergence guarantee for "same verified source -> same snapshot,"
    including under concurrent import attempts -- the application-level
    find-or-create check alone had a TOCTOU race (two simultaneous imports
    could both SELECT-find-nothing, then both INSERT). The importer now
    catches the resulting IntegrityError and re-reads the winning row rather
    than raising or duplicating. Multiple NULL-checksum rows remain
    unrestricted (distinct under a unique constraint on both dialects), but
    in practice every PDL snapshot created going forward has a mandatory,
    validated checksum -- see app/pdl/cli.py's ``validate_snapshot_info``.
    """

    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider", "checksum", name="uq_source_snapshots_provider_checksum"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mirror_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reported_acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    retrieved_license: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retrieved_attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checksum_algorithm: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    import_runs: Mapped[list["ImportRun"]] = relationship(
        "ImportRun", back_populates="source_snapshot"
    )
    company_identities: Mapped[list["CompanyIdentity"]] = relationship(
        "CompanyIdentity", back_populates="source_snapshot"
    )
    leads: Mapped[list["Lead"]] = relationship(
        "Lead", back_populates="source_snapshot"
    )
