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
from threading import BoundedSemaphore
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.actor import Actor, reset_actor, set_actor
from app.models.auth import CREATED_VIA_SIGNUP, ROLES, LoginThrottle, User, UserSession
from app.models.workflow_event import WorkflowEvent

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
LOGIN_WINDOW_SECONDS = 300
MAX_PEER_ATTEMPTS = 30
LAST_SEEN_WRITE_SECONDS = 60
GENERIC_LOGIN_ERROR = "Invalid username or password."
_DUMMY_HASH: str | None = None
# scrypt N=2^15, r=8, p=3 is OWASP's 32 MiB option. Bound simultaneous
# hashing within each process as well as the shared login request rate.
PASSWORD_HASH_P = 3
_HASH_SLOTS = BoundedSemaphore(2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------ passwords

def hash_password(password: str, n: int | None = None) -> str:
    n = n or settings.password_hash_n
    salt = secrets.token_bytes(16)
    with _HASH_SLOTS:
        digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=8, p=PASSWORD_HASH_P,
                                dklen=32, maxmem=256 * 1024 * 1024)
    return "scrypt${}${}${}${}${}".format(n, 8, PASSWORD_HASH_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        with _HASH_SLOTS:
            actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                    dklen=len(expected), maxmem=256 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


# ------------------------------------------------------------ users (CLI)

def _event(session: Session, event_type: str, user_id, **data) -> None:
    session.add(WorkflowEvent(event_type=event_type,
                              event_data={"target_user_id": str(user_id), **data}))


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
    _event(session, "auth_user_created", user.id, role=role)
    return user


def revoke_user_sessions(session: Session, user_id) -> int:
    result = session.execute(update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
                             .values(revoked_at=_now()).execution_options(synchronize_session=False))
    count = result.rowcount or 0
    _event(session, "auth_sessions_revoked", user_id, count=count)
    return count


def set_password(session: Session, user: User, password: str) -> None:
    validate_new_password(password)
    user.password_hash = hash_password(password)
    user.password_changed_at = _now()
    user.failed_login_count, user.locked_until = 0, None
    revoke_user_sessions(session, user.id)
    _event(session, "auth_password_changed", user.id)


def set_active(session: Session, user: User, active: bool) -> None:
    user.is_active = active
    if not active:
        revoke_user_sessions(session, user.id)
    _event(session, "auth_user_enabled" if active else "auth_user_disabled", user.id)


# ------------------------------------------------------------ login / sessions

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token_for(token: str) -> str:
    return hashlib.sha256(b"gtmflow-csrf:" + token.encode()).hexdigest()


class LoginFailed(Exception):
    pass


class LoginThrottled(Exception):
    pass


def _limit_peer(session: Session, client_ip: str | None, scope: str = "login") -> None:
    """Count attempts atomically before hashing, across API processes.

    The peer comes from the ASGI connection, never a header read here.
    Only explicitly trusted reverse proxies may set forwarded addresses.
    PostgreSQL and SQLite are the app's supported databases. Sign-up,
    verification and guest access each count in their own bucket.
    """
    now = _now()
    cutoff = now - timedelta(seconds=LOGIN_WINDOW_SECONDS)
    session.execute(delete(LoginThrottle).where(LoginThrottle.window_started_at < cutoff))
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    table = LoginThrottle.__table__
    peer = client_ip or "unknown"
    key = token_hash(peer if scope == "login" else f"{scope}:{peer}")
    statement = insert(table).values(key=key, window_started_at=now, attempts=1)
    expired = table.c.window_started_at < cutoff
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.key],
        set_={"attempts": case((expired, 1), else_=table.c.attempts + 1),
              "window_started_at": case((expired, now), else_=table.c.window_started_at)},
    ).returning(table.c.attempts)
    attempts = session.execute(statement).scalar_one()
    session.commit()
    if attempts > MAX_PEER_ATTEMPTS:
        raise LoginThrottled()


def login(session: Session, username: str, password: str, *, client_ip: str | None,
          user_agent: str | None, previous_token: str | None = None) -> tuple[User, str, UserSession]:
    global _DUMMY_HASH
    _limit_peer(session, client_ip)
    # Acquire a database write lock before checking/incrementing the counter.
    # SELECT then increment lost failures when parallel requests raced. The
    # no-op UPDATE serializes on both PostgreSQL and SQLite (FOR UPDATE is
    # ignored by SQLite). Account-management writes use the same row lock.
    user = session.scalar(update(User).where(User.username == normalize_username(username))
                          .values(username=User.username).returning(User)
                          .execution_options(populate_existing=True))
    if user is None:
        session.rollback()
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password("dummy-password-for-timing")
        verify_password(password, _DUMMY_HASH)
        raise LoginFailed()
    now = _now()
    locked = _aware(user.locked_until) is not None and _aware(user.locked_until) > now
    ok = verify_password(password, user.password_hash)  # computed even when locked: same timing
    # A sign-up account cannot sign in until its email is verified.
    unverified = user.created_via == CREATED_VIA_SIGNUP and user.email_verified_at is None
    if locked or not user.is_active or not ok or unverified:
        if not locked and user.is_active and not unverified:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_login_count = 0
                _event(session, "auth_account_locked", user.id)
        session.commit()
        raise LoginFailed()
    # Upgrade hashes from the initial Phase 12 cost without breaking old
    # accounts. Only a successfully verified password is ever rehashed.
    parts = user.password_hash.split("$")
    if int(parts[1]) < settings.password_hash_n or (int(parts[1]) == settings.password_hash_n and int(parts[3]) < PASSWORD_HASH_P):
        user.password_hash = hash_password(password)
    user.failed_login_count, user.locked_until, user.last_login_at = 0, None, now
    token, row = start_session(session, user, client_ip=client_ip, user_agent=user_agent,
                               previous_token=previous_token, now=now)
    return user, token, row


def start_session(session: Session, user: User, *, client_ip: str | None, user_agent: str | None,
                  previous_token: str | None = None, hours: int | None = None,
                  now: datetime | None = None) -> tuple[str, UserSession]:
    """Create a new session for an already-authenticated user and commit.

    A previous session in the same browser is revoked (no fixation). Only
    the token's hash is stored; the token itself goes into the cookie."""
    now = now or _now()
    if previous_token:
        session.execute(update(UserSession).where(UserSession.token_hash == token_hash(previous_token),
                                                  UserSession.revoked_at.is_(None))
                        .values(revoked_at=now).execution_options(synchronize_session=False))
    token = secrets.token_urlsafe(32)
    row = UserSession(user_id=user.id, token_hash=token_hash(token), created_at=now, last_seen_at=now,
                      expires_at=now + timedelta(hours=hours or settings.session_absolute_hours),
                      client_ip=(client_ip or "")[:64] or None, user_agent=(user_agent or "")[:200] or None)
    session.add(row)
    actor_token = set_actor(Actor(label=f"user:{user.username}", user_id=str(user.id), role=user.role))
    try:
        _event(session, "auth_login", user.id)
        session.commit()
    finally:
        reset_actor(actor_token)
    return token, row


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
            or (user.created_via == CREATED_VIA_SIGNUP and user.email_verified_at is None)
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
        user_id = session.scalar(update(UserSession).where(UserSession.token_hash == token_hash(token),
                                                  UserSession.revoked_at.is_(None))
                        .values(revoked_at=_now()).returning(UserSession.user_id)
                        .execution_options(synchronize_session=False))
        if user_id is not None:
            _event(session, "auth_logout", user_id)
        session.commit()
