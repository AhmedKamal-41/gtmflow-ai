from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.lead import Lead


class LeadFitScore(Base):
    """Phase 4 (Part D.1): the v2 deterministic company-fit scorer's
    versioned, historical storage -- deliberately NOT unique on `lead_id`,
    unlike the legacy v1 `lead_scores` table (which this does not touch or
    replace). Every scoring run inserts a NEW row rather than overwriting
    the previous one, so:

      * v1's single mutable `LeadScore` row and its "Hot/Warm/Cold"
        interpretation are fully preserved, untouched, still authoritative
        for every existing consumer (API responses, push gating, metrics).
      * v2's rescoring a lead (e.g. after a rubric version bump) never
        destroys the prior result -- both remain queryable, each stamped
        with the exact versions that produced it.
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
