"""Release regressions: browser boundaries, parallel login, audit identity.

All records are synthetic and every integration is mock. PostgreSQL-only
tests run in the disposable release service, never the working database.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.actor import Actor, current_actor, reset_actor, set_actor
from app.models import IntegrationPush, LoginThrottle, User, WorkflowEvent
from app.services import auth
from tests.conftest import TEST_PASSWORD, create_test_user, sign_in
from tests.test_phase12_auth import _all_routes, _concrete


def test_login_rejects_cross_site_and_simple_requests_before_changing_accounts(anon_client, db_session_factory, db_session):
    create_test_user(db_session_factory, "login-user")
    body = {"username": "login-user", "password": TEST_PASSWORD}
    for origin in ("https://evil.example", "null", "http://localhost:3000.evil.example"):
        r = anon_client.post("/api/auth/login", json=body, headers={"Origin": origin})
        assert r.status_code == 403 and "set-cookie" not in r.headers
    # With no Content-Type, FastAPI would otherwise parse this as JSON.
    r = anon_client.post("/api/auth/login", content=json.dumps(body))
    assert r.status_code == 415 and "set-cookie" not in r.headers
    user = db_session.scalar(select(User).where(User.username == "login-user"))
    assert user.failed_login_count == 0 and user.last_login_at is None
    assert anon_client.post("/api/auth/login", json=body, headers={"Origin": "http://localhost:3000"}).status_code == 200


def test_validation_and_private_responses_never_echo_passwords_or_allow_caching(anon_client, client):
    secret = "not-for-responses-" * 20
    r = anon_client.post("/api/auth/login", json={"username": "user", "password": secret})
    assert r.status_code == 422 and secret not in r.text
    assert "input" not in r.text
    assert anon_client.post("/api/auth/login", json={"username": "user", "password": "x" * 17000}).status_code == 413
    for c in (anon_client, client):
        r = c.get("/api/leads")
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-content-type-options"] == "nosniff"
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anon_client.get(path).status_code == 401
        assert client.get(path).status_code == 200


def test_every_write_requires_operator_even_if_a_viewer_has_valid_csrf(app_client, db_session_factory):
    create_test_user(db_session_factory, "reader", "viewer")
    viewer = app_client()
    sign_in(viewer, "reader")
    for method, path in _all_routes():
        if method in ("GET", "HEAD", "OPTIONS") or path.startswith("/api/auth/"):
            continue
        assert viewer.request(method, _concrete(path), json={}).status_code == 403, path
    assert viewer.post("/api/auth/logout").status_code == 204


def test_throttling_covers_unknown_usernames_and_expires(anon_client, db_session_factory, db_session):
    create_test_user(db_session_factory, "valid-user")
    for i in range(auth.MAX_PEER_ATTEMPTS):
        assert anon_client.post("/api/auth/login", json={"username": f"missing-{i}", "password": "wrong"}).status_code == 401
    r = anon_client.post("/api/auth/login", json={"username": "valid-user", "password": TEST_PASSWORD})
    assert r.status_code == 429 and r.headers["retry-after"] == str(auth.LOGIN_WINDOW_SECONDS)
    row = db_session.scalar(select(LoginThrottle))
    assert len(row.key) == 64 and "testclient" not in row.key
    row.window_started_at = datetime.now(timezone.utc) - timedelta(seconds=auth.LOGIN_WINDOW_SECONDS + 1)
    db_session.commit()
    assert anon_client.post("/api/auth/login", json={"username": "valid-user", "password": TEST_PASSWORD}).status_code == 200


def test_parallel_failures_cannot_bypass_account_lockout(db_engine, db_session_factory, monkeypatch):
    if db_engine.dialect.name != "postgresql":
        pytest.skip("requires independent PostgreSQL transactions")
    create_test_user(db_session_factory, "parallel-user")
    barrier = threading.Barrier(8)
    verify = auth.verify_password

    def delayed_verify(*args):
        time.sleep(0.03)  # widen the read/write race, not a polling timeout
        return verify(*args)

    monkeypatch.setattr(auth, "verify_password", delayed_verify)

    def attempt(i):
        barrier.wait(timeout=10)
        with db_session_factory() as s:
            with pytest.raises(auth.LoginFailed):
                auth.login(s, "parallel-user", "wrong-password", client_ip=f"127.0.0.{i+1}", user_agent=None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(attempt, range(8)))
    with db_session_factory() as s:
        user = s.scalar(select(User).where(User.username == "parallel-user"))
        assert user.locked_until > datetime.now(timezone.utc)
        with pytest.raises(auth.LoginFailed):
            auth.login(s, "parallel-user", TEST_PASSWORD, client_ip="127.0.0.10", user_agent=None)


def test_password_bounds_hash_upgrade_and_security_audit(app_client, db_session_factory, db_session):
    with pytest.raises(ValueError, match="at most 256"):
        auth.create_user(db_session, "too-long", "x" * 257, "operator")
    create_test_user(db_session_factory, "old-hash")
    user = db_session.scalar(select(User).where(User.username == "old-hash"))
    assert user.password_hash.split("$")[3] == "3"
    import base64
    import hashlib
    parts = user.password_hash.split("$")
    parts[3] = "1"  # the original Phase 12 cost; verify then upgrade
    parts[5] = base64.b64encode(hashlib.scrypt(TEST_PASSWORD.encode(), salt=base64.b64decode(parts[4]),
                                              n=int(parts[1]), r=8, p=1, dklen=32)).decode()
    user.password_hash = "$".join(parts)
    db_session.commit()
    browser = app_client()
    sign_in(browser, "old-hash")
    assert browser.post("/api/auth/logout").status_code == 204
    db_session.expire_all()
    assert user.password_hash.split("$")[3] == "3"
    events = db_session.scalars(select(WorkflowEvent).where(WorkflowEvent.event_type.in_(["auth_login", "auth_logout"]))).all()
    assert {e.event_type for e in events} == {"auth_login", "auth_logout"}
    assert all(e.event_data["actor"] == "user:old-hash" and e.event_data["actor_user_id"] == str(user.id) for e in events)
    assert TEST_PASSWORD not in json.dumps([e.event_data for e in events])


def test_actor_stamp_is_authoritative_and_scoped(db_session):
    original = current_actor()
    token = set_actor(Actor(label="user:actual", user_id="server-id"))
    try:
        event = WorkflowEvent(event_type="synthetic_audit", event_data={"actor": "forged", "actor_user_id": "forged-id"})
        db_session.add(event)
        db_session.commit()
        assert event.event_data == {"actor": "user:actual", "actor_user_id": "server-id", "action_source": "forged"}
    finally:
        reset_actor(token)
    assert current_actor() == original


def test_delivery_claim_and_resolution_keep_authenticated_actor(client, active_seller_profile, db_session, monkeypatch):
    from app.services import integration_push
    from tests.test_push_endpoints import _lead_by, _scored_setup

    _, leads = _scored_setup(client)
    lead = _lead_by(leads, "Cascade Modular")
    # A local stand-in for a lost reply: no transport is involved.
    monkeypatch.setattr(integration_push, "send_slack_payload", lambda *a: ("unknown", "synthetic timeout"))
    r = client.post(f"/api/leads/{lead['id']}/push", json={"redeliver": True})
    assert r.status_code == 200, r.text
    push = r.json()
    assert push["status"] == "unknown"
    assert client.post(f"/api/pushes/{push['id']}/resolve", json={"resolution": "confirmed_not_delivered"}).status_code == 200
    claim = db_session.scalar(select(IntegrationPush))
    assert claim.claimed_by == "user:test-operator"
    events = db_session.scalars(select(WorkflowEvent).where(WorkflowEvent.event_type.in_(["lead_pushed", "push_outcome_resolved"]))).all()
    assert len(events) == 2
    assert all(e.event_data["actor"] == "user:test-operator" and e.event_data.get("actor_user_id") for e in events)
