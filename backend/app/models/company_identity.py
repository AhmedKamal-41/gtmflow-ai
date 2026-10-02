from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.lead import Lead
    from app.models.source_snapshot import SourceSnapshot


class CompanyIdentity(Base):
    """A canonical company, resolved (Phase 3+) from one or more Lead rows.

    Deliberately weak identity guarantees, by design, not by omission:

    - ``website_domain`` has NO unique constraint. Businesses can legitimately
      share a domain (franchises, holding companies, dead/reused domains);
      treating it as a global key would incorrectly merge distinct companies.
    - ``source_record_id`` (e.g. a PDL company id) is stored as a hint, not a
      durable primary key -- source providers do not guarantee these persist
      across dataset releases, so it is indexed for lookup convenience only,
      never used as the sole basis for identity resolution.
    - ``source_raw_identity`` preserves the original values as seen in the
      source record (name/website/size/locality/etc. before normalization),
      so a later normalization decision can always be explained or redone.
    - Resolution/merge logic (deciding which Lead rows point at the same
      CompanyIdentity) is out of scope for Phase 2 -- this model is the
      schema hook Phase 3's importer will populate. No existing Lead rows
      are backfilled with a guessed CompanyIdentity by this phase's
      migration; ``Lead.company_identity_id`` stays NULL for all of them.
    """

    __tablename__ = "company_identities"
    __table_args__ = (
        # Idempotent-reimport guarantee (Part D.7): at most one
        # CompanyIdentity per (snapshot, source record identity key).
        # Multiple NULL source_record_id rows are still allowed -- both
        # Postgres and SQLite treat NULLs as distinct under a unique
        # constraint, so this never restricts non-PDL identities (none
        # exist yet, but nothing here assumes that stays true).
        UniqueConstraint(
            "source_snapshot_id",
            "source_record_id",
            name="uq_company_identities_snapshot_source_record",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    website_domain: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_snapshots.id"),
        nullable=True,
        index=True,
    )
    source_record_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    # Phase 3: "source_id" (the source's own record id was present and used
    # directly) or "fingerprint" (no id was present; a deterministic hash of
    # normalized name+locality+region+country stands in -- weaker evidence,
    # honestly labeled per docs/engineering-log's Phase 3 handoff Part D.2). NULL
    # for identities not created by the Phase 3 pipeline.
    identity_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_raw_identity: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
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

    source_snapshot: Mapped["SourceSnapshot | None"] = relationship(
        "SourceSnapshot", back_populates="company_identities"
    )
    leads: Mapped[list["Lead"]] = relationship(
        "Lead", back_populates="company_identity"
    )
