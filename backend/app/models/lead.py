from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.ai_output import AIOutput
    from app.models.ai_output_review import AIOutputReview
    from app.models.company_identity import CompanyIdentity
    from app.models.import_run import ImportRun
    from app.models.integration_push import IntegrationPush
    from app.models.lead_batch import LeadBatch
    from app.models.lead_fit_score import LeadFitScore
    from app.models.lead_score import LeadScore
    from app.models.source_snapshot import SourceSnapshot
    from app.models.workflow_event import WorkflowEvent


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        # Idempotent-reimport guarantee (Part D.7): at most one Lead per
        # (snapshot, source record identity key) -- reimporting the same
        # PDL snapshot never creates a second operational Lead for a
        # company already imported. Multiple NULL rows (every CSV/demo
        # lead) remain unrestricted, same reasoning as CompanyIdentity's
        # equivalent constraint.
        UniqueConstraint(
            "source_snapshot_id",
            "source_record_id",
            name="uq_leads_snapshot_source_record",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("lead_batches.id"),
        nullable=False,
        index=True,
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    contact_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_size: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="new", nullable=False, index=True
    )
    cleaned_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # -- Phase 2 additions: provenance + canonical identity ---------------
    # All nullable and untouched by the CSV-upload and demo-seed paths, so
    # synthetic/demo leads never carry PDL provenance (docs/engineering-log/audit.md
    # Part C invariant). Populated only by the Phase 3 PDL importer.
    company_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("company_identities.id"),
        nullable=True,
        index=True,
    )
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_snapshots.id"),
        nullable=True,
        index=True,
    )
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_runs.id"),
        nullable=True,
        index=True,
    )
    source_record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_raw_data: Mapped[dict[str, Any] | None] = mapped_column(
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

    batch: Mapped["LeadBatch"] = relationship("LeadBatch", back_populates="leads")
    score: Mapped["LeadScore | None"] = relationship(
        "LeadScore", back_populates="lead", uselist=False
    )
    fit_scores: Mapped[list["LeadFitScore"]] = relationship(
        "LeadFitScore", back_populates="lead"
    )
    ai_outputs: Mapped[list["AIOutput"]] = relationship(
        "AIOutput", back_populates="lead"
    )
    workflow_events: Mapped[list["WorkflowEvent"]] = relationship(
        "WorkflowEvent", back_populates="lead"
    )
    integration_pushes: Mapped[list["IntegrationPush"]] = relationship(
        "IntegrationPush", back_populates="lead"
    )
    ai_output_reviews: Mapped[list["AIOutputReview"]] = relationship(
        "AIOutputReview", back_populates="lead"
    )
    company_identity: Mapped["CompanyIdentity | None"] = relationship(
        "CompanyIdentity", back_populates="leads"
    )
    source_snapshot: Mapped["SourceSnapshot | None"] = relationship(
        "SourceSnapshot", back_populates="leads"
    )
    import_run: Mapped["ImportRun | None"] = relationship(
        "ImportRun", back_populates="leads"
    )
