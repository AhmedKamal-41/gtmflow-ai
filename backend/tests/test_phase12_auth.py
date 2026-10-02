"""Phase 12: backend-enforced access control, session handling, login
protections and actor recording."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from app.api.auth import COOKIE_NAME, PUBLIC_PATHS
from app.core import config
from app.main import app
from app.models import AIOutputReview, SellerProfileActivation, WorkflowEvent
from app.models.auth import User, UserSession
from app.models.background_job import BackgroundJob
from app.services import auth as auth_service
from tests.conftest import (
    SYNTHETIC_SELLER_PROFILE,
    TEST_PASSWORD,
    approve_current_draft,
    create_test_user,
    save_and_activate,
    sign_in,
)

ZERO = "00000000-0000-0000-0000-000000000000"


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", ZERO, path)


def _all_routes():
    """Every path and method the API exposes (from the app's own OpenAPI
    schema, so routers included in any way are covered)."""
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.upper() not in ("HEAD", "OPTIONS"):
                yield method.upper(), path


# ------------------------------------------------------------ deny by default

def test_every_non_public_route_refuses_anonymous_requests(anon_client):
    checked = 0
    for method, path in _all_routes():
        response = anon_client.request(method, _concrete(path), json={})
        if path in PUBLIC_PATHS:
            assert response.status_code != 401, path
            continue
        assert response.status_code == 401, (method, path, response.status_code)
        checked += 1
    assert checked >= 50  # the whole API surface, not a sample


def test_health_is_public_and_says_nothing_sensitive(anon_client):
    assert anon_client.get("/api/health").json()["status"] == "ok"
    assert anon_client.get("/health").status_code == 200


@pytest.mark.parametrize("method, path, body", [
    ("POST", f"/api/leads/{ZERO}/approve-outreach", {"ai_output_id": ZERO, "content_hash": "0" * 64}),
    ("POST", f"/api/leads/{ZERO}/reject-outreach", {"ai_output_id": ZERO, "content_hash": "0" * 64, "reason": "x"}),
    ("POST", f"/api/batches/{ZERO}/jobs", {"job_type": "push_hot"}),
    ("POST", f"/api/jobs/{ZERO}/cancel", {}),
    ("POST", f"/api/leads/{ZERO}/push", {"integration_type": "slack", "redeliver": True}),
    ("POST", f"/api/pushes/{ZERO}/resolve", {"resolution": "confirmed_delivered"}),
    ("POST", "/api/seller-profile/activate", {}),
    ("POST", "/api/demo/run", {}),
    ("GET", "/api/leads", None),
    ("GET", "/api/metrics/dashboard", None),
])
def test_sensitive_actions_need_a_session_and_an_operator(anon_client, app_client, db_session_factory, method, path, body):
    assert anon_client.request(method, path, json=body).status_code == 401
    create_test_user(db_session_factory, "vera", role="viewer")
    viewer = app_client()
    sign_in(viewer, "vera")
    code = viewer.request(method, path, json=body).status_code
    assert code == (403 if method == "POST" else 200), (path, code)


# ------------------------------------------------------------ login protections

def test_login_failures_are_generic_and_lock_the_account(anon_client, db_session_factory, db_session):
    create_test_user(db_session_factory, "olga")
    unknown = anon_client.post("/api/auth/login", json={"username": "nobody", "password": "whatever-password"})
    wrong = anon_client.post("/api/auth/login", json={"username": "olga", "password": "wrong-password-123"})
    assert unknown.status_code == wrong.status_code == 401 and unknown.json() == wrong.json()
    for _ in range(4):
        anon_client.post("/api/auth/login", json={"username": "olga", "password": "wrong-password-123"})
    locked = anon_client.post("/api/auth/login", json={"username": "olga", "password": TEST_PASSWORD})
    assert locked.status_code == 401 and locked.json() == wrong.json()  # locked; says nothing more
    user = db_session.scalar(select(User).where(User.username == "olga"))
    user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert anon_client.post("/api/auth/login", json={"username": "OLGA ", "password": TEST_PASSWORD}).status_code == 200


def test_session_cookie_flags_and_hash_only_storage(anon_client, db_session_factory, db_session, monkeypatch):
    from app.api import auth as auth_api

    create_test_user(db_session_factory, "sam")
    monkeypatch.setattr(auth_api, "settings", config.Settings(**{**config.settings.__dict__, "session_cookie_secure": True}))
    response = anon_client.post("/api/auth/login", json={"username": "sam", "password": TEST_PASSWORD})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie and "Path=/" in cookie
    assert response.headers["cache-control"] == "no-store"
    token = cookie.split(f"{COOKIE_NAME}=", 1)[1].split(";", 1)[0]
    stored = db_session.scalars(select(UserSession.token_hash)).all()
    assert token not in stored and auth_service.token_hash(token) in stored
    assert TEST_PASSWORD not in db_session.scalar(select(User.password_hash).where(User.username == "sam"))


# ------------------------------------------------------------ session handling

def test_csrf_is_required_for_state_changes(client):
    token = client.headers.pop("X-CSRF-Token")
    assert client.get("/api/leads").status_code == 200  # reads need no token
    assert client.post("/api/demo/run").status_code == 403
    client.headers["X-CSRF-Token"] = "0" * 64
    assert client.post("/api/demo/run").status_code == 403
    client.headers["X-CSRF-Token"] = token
    assert client.post("/api/demo/run").status_code == 201


def test_logout_revokes_and_login_rotates_the_session(app_client, db_session_factory):
    create_test_user(db_session_factory, "lena")
    first = app_client()
    sign_in(first, "lena")
    old_cookie = first.cookies.get(COOKIE_NAME)
    sign_in(first, "lena")  # logging in again rotates: the old token is dead
    live_cookie = first.cookies.get(COOKIE_NAME)
    stale = app_client()
    stale.cookies.set(COOKIE_NAME, old_cookie)
    assert stale.get("/api/auth/session").status_code == 401
    assert first.get("/api/auth/session").json()["username"] == "lena"
    assert first.post("/api/auth/logout").status_code == 204
    replay = app_client()
    replay.cookies.set(COOKIE_NAME, live_cookie)
    assert first.get("/api/auth/session").status_code == 401 and replay.get("/api/leads").status_code == 401


def test_idle_and_absolute_expiry_disabled_users_and_password_changes(app_client, db_session_factory, db_session):
    create_test_user(db_session_factory, "ivan")

    def fresh():
        c = app_client()
        sign_in(c, "ivan")
        return c

    def latest_session():
        db_session.expire_all()
        return db_session.scalars(select(UserSession).order_by(UserSession.created_at.desc())).first()

    idle = fresh()
    row = latest_session()
    row.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=config.settings.session_idle_minutes + 1)
    db_session.commit()
    assert idle.get("/api/leads").status_code == 401
    expired = fresh()
    row = latest_session()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert expired.get("/api/leads").status_code == 401
    disabled = fresh()
    user = db_session.scalar(select(User).where(User.username == "ivan"))
    auth_service.set_active(db_session, user, False)
    db_session.commit()
    assert disabled.get("/api/leads").status_code == 401
    assert app_client().post("/api/auth/login", json={"username": "ivan", "password": TEST_PASSWORD}).status_code == 401
    auth_service.set_active(db_session, user, True)
    db_session.commit()
    before = fresh()
    auth_service.set_password(db_session, user, "a-brand-new-password-42")
    db_session.commit()
    assert before.get("/api/leads").status_code == 401


def test_cors_allows_only_configured_origins_and_the_needed_headers(anon_client):
    ok = anon_client.options("/api/leads", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
                                                    "Access-Control-Request-Headers": "X-CSRF-Token"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert ok.headers.get("access-control-allow-credentials") == "true"
    evil = anon_client.options("/api/leads", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in evil.headers


# ------------------------------------------------------------ actors

def test_authenticated_actors_are_recorded_server_side(client, db_session, db_session_factory):
    from app.jobs import runner
    from app.jobs.queue import claim_next

    save_and_activate(client, SYNTHETIC_SELLER_PROFILE)
    batch = client.post("/api/batches/upload", files={"file": ("l.csv",
        b"company_name,industry,contact_title,contact_name,contact_email,website,company_size,location,notes\n"
        b"Cascade Modular,Housing,VP Operations,Sarah Chen,sarah@cascade.com,cascade.com,240,USA,scheduling\n", "text/csv")}).json()
    lead = client.get(f"/api/leads?batch_id={batch['batch_id']}").json()["items"][0]
    approve_current_draft(client, lead["id"])
    review = db_session.scalar(select(AIOutputReview))
    assert review.reviewer_label == "user:test-operator"
    activation = db_session.scalar(select(SellerProfileActivation))
    assert activation.actor_label == "user:test-operator"
    approved = db_session.scalar(select(WorkflowEvent).where(WorkflowEvent.event_type == "outreach_approved"))
    operator = db_session.scalar(select(User).where(User.username == "test-operator"))
    assert approved.event_data["actor"] == "user:test-operator" and approved.event_data["actor_user_id"] == str(operator.id)

    job = client.post(f"/api/batches/{batch['batch_id']}/jobs", json={"job_type": "generate_summary"}).json()
    assert db_session.get(BackgroundJob, UUID(job["id"])).created_by == "user:test-operator"
    with db_session_factory() as s:
        claim_next(s, "w1", 60, job_id=UUID(job["id"]))
    assert runner.run_job(db_session_factory, UUID(job["id"]), "w1") == "completed"
    db_session.expire_all()
    done = db_session.scalar(select(WorkflowEvent).where(WorkflowEvent.event_type == "job_completed"))
    generated = db_session.scalar(select(WorkflowEvent).where(WorkflowEvent.event_type == "ai_summary_generated"))
    assert done.event_data["actor"] == generated.event_data["actor"] == "job:user:test-operator"
    assert done.event_data["actor_user_id"] == generated.event_data["actor_user_id"] == str(operator.id)


def test_the_cli_creates_accounts_without_echoing_passwords(db_engine, monkeypatch, capsys):
    from app import auth_cli
    from app.core import database

    monkeypatch.setattr(database, "get_sessionmaker", lambda: __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(bind=db_engine))
    monkeypatch.setattr(auth_cli, "get_sessionmaker", database.get_sessionmaker)
    monkeypatch.setenv("GTMFLOW_NEW_PASSWORD", "short")
    with pytest.raises(SystemExit, match="at least 12"):
        auth_cli.main(["create-user", "cli-user"])
    monkeypatch.setenv("GTMFLOW_NEW_PASSWORD", "a-long-enough-password")
    assert auth_cli.main(["create-user", "cli-user", "--role", "viewer"]) == 0
    with pytest.raises(SystemExit, match="already exists"):
        auth_cli.main(["create-user", "CLI-USER"])
    assert auth_cli.main(["disable", "cli-user"]) == 0
    assert "a-long-enough-password" not in capsys.readouterr().out


def test_worker_and_cli_processes_stamp_actors_without_importing_the_api():
    """Regression (found in the Phase 12 live release run): the actor hook
    was installed only by app.main, so events written by the separate
    worker process carried no actor. A fresh process importing only the
    worker must have it."""
    import subprocess
    import sys

    code = ("import app.jobs.worker, app.auth_cli; from sqlalchemy import event; from app.core.actor import _stamp; "
            "from app.models import WorkflowEvent; print(event.contains(WorkflowEvent, 'before_insert', _stamp))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__("os").environ, "DATABASE_URL": "sqlite://"}, cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    assert out.stdout.strip() == "True", out.stderr[-500:]
