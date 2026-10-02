"""Self-service sign-up with an emailed code, and guest access."""
from __future__ import annotations

import smtplib
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api import auth as auth_api
from app.core import config
from app.integrations import email as email_module
from app.models.auth import EmailVerification, User
from app.models.workflow_event import WorkflowEvent
from app.services import auth as auth_service
from app.services import signup as signup_service
from tests.conftest import TEST_PASSWORD

EMAIL = "Sam.Rivera@Example.com"
NORMALIZED = "sam.rivera@example.com"


@pytest.fixture()
def configure(monkeypatch):
    """Apply setting overrides everywhere settings are read."""
    def apply(**changes):
        patched = config.Settings(**{**config.settings.__dict__, **changes})
        for module in (auth_api, auth_service, signup_service, email_module):
            monkeypatch.setattr(module, "settings", patched)
        return patched
    return apply


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(email_module, "send_verification_code", lambda to, code, minutes: sent.append((to, code)))
    return sent


@pytest.fixture()
def enabled(configure):
    return configure(self_signup_enabled=True, guest_access_enabled=True)


def _register(client, email=EMAIL, password=TEST_PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _verify(client, code, email=EMAIL):
    return client.post("/api/auth/verify-email", json={"email": email, "code": code})


# ------------------------------------------------------------ switches

def test_options_and_endpoints_are_off_by_default(app_client, outbox):
    client = app_client()
    assert client.get("/api/auth/options").json() == {
        "self_signup": False, "guest_access": False, "email_delivery": "mock", "guest_can_edit": True}
    assert _register(client).status_code == 403
    assert client.post("/api/auth/guest", json={}).status_code == 403
    assert _verify(client, "123456").status_code == 403
    assert outbox == []


def test_options_report_enabled_features(app_client, enabled):
    assert app_client().get("/api/auth/options").json()["self_signup"] is True
    assert app_client().get("/api/auth/options").json()["guest_access"] is True


# ------------------------------------------------------------ sign-up

def test_sign_up_requires_the_emailed_code_before_signing_in(app_client, enabled, outbox):
    client = app_client()
    response = _register(client)
    assert response.status_code == 202
    assert outbox and outbox[0][0] == NORMALIZED and len(outbox[0][1]) == 6
    code = outbox[0][1]

    blocked = client.post("/api/auth/login", json={"username": NORMALIZED, "password": TEST_PASSWORD})
    assert blocked.status_code == 401  # unverified accounts cannot sign in
    wrong = "000000" if code != "000000" else "111111"
    assert _verify(client, wrong).status_code == 400

    verified = _verify(client, code)
    assert verified.status_code == 200
    assert verified.json()["username"] == NORMALIZED and verified.json()["role"] == "operator"
    client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]
    assert client.get("/api/metrics/dashboard").status_code == 200
    assert _verify(client, code).status_code == 400  # a code works once

    again = app_client()
    signed_in = again.post("/api/auth/login", json={"username": EMAIL, "password": TEST_PASSWORD})
    assert signed_in.status_code == 200


def test_existing_accounts_get_the_same_answer_and_no_email(app_client, enabled, outbox):
    client = app_client()
    _register(client)
    _verify(client, outbox[0][1])
    outbox.clear()
    repeat = _register(app_client(), password="a-different-password")
    assert repeat.status_code == 202
    assert repeat.json() == _register(app_client(), email="new.person@example.com").json()
    assert [to for to, _ in outbox] == ["new.person@example.com"]
    # The verified account's password was not replaced.
    assert app_client().post("/api/auth/login", json={"username": EMAIL, "password": TEST_PASSWORD}).status_code == 200


def test_only_hashes_are_stored(app_client, enabled, outbox, db_session_factory):
    _register(app_client())
    code = outbox[0][1]
    with db_session_factory() as session:
        user = session.scalar(select(User).where(User.username == NORMALIZED))
        row = session.get(EmailVerification, user.id)
        assert code not in row.code_hash and len(row.code_hash) == 64
        assert user.password_hash.startswith("scrypt$") and TEST_PASSWORD not in user.password_hash
        assert user.email_verified_at is None and user.created_via == "signup"


def test_wrong_guesses_are_limited(app_client, enabled, outbox):
    client = app_client()
    _register(client)
    code = outbox[0][1]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(signup_service.MAX_CODE_ATTEMPTS):
        assert _verify(client, wrong).status_code == 400
    assert _verify(client, code).status_code == 400  # locked out of this code


def test_codes_expire(app_client, enabled, outbox, db_session_factory):
    client = app_client()
    _register(client)
    with db_session_factory() as session:
        row = session.scalars(select(EmailVerification)).one()
        row.expires_at = row.expires_at - timedelta(minutes=signup_service.CODE_TTL_MINUTES + 1)
        session.commit()
    assert _verify(client, outbox[0][1]).status_code == 400


def test_resend_waits_for_the_cooldown_and_replaces_the_code(app_client, enabled, outbox, db_session_factory):
    client = app_client()
    _register(client)
    first = outbox[0][1]
    assert client.post("/api/auth/resend-code", json={"email": EMAIL}).status_code == 202
    assert len(outbox) == 1  # inside the cooldown: nothing new is sent
    with db_session_factory() as session:
        row = session.scalars(select(EmailVerification)).one()
        row.last_sent_at = row.last_sent_at - timedelta(seconds=signup_service.RESEND_COOLDOWN_SECONDS + 1)
        session.commit()
    assert client.post("/api/auth/resend-code", json={"email": EMAIL}).status_code == 202
    assert len(outbox) == 2
    second = outbox[1][1]
    if first != second:
        assert _verify(client, first).status_code == 400
    assert _verify(client, second).status_code == 200


def test_unknown_addresses_resend_nothing(app_client, enabled, outbox):
    assert app_client().post("/api/auth/resend-code", json={"email": "nobody@example.com"}).status_code == 202
    assert outbox == []


def test_domain_allowlist_and_input_validation(app_client, configure, outbox):
    configure(self_signup_enabled=True, signup_allowed_email_domains=("example.com",))
    client = app_client()
    assert _register(client, email="someone@elsewhere.org").status_code == 403
    assert _register(client, email="not-an-email").status_code == 422
    assert _register(client, password="short").status_code == 422
    assert _register(client).status_code == 202
    assert [to for to, _ in outbox] == [NORMALIZED]


def test_invalid_role_setting_falls_back_to_read_only(app_client, configure, outbox):
    configure(self_signup_enabled=True, self_signup_role="admin")
    client = app_client()
    _register(client)
    assert _verify(client, outbox[0][1]).json()["role"] == "viewer"


@pytest.mark.parametrize("path, body", [
    ("/api/auth/register", {"email": EMAIL, "password": TEST_PASSWORD}),
    ("/api/auth/verify-email", {"email": EMAIL, "code": "123456"}),
    ("/api/auth/resend-code", {"email": EMAIL}),
    ("/api/auth/guest", {}),
])
def test_public_account_endpoints_require_json_from_an_allowed_origin(app_client, enabled, outbox, path, body):
    client = app_client()
    assert client.post(path, data=body).status_code == 415
    assert client.post(path, json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post(path, content=b"{" + b" " * 17000 + b"}",
                       headers={"Content-Type": "application/json"}).status_code == 413
    assert outbox == []


def test_email_delivery_failure_is_reported_without_details(app_client, enabled, monkeypatch):
    def fail(to, code, minutes):
        raise email_module.EmailDeliveryError("The verification email could not be sent.")
    monkeypatch.setattr(email_module, "send_verification_code", fail)
    response = _register(app_client())
    assert response.status_code == 503 and "could not be sent" in response.json()["detail"]


# ------------------------------------------------------------ guests

def test_each_guest_gets_a_fresh_short_session(app_client, enabled):
    first, second = app_client(), app_client()
    a = first.post("/api/auth/guest", json={})
    b = second.post("/api/auth/guest", json={})
    assert a.status_code == b.status_code == 200
    assert a.json()["role"] == "guest" and a.json()["username"] != b.json()["username"]
    assert "Max-Age=7200" in a.headers["set-cookie"]
    first.headers["X-CSRF-Token"] = a.json()["csrf_token"]
    assert first.get("/api/auth/session").json()["username"] == a.json()["username"]
    assert first.post("/api/demo/run").status_code == 201  # mock-only server: guests can use the app


def test_guests_are_read_only_when_a_real_integration_is_configured(app_client, configure):
    configure(guest_access_enabled=True, slack_webhook_url="https://hooks.example.invalid/x")
    client = app_client()
    info = client.post("/api/auth/guest", json={}).json()
    client.headers["X-CSRF-Token"] = info["csrf_token"]
    assert client.get("/api/metrics/dashboard").status_code == 200
    refused = client.post("/api/demo/run")
    assert refused.status_code == 403 and "mock mode" in refused.json()["detail"]
    assert client.post("/api/auth/logout").status_code == 204


def test_guest_accounts_cannot_sign_in_with_a_password(app_client, enabled):
    username = app_client().post("/api/auth/guest", json={}).json()["username"]
    for password in ("!", "", "anything-at-all-123"):
        attempt = app_client().post("/api/auth/login", json={"username": username, "password": password or "x"})
        assert attempt.status_code == 401


def test_guest_creation_is_throttled_per_client(app_client, enabled):
    client = app_client()
    codes = [client.post("/api/auth/guest", json={}).status_code for _ in range(auth_service.MAX_PEER_ATTEMPTS + 1)]
    assert codes[:-1] == [200] * auth_service.MAX_PEER_ATTEMPTS and codes[-1] == 429
    # Separate bucket: sign-in from the same client is unaffected.
    assert client.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code == 401


def test_new_accounts_are_audited_with_their_actor(app_client, enabled, outbox, db_session_factory):
    guest = app_client().post("/api/auth/guest", json={}).json()
    client = app_client()
    _register(client)
    _verify(client, outbox[0][1])
    with db_session_factory() as session:
        events = {e.event_type: e for e in session.scalars(select(WorkflowEvent))}
    assert {"auth_user_signed_up", "auth_verification_code_sent", "auth_email_verified",
            "auth_guest_created", "auth_login"} <= set(events)
    logins = [e for e in events.values() if e.event_type == "auth_login"]
    assert logins and all(str(e.event_data.get("actor", "")).startswith("user:") for e in logins)
    assert guest["username"].startswith("guest-")


# ------------------------------------------------------------ SMTP sender

class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.calls, self.message = host, port, [], None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, message):
        self.message = message


def test_smtp_delivery_uses_starttls_and_login(configure, monkeypatch, capsys):
    configure(smtp_host="smtp.example.invalid", smtp_username="mailer@example.com", smtp_password="secret-value",
              email_from="GTMFlow <no-reply@example.com>")
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    assert email_module.delivery_mode() == "smtp"
    email_module.send_verification_code("sam@example.com", "123456", 10)
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.example.invalid", 587)
    assert smtp.calls == ["starttls", ("login", "mailer@example.com")]
    assert smtp.message["To"] == "sam@example.com" and "123456" in smtp.message.get_content()
    assert "123456" not in capsys.readouterr().out  # real mode never prints the code


def test_smtp_failure_hides_server_details(configure, monkeypatch):
    configure(smtp_host="smtp.example.invalid", smtp_password="secret-value")

    def refuse(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials for secret-value")
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    with pytest.raises(email_module.EmailDeliveryError) as error:
        email_module.send_verification_code("sam@example.com", "123456", 10)
    assert "secret-value" not in str(error.value) and "123456" not in str(error.value)


def test_mock_delivery_prints_the_code_locally(configure, capsys):
    configure(smtp_host="")
    email_module.send_verification_code("sam@example.com", "654321", 10)
    assert "654321" in capsys.readouterr().out
