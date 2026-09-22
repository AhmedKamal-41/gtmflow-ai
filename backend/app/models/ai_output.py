from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.ai_output_review import AIOutputReview
    from app.models.lead import Lead

ORIGIN_GENERATED = "generated"
ORIGIN_HUMAN_EDITED = "human_edited"


class AIOutput(Base):
    """One immutable generation or human edit of a lead's AI content.

    Every row is a distinct, identified revision -- ``id`` IS the exact
    output/revision identity referenced by reviews (Part D). A human edit of
    a draft creates a NEW row with ``origin="human_edited"`` and
    ``parent_output_id`` pointing at the row it was edited from; the
    original row is never mutated in place.

    Provenance columns (``input_snapshot``, ``input_hash``,
    ``output_schema_version``, ``model_revision``, ``adapter_revision``) are
    populated for every NEW generation from this migration forward. For
    rows that existed before this phase, they are left NULL rather than
    backfilled with a reconstructed guess -- the lead may have changed since
    the original generation, so we cannot honestly reconstruct what input
    produced that historical output. ``origin`` is backfilled to
    ``"generated"`` for historical rows, which is a true structural fact
    (no edit capability existed before this phase), not a guess.
    """

    __tablename__ = "ai_outputs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("leads.id"),
        nullable=False,
        index=True,
    )
    output_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # -- Phase 2 additions: identity + provenance -------------------------
    parent_output_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_outputs.id"), nullable=True, index=True
    )
    origin: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ORIGIN_GENERATED
    )
    input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_schema_version: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    model_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    adapter_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    lead: Mapped["Lead"] = relationship("Lead", back_populates="ai_outputs")
    parent_output: Mapped["AIOutput | None"] = relationship(
        "AIOutput", remote_side=[id]
    )
    reviews: Mapped[list["AIOutputReview"]] = relationship(
        "AIOutputReview", back_populates="ai_output"
    )
