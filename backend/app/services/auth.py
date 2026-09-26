"""Phase 12: password hashing, login with lockout, and session handling.

* Passwords: scrypt (Python standard library; no new dependency) with a
  16-byte random salt; the cost parameters are stored in each hash, so
  raising PASSWORD_HASH_N later does not break existing hashes. At least 12
  characters.
* Login: generic failure message for every reason (unknown user, wrong
  password, disabled, locked); a dummy hash is computed for unknown users so
  response time does not reveal which usernames exist. 5 consecutive
  failures lock the account for 15 minutes.
* Sessions: a new 256-bit random token per login (session fixation is
  impossible: the old session, if any, is revoked); only its SHA-256 is
  stored. Idle timeout and absolute lifetime from settings. Logout, password
  change and disabling a user revoke sessions.
* CSRF: state-changing requests must send X-CSRF-Token, a value derived from
  the session token that only a same-origin page can read (from
  GET /api/auth/session); a cross-site form cannot.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.auth import ROLES, User, UserSession

MIN_PASSWORD_LENGTH = 12
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
LAST_SEEN_WRITE_SECONDS = 60
GENERIC_LOGIN_ERROR = "Invalid username or password."
_DUMMY_HASH: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------ passwords

def hash_password(password: str, n: int | None = None) -> str:
    n = n or settings.password_hash_n
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=8, p=1, dklen=32, maxmem=256 * 1024 * 1024)
    return "scrypt${}${}${}${}${}".format(n, 8, 1, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected), maxmem=256 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


# ------------------------------------------------------------ users (CLI)

def create_user(session: Session, username: str, password: str, role: str) -> User:
    name = normalize_username(username)
    if not name or len(name) > 40 or not all(c.isalnum() or c in "._-" for c in name):
        raise ValueError("Username: 1-40 characters, letters, digits, '.', '_' or '-'.")
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
    validate_new_password(password)
    if session.scalar(select(User).where(User.username == name)) is not None:
        raise ValueError(f"User '{name}' already exists.")
    user = User(username=name, password_hash=hash_password(password), role=role, is_active=True,
                failed_login_count=0, password_changed_at=_now())
    session.add(user)
    session.flush()
    return user


def revoke_user_sessions(session: Session, user_id) -> int:
    result = session.execute(update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
                             .values(revoked_at=_now()).execution_options(synchronize_session=False))
    return result.rowcount or 0


def set_password(session: Session, user: User, password: str) -> None:
    validate_new_password(password)
    user.password_hash = hash_password(password)
    user.password_changed_at = _now()
    user.failed_login_count, user.locked_until = 0, None
    revoke_user_sessions(session, user.id)


def set_active(session: Session, user: User, active: bool) -> None:
    user.is_active = active
    if not active:
        revoke_user_sessions(session, user.id)


# ------------------------------------------------------------ login / sessions

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token_for(token: str) -> str:
    return hashlib.sha256(b"gtmflow-csrf:" + token.encode()).hexdigest()


class LoginFailed(Exception):
    pass


def login(session: Session, username: str, password: str, *, client_ip: str | None,
          user_agent: str | None, previous_token: str | None = None) -> tuple[User, str, UserSession]:
    global _DUMMY_HASH
    user = session.scalar(select(User).where(User.username == normalize_username(username)))
    if user is None:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password("dummy-password-for-timing")
        verify_password(password, _DUMMY_HASH)
        raise LoginFailed()
    now = _now()
    locked = _aware(user.locked_until) is not None and _aware(user.locked_until) > now
    ok = verify_password(password, user.password_hash)  # computed even when locked: same timing
    if locked or not user.is_active or not ok:
        if not locked and user.is_active:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_login_count = 0
        session.commit()
        raise LoginFailed()
    if previous_token:
        session.execute(update(UserSession).where(UserSession.token_hash == token_hash(previous_token),
                                                  UserSession.revoked_at.is_(None))
                        .values(revoked_at=now).execution_options(synchronize_session=False))
    user.failed_login_count, user.locked_until, user.last_login_at = 0, None, now
    token = secrets.token_urlsafe(32)
    row = UserSession(user_id=user.id, token_hash=token_hash(token), created_at=now, last_seen_at=now,
                      expires_at=now + timedelta(hours=settings.session_absolute_hours),
                      client_ip=(client_ip or "")[:64] or None, user_agent=(user_agent or "")[:200] or None)
    session.add(row)
    session.commit()
    return user, token, row


@dataclass(frozen=True)
class ResolvedSession:
    user_id: str
    username: str
    role: str
    session_id: str
    expires_at: datetime
    csrf_token: str


def resolve(session: Session, token: str | None) -> ResolvedSession | None:
    """The live session for a cookie token, or None (unknown, revoked,
    expired, idle too long, or the user is disabled). Updates last_seen at
    most once a minute."""
    if not token:
        return None
    row = session.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
    if row is None or row.revoked_at is not None:
        return None
    now = _now()
    user = row.user
    if (not user.is_active or _aware(row.expires_at) <= now
            or now - _aware(row.last_seen_at) > timedelta(minutes=settings.session_idle_minutes)
            or _aware(row.created_at) < _aware(user.password_changed_at)):
        return None
    if now - _aware(row.last_seen_at) > timedelta(seconds=LAST_SEEN_WRITE_SECONDS):
        session.execute(update(UserSession).where(UserSession.id == row.id).values(last_seen_at=now)
                        .execution_options(synchronize_session=False))
        session.commit()
    return ResolvedSession(str(user.id), user.username, user.role, str(row.id), _aware(row.expires_at),
                           csrf_token_for(token))


def logout(session: Session, token: str | None) -> None:
    if token:
        session.execute(update(UserSession).where(UserSession.token_hash == token_hash(token),
                                                  UserSession.revoked_at.is_(None))
                        .values(revoked_at=_now()).execution_options(synchronize_session=False))
        session.commit()
