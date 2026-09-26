from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.lead import Lead


# Phase 11 delivery ledger. A delivery row is claimed (status "pending") and
# COMMITTED before any send; `delivery_key` is unique, so one approved draft
# attempt can be claimed by exactly one request, worker or restart. Outcomes:
# "success" / "mock_success" (delivered), "failed" (definitely not delivered),
# "unknown" (may have been delivered -- never resent automatically). Rows from
# before Phase 11 have delivery_key NULL and keep their original status.
PUSH_PENDING = "pending"
PUSH_SUCCESS = "success"
PUSH_MOCK_SUCCESS = "mock_success"
PUSH_FAILED = "failed"
PUSH_UNKNOWN = "unknown"


class IntegrationPush(Base):
    __tablename__ = "integration_pushes"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("leads.id"),
        nullable=False,
        index=True,
    )
    integration_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    # -- Phase 11 delivery ledger (NULL on pre-Phase-11 rows) --------------
    delivery_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    approved_output_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_outputs.id"), nullable=True, index=True
    )
    approved_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "mock" (no webhook configured: nothing leaves the app) or "real".
    delivery_mode: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead: Mapped["Lead"] = relationship("Lead", back_populates="integration_pushes")
