"""Phase 12: login, logout, session, and the app-wide access check.

`authenticate` is installed as an application-level dependency (app/main.py),
so every route -- including any added later -- requires a live session
unless its path is listed in PUBLIC_PATHS. State-changing methods also need
the session's CSRF token (header X-CSRF-Token) and the `operator` role;
`viewer` accounts are read-only, and `guest` accounts may change data only
while the server is guest-safe (no Slack webhook, no paid per-request AI API).
Sign-up, email verification and guest access are public but share login's
protections: JSON only, an allowed Origin, a bounded body, peer throttling. The authenticated user becomes the actor
recorded in labels and on every WorkflowEvent (app/core/actor.py).
"""
from __future__ import annotations

import hmac
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.actor import Actor, reset_actor, set_actor
from app.core.config import settings
from app.core.http_security import ACCOUNT_POST_PATHS  # public; JSON bodies bounded there
from app.core.database import get_session
from app.integrations import email as email_sender
from app.models.auth import ROLE_GUEST, ROLE_OPERATOR
from app.services import auth as auth_service
from app.services import signup as signup_service

COOKIE_NAME = "gtmflow_session"
CSRF_HEADER = "X-CSRF-Token"
PUBLIC_PATHS = frozenset({"/health", "/api/health", "/api/auth/options", *ACCOUNT_POST_PATHS})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class ResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)


class GuestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthOptions(BaseModel):
    self_signup: bool
    guest_access: bool
    email_delivery: str
    guest_can_edit: bool


class CodeSent(BaseModel):
    message: str


class SessionInfo(BaseModel):
    username: str
    role: str
    expires_at: str
    csrf_token: str


async def authenticate(request: Request, session: Session = Depends(get_session)) -> AsyncIterator[None]:
    if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
        yield
        return
    token = request.cookies.get(COOKIE_NAME)
    resolved = await run_in_threadpool(auth_service.resolve, session, token)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required.")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), resolved.csrf_token.encode()):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing or invalid CSRF token.")
        if request.url.path != "/api/auth/logout" and not _may_write(resolved.role):
            detail = ("Guest accounts are read-only on this server (it can send real messages or call a paid AI API)."
                      if resolved.role == ROLE_GUEST else "This account is read-only (viewer role).")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    request.state.auth = resolved
    # Set here, in the request's own context: the endpoint (run in a thread
    # pool) inherits a copy of it.
    context = set_actor(Actor(label=f"user:{resolved.username}", user_id=resolved.user_id, role=resolved.role))
    try:
        yield
    finally:
        reset_actor(context)


def _may_write(role: str) -> bool:
    return role == ROLE_OPERATOR or (role == ROLE_GUEST and settings.guest_safe)


def _set_cookie(response: Response, token: str, hours: int | None = None) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=(hours or settings.session_absolute_hours) * 3600, httponly=True,
                        secure=settings.session_cookie_secure, samesite="lax", path="/")


def _require_json_from_allowed_origin(request: Request) -> None:
    # These endpoints have no existing session token. Requiring JSON prevents
    # simple cross-site form/fetch requests; an explicit Origin check also
    # rejects hostile browser origins before any account state is changed.
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(status_code=415, detail="Sign-in requires application/json.")
    origin = request.headers.get("origin")
    own_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin is not None and origin not in (*settings.allowed_origins, own_origin):
        raise HTTPException(status_code=403, detail="Sign-in origin is not allowed.")


def _client(request: Request) -> str | None:
    return request.client.host if request.client else None


def _throttled() -> HTTPException:
    return HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                         headers={"Retry-After": str(auth_service.LOGIN_WINDOW_SECONDS)})


def _started(response: Response, user, token: str, row) -> SessionInfo:
    hours = settings.guest_session_hours if user.role == ROLE_GUEST else None
    _set_cookie(response, token, hours)
    response.headers["Cache-Control"] = "no-store"
    return SessionInfo(username=user.username, role=user.role, expires_at=row.expires_at.isoformat(),
                       csrf_token=auth_service.csrf_token_for(token))


def _send_code(pending: tuple[str, str] | None) -> CodeSent:
    if pending is not None:
        address, code = pending
        try:
            email_sender.send_verification_code(address, code, signup_service.CODE_TTL_MINUTES)
        except email_sender.EmailDeliveryError:
            raise HTTPException(status_code=503, detail="The verification email could not be sent. Try again later.") from None
    # The same answer whether or not the address already has an account.
    return CodeSent(message="If this address can be registered, a 6-digit code is on its way. Enter it to finish.")


def _info(resolved: auth_service.ResolvedSession) -> SessionInfo:
    return SessionInfo(username=resolved.username, role=resolved.role,
                       expires_at=resolved.expires_at.isoformat(), csrf_token=resolved.csrf_token)


@router.post("/login", response_model=SessionInfo)
def login(body: LoginRequest, request: Request, response: Response,
          session: Session = Depends(get_session)) -> SessionInfo:
    _require_json_from_allowed_origin(request)
    try:
        user, token, row = auth_service.login(
            session, body.username, body.password,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            previous_token=request.cookies.get(COOKIE_NAME),
        )
    except auth_service.LoginThrottled:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.",
                            headers={"Retry-After": str(auth_service.LOGIN_WINDOW_SECONDS)}) from None
    except auth_service.LoginFailed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=auth_service.GENERIC_LOGIN_ERROR) from None
    _set_cookie(response, token)
    response.headers["Cache-Control"] = "no-store"
    return SessionInfo(username=user.username, role=user.role, expires_at=row.expires_at.isoformat(),
                       csrf_token=auth_service.csrf_token_for(token))


@router.get("/session", response_model=SessionInfo)
def current_session(request: Request, response: Response) -> SessionInfo:
    response.headers["Cache-Control"] = "no-store"
    return _info(request.state.auth)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, session: Session = Depends(get_session)) -> Response:
    auth_service.logout(session, request.cookies.get(COOKIE_NAME))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(COOKIE_NAME, path="/", secure=settings.session_cookie_secure, httponly=True, samesite="lax")
    return response


@router.get("/options", response_model=AuthOptions)
def auth_options() -> AuthOptions:
    """Which sign-in paths this server offers (public; no account data)."""
    return AuthOptions(self_signup=settings.self_signup_enabled, guest_access=settings.guest_access_enabled,
                       email_delivery=email_sender.delivery_mode(), guest_can_edit=settings.guest_safe)


@router.post("/register", response_model=CodeSent, status_code=status.HTTP_202_ACCEPTED)
def register(body: RegisterRequest, request: Request, session: Session = Depends(get_session)) -> CodeSent:
    _require_json_from_allowed_origin(request)
    try:
        pending = signup_service.register(session, body.email, body.password, client_ip=_client(request))
    except signup_service.FeatureDisabled:
        raise HTTPException(status_code=403, detail="Self-service sign-up is not enabled on this server.") from None
    except signup_service.DomainNotAllowed:
        raise HTTPException(status_code=403, detail="Sign-up is limited to approved email domains.") from None
    except auth_service.LoginThrottled:
        raise _throttled() from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return _send_code(pending)


@router.post("/resend-code", response_model=CodeSent, status_code=status.HTTP_202_ACCEPTED)
def resend_code(body: ResendRequest, request: Request, session: Session = Depends(get_session)) -> CodeSent:
    _require_json_from_allowed_origin(request)
    try:
        pending = signup_service.resend_code(session, body.email, client_ip=_client(request))
    except signup_service.FeatureDisabled:
        raise HTTPException(status_code=403, detail="Self-service sign-up is not enabled on this server.") from None
    except auth_service.LoginThrottled:
        raise _throttled() from None
    return _send_code(pending)


@router.post("/verify-email", response_model=SessionInfo)
def verify_email(body: VerifyRequest, request: Request, response: Response,
                 session: Session = Depends(get_session)) -> SessionInfo:
    _require_json_from_allowed_origin(request)
    try:
        user, token, row = signup_service.verify(session, body.email, body.code, client_ip=_client(request),
                                                 user_agent=request.headers.get("user-agent"),
                                                 previous_token=request.cookies.get(COOKIE_NAME))
    except signup_service.FeatureDisabled:
        raise HTTPException(status_code=403, detail="Self-service sign-up is not enabled on this server.") from None
    except auth_service.LoginThrottled:
        raise _throttled() from None
    except signup_service.VerificationFailed:
        raise HTTPException(status_code=400, detail="That code is wrong or has expired. Request a new one.") from None
    return _started(response, user, token, row)


@router.post("/guest", response_model=SessionInfo)
def guest(body: GuestRequest, request: Request, response: Response,
          session: Session = Depends(get_session)) -> SessionInfo:
    _require_json_from_allowed_origin(request)
    try:
        user, token, row = signup_service.create_guest(session, client_ip=_client(request),
                                                       user_agent=request.headers.get("user-agent"),
                                                       previous_token=request.cookies.get(COOKIE_NAME))
    except signup_service.FeatureDisabled:
        raise HTTPException(status_code=403, detail="Guest access is not enabled on this server.") from None
    except auth_service.LoginThrottled:
        raise _throttled() from None
    return _started(response, user, token, row)
