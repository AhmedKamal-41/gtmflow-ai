from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base


class SellerProfile(Base):
    """Immutable draft revisions for the single workspace's seller profile.

    Saving a draft does not activate it for generation or change fit scoring.
    The API only creates revisions; previous content remains available.
    Activation is a separate, explicit step (``SellerProfileActivation``).
    """

    __tablename__ = "seller_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    editor_label: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class SellerProfileActivation(Base):
    """Append-only log of which seller-profile revision is active.

    The row with the highest ``sequence`` is the current state. A row with
    ``seller_profile_id`` NULL records a deactivation. Saving a new draft
    never writes here, so a draft cannot silently become active.
    ``reviewed_confirmation`` records the operator's explicit statement at
    activation time; the app has no authentication, so it is not a verified
    identity (``actor_label`` says so).
    """

    __tablename__ = "seller_profile_activations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    seller_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("seller_profiles.id"), nullable=True, index=True
    )
    seller_profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewed_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    demo_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actor_label: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    seller_profile: Mapped["SellerProfile | None"] = relationship("SellerProfile")
