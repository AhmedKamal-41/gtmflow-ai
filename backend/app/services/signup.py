"""Self-service sign-up with an emailed code, and one-click guest access.

Sign-up creates an unverified account (username = email) and issues a
6-digit code. The account cannot sign in until the code is entered:
* only a hash of the code is stored, bound to the account;
* it expires after CODE_TTL_MINUTES and allows MAX_CODE_ATTEMPTS wrong tries;
* a new code can be sent only after RESEND_COOLDOWN_SECONDS.
Responses never reveal whether an address already has an account.

Guest access creates a fresh account per visitor, with no password and a
short session. Guests can change data only while the server is mock-only
(enforced in app/api/auth.py), so a guest can never reach a real AI
provider or Slack. Both features are off unless enabled in settings.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.auth import (
    CREATED_VIA_GUEST,
    CREATED_VIA_SIGNUP,
    ROLE_GUEST,
    ROLE_VIEWER,
    ROLES,
    EmailVerification,
    User,
    UserSession,
)
from app.services.auth import (
    _aware,
    _event,
    _limit_peer,
    _now,
    hash_password,
    normalize_username,
    start_session,
    validate_new_password,
)

CODE_TTL_MINUTES = 10
MAX_CODE_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
GUEST_PASSWORD_HASH = "!"  # not a valid hash: a guest can never sign in with a password
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,63}$")


class FeatureDisabled(Exception):
    pass


class DomainNotAllowed(Exception):
    pass


class VerificationFailed(Exception):
    pass


def normalize_email(email: str) -> str:
    value = normalize_username(email)
    if len(value) > 254 or not EMAIL_PATTERN.match(value):
        raise ValueError("Enter a valid email address.")
    return value


def _code_hash(user: User, code: str) -> str:
    return hashlib.sha256(f"gtmflow-email-code:{user.id}:{code}".encode()).hexdigest()


def _pending_signup(session: Session, email: str) -> User | None:
    user = session.scalar(select(User).where(User.username == email))
    if user is None or user.created_via != CREATED_VIA_SIGNUP or user.email_verified_at is not None:
        return None
    return user


def _issue_code(session: Session, user: User) -> str | None:
    """A new code for `user`, or None while the resend cooldown runs."""
    now = _now()
    row = session.get(EmailVerification, user.id)
    if row is not None and now - _aware(row.last_sent_at) < timedelta(seconds=RESEND_COOLDOWN_SECONDS):
        return None
    code = f"{secrets.randbelow(10**6):06d}"
    if row is None:
        row = EmailVerification(user_id=user.id)
        session.add(row)
    row.code_hash = _code_hash(user, code)
    row.expires_at = now + timedelta(minutes=CODE_TTL_MINUTES)
    row.attempts = 0
    row.last_sent_at = now
    _event(session, "auth_verification_code_sent", user.id)
    return code


def register(session: Session, email: str, password: str, *, client_ip: str | None) -> tuple[str, str] | None:
    """Create or refresh an unverified account. Returns (email, code) when a
    code should be emailed, None when nothing should be sent."""
    if not settings.self_signup_enabled:
        raise FeatureDisabled()
    _limit_peer(session, client_ip, "signup")
    address = normalize_email(email)
    domain = address.rsplit("@", 1)[1]
    if settings.signup_allowed_email_domains and domain not in settings.signup_allowed_email_domains:
        raise DomainNotAllowed()
    validate_new_password(password)
    existing = session.scalar(select(User).where(User.username == address))
    if existing is not None and _pending_signup(session, address) is None:
        return None  # already a verified (or CLI) account: say nothing
    if existing is None:
        role = settings.self_signup_role if settings.self_signup_role in ROLES else ROLE_VIEWER
        existing = User(username=address, password_hash=hash_password(password), role=role, is_active=True,
                        failed_login_count=0, password_changed_at=_now(), created_via=CREATED_VIA_SIGNUP)
        session.add(existing)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()  # a simultaneous sign-up for the same address won
            return None
        _event(session, "auth_user_signed_up", existing.id, role=role)
    else:
        # Re-registering an unverified address replaces its password; the
        # account still needs a code sent to that mailbox.
        existing.password_hash = hash_password(password)
        existing.password_changed_at = _now()
    code = _issue_code(session, existing)
    session.commit()
    return (address, code) if code else None


def resend_code(session: Session, email: str, *, client_ip: str | None) -> tuple[str, str] | None:
    if not settings.self_signup_enabled:
        raise FeatureDisabled()
    _limit_peer(session, client_ip, "signup")
    try:
        address = normalize_email(email)
    except ValueError:
        return None
    user = _pending_signup(session, address)
    code = _issue_code(session, user) if user is not None else None
    session.commit()
    return (address, code) if code else None


def verify(session: Session, email: str, code: str, *, client_ip: str | None, user_agent: str | None,
           previous_token: str | None = None) -> tuple[User, str, UserSession]:
    if not settings.self_signup_enabled:
        raise FeatureDisabled()
    _limit_peer(session, client_ip, "verify")
    try:
        address = normalize_email(email)
    except ValueError:
        raise VerificationFailed() from None
    user = _pending_signup(session, address)
    row = session.get(EmailVerification, user.id) if user is not None else None
    now = _now()
    if user is None or row is None or _aware(row.expires_at) <= now or row.attempts >= MAX_CODE_ATTEMPTS:
        session.rollback()
        raise VerificationFailed()
    if not hmac.compare_digest(_code_hash(user, code.strip()), row.code_hash):
        row.attempts += 1
        session.commit()
        raise VerificationFailed()
    user.email_verified_at = now
    user.last_login_at = now
    session.delete(row)
    _event(session, "auth_email_verified", user.id)
    token, session_row = start_session(session, user, client_ip=client_ip, user_agent=user_agent,
                                       previous_token=previous_token, now=now)
    return user, token, session_row


def create_guest(session: Session, *, client_ip: str | None, user_agent: str | None,
                 previous_token: str | None = None) -> tuple[User, str, UserSession]:
    if not settings.guest_access_enabled:
        raise FeatureDisabled()
    _limit_peer(session, client_ip, "guest")
    now = _now()
    user = User(username=f"guest-{secrets.token_hex(5)}", password_hash=GUEST_PASSWORD_HASH, role=ROLE_GUEST,
                is_active=True, failed_login_count=0, password_changed_at=now, created_via=CREATED_VIA_GUEST,
                last_login_at=now)
    session.add(user)
    session.flush()
    _event(session, "auth_guest_created", user.id)
    token, row = start_session(session, user, client_ip=client_ip, user_agent=user_agent,
                               previous_token=previous_token, hours=settings.guest_session_hours, now=now)
    return user, token, row
