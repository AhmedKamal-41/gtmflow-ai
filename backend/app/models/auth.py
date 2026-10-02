"""Phase 12: operator accounts and login sessions (single workspace).

Accounts are created with the operator CLI (`python -m app.auth_cli`), by
self-service sign-up with an emailed verification code, or as one-click guest
accounts; sign-up and guests are off unless enabled in settings. Passwords are stored as scrypt hashes with
a per-user salt and their cost parameters. A session stores only the SHA-256
of its random token (the cookie value), so a database read does not yield a
usable session.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base

ROLE_OPERATOR = "operator"  # every read and action
ROLE_VIEWER = "viewer"      # read-only
ROLE_GUEST = "guest"        # operator actions only while the server is mock-only
ROLES = (ROLE_OPERATOR, ROLE_VIEWER)  # roles the CLI and sign-up may grant

CREATED_VIA_SIGNUP = "signup"
CREATED_VIA_GUEST = "guest"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # CLI usernames are short identifiers; sign-up accounts use their email.
    username: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_OPERATOR)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    # NULL for CLI accounts (created before or without sign-up).
    created_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["UserSession"]] = relationship("UserSession", back_populates="user")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)

    user: Mapped[User] = relationship("User", back_populates="sessions")


class LoginThrottle(Base):
    """Shared across API processes; the key hashes the connection's peer IP.

    No username or supplied credentials are retained. Expired entries are
    removed during later attempts, so this is not a permanent visitor log.
    """
    __tablename__ = "login_throttles"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)


class EmailVerification(Base):
    """The one outstanding sign-up code for an account.

    Only a hash of the 6-digit code is stored. A code expires, allows a
    bounded number of wrong guesses, and can be re-sent only after a cooldown.
    """
    __tablename__ = "email_verifications"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
