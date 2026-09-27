"""Phase 12: login, logout, session, and the app-wide access check.

`authenticate` is installed as an application-level dependency (app/main.py),
so every route -- including any added later -- requires a live session
unless its path is listed in PUBLIC_PATHS. State-changing methods also need
the session's CSRF token (header X-CSRF-Token) and the `operator` role;
`viewer` accounts are read-only. The authenticated user becomes the actor
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
from app.core.database import get_session
from app.models.auth import ROLE_OPERATOR
from app.services import auth as auth_service

COOKIE_NAME = "gtmflow_session"
CSRF_HEADER = "X-CSRF-Token"
PUBLIC_PATHS = frozenset({"/health", "/api/health", "/api/auth/login"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=256)


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
        if resolved.role != ROLE_OPERATOR and request.url.path != "/api/auth/logout":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="This account is read-only (viewer role).")
    request.state.auth = resolved
    # Set here, in the request's own context: the endpoint (run in a thread
    # pool) inherits a copy of it.
    context = set_actor(Actor(label=f"user:{resolved.username}", user_id=resolved.user_id, role=resolved.role))
    try:
        yield
    finally:
        reset_actor(context)


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=settings.session_absolute_hours * 3600, httponly=True,
                        secure=settings.session_cookie_secure, samesite="lax", path="/")


def _info(resolved: auth_service.ResolvedSession) -> SessionInfo:
    return SessionInfo(username=resolved.username, role=resolved.role,
                       expires_at=resolved.expires_at.isoformat(), csrf_token=resolved.csrf_token)


@router.post("/login", response_model=SessionInfo)
def login(body: LoginRequest, request: Request, response: Response,
          session: Session = Depends(get_session)) -> SessionInfo:
    # Login has no existing session token. Requiring JSON prevents simple
    # cross-site form/fetch requests; an explicit Origin check also rejects
    # hostile browser origins before any account state is changed.
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(status_code=415, detail="Sign-in requires application/json.")
    origin = request.headers.get("origin")
    own_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin is not None and origin not in (*settings.allowed_origins, own_origin):
        raise HTTPException(status_code=403, detail="Sign-in origin is not allowed.")
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
