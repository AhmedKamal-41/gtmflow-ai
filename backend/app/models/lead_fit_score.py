from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.lead import Lead


class LeadFitScore(Base):
    """Versioned, append-only lead rankings (app/scoring/fit.py) --
    deliberately NOT unique on `lead_id`. Every ranking run inserts a NEW
    row rather than overwriting the previous one, so:

      * a row is scoped to the lead and to the seller-profile revision
        (`seller_profile_id`, `profile_id`/`profile_version`) and the
        target preferences (`targeting_hash`) it was ranked against;
      * changing target preferences never destroys the prior result -- it
        stays queryable, stamped with what produced it, and simply stops
        being current (app/services/fit_queries.py);
      * rows from before profile-based ranking (profile
        `demo-us-sectors-v1`, no targeting_hash) are kept as history and
        are never current.
      * "unique scored leads" under a given profile/version is
        COUNT(DISTINCT lead_id) filtered to that profile_id/profile_version,
        not COUNT(*) -- rescoring once must not inflate the count (Part
        D.6). See app/scoring/fit.py for the scorer itself and
        app/api/fit_scoring.py for the read/query helpers.

    Company-fit evaluation is data only -- writing a row here never
    touches `Lead.status`, `LeadBatch.status`, or any AIOutputReview (Part
    D.3): no blocked disposition is cleared, no partial-import state is
    replaced, no draft is approved, no company is marked contactable by
    virtue of being scored or rescored.
    """

    __tablename__ = "lead_fit_scores"
    # Declared here so the model matches migration 0006 exactly (without
    # it, `alembic check` / autogenerate would propose dropping the index).
    # Serves the "latest row per lead for this profile" lookups in
    # app/services/fit_queries.py.
    __table_args__ = (
        Index(
            "ix_lead_fit_scores_lead_profile_created",
            "lead_id", "profile_id", "profile_version", "created_at",
        ),
        Index("ix_lead_fit_scores_lead_targeting_created", "lead_id", "targeting_hash", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("leads.id"),
        nullable=False,
        index=True,
    )

    # ---- versions (Part D.2): identical inputs under identical versions
    # must reproduce identical results -- these four fields, together with
    # `input_fingerprint`, are what a later reader checks to confirm that.
    scorer_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    profile_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    normalization_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Migration 0015: the seller revision (NULL for the built-in demo
    # profile and for pre-0015 rows) and the hash of the target preferences,
    # weights and rules the row was ranked with (NULL before 0015).
    seller_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("seller_profiles.id"), nullable=True, index=True
    )
    targeting_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # ---- Part C outputs
    fit_score: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    evidence_coverage_pct: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    readiness: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    eligibility_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    eligibility_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computation_ms: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    lead: Mapped["Lead"] = relationship("Lead", back_populates="fit_scores")
