from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.ai_output import AIOutput
    from app.models.lead import Lead

REVIEW_KIND_OPERATIONAL_OUTREACH = "operational_outreach"
REVIEW_KIND_TRAINING_ANNOTATION = "training_annotation"


class AIOutputReview(Base):
    """A single review decision against one exact, identified AIOutput.

    Immutable once written: a changed decision (e.g. approve -> reject) is a
    NEW row, never an update to an existing one, so the full review history
    for a draft is reconstructable and a later decision remains its own
    auditable event (Part D point 8).

    ``ai_output_id`` is nullable to support ``legacy_unlinked`` rows: the
    Phase 2 migration backfills this table from pre-Phase-2 WorkflowEvent
    rows, and only links a legacy event to a specific output when that
    target can be proven (the event's stored ``ai_output_id`` resolves to a
    real AIOutput belonging to the same lead). When it can't be proven, the
    row is kept with ``ai_output_id=NULL`` and ``legacy_unlinked=True``
    rather than guessing via "latest output" or timestamp proximity.

    ``review_kind`` keeps day-to-day operational approve/reject (this phase)
    structurally distinct from the future training-annotation workflow
    (Phase 6/7) -- both will live in this table, but must never be conflated
    when exporting training examples.

    ``reviewer_label`` is an honest, non-authenticated label. This
    application has no auth (see docs/upgrade/audit.md F.1); every review
    created through the current UI is stamped with a fixed constant
    (`"local-demo-unauthenticated"`) rather than a browser-supplied name
    dressed up as an identity.
    """

    __tablename__ = "ai_output_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True
    )
    ai_output_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_outputs.id"), nullable=True, index=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=REVIEW_KIND_OPERATIONAL_OUTREACH,
        index=True,
    )
    reviewer_label: Mapped[str] = mapped_column(String(64), nullable=False)
    legacy_unlinked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    lead: Mapped["Lead"] = relationship("Lead", back_populates="ai_output_reviews")
    ai_output: Mapped["AIOutput | None"] = relationship(
        "AIOutput", back_populates="reviews"
    )
