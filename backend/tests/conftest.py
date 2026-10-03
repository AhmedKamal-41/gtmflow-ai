import os
from collections.abc import Iterator

# The test suite must never use a developer's backend/.env (a real AI key,
# USE_MOCK_AI=false, a Slack webhook): set safe values BEFORE the app's
# settings load. python-dotenv never overrides variables already set.
os.environ["USE_MOCK_AI"] = "true"
os.environ["OPENAI_API_KEY"] = ""
os.environ["SLACK_WEBHOOK_URL"] = ""
os.environ["AI_PROVIDER"] = "openai"  # Phase 10: never a developer's inference server
os.environ["AGENT_PROVIDER"] = "mock"
os.environ["LORA_INFERENCE_BASE_URL"] = ""
os.environ["LORA_INFERENCE_API_KEY"] = ""
# Raw health clients and file-only dataset CLI tests also construct the
# session dependency. Do not rely on a developer's .env being present.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
# Phase 12: the test client talks plain http, and hashing cost is kept low.
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["PASSWORD_HASH_N"] = "1024"
# Workstation proxy settings must not divert the transport-guard test or
# loopback HTTP checks. No test uses an external proxy or alternate API.
for _proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_proxy, None)
os.environ["OPENAI_BASE_URL"] = "https://api.openai.com/v1"

# Defense in depth: even if a test builds a real client explicitly, no
# socket may resolve or reach a non-loopback host during the test run.
# Local Postgres (TEST_DATABASE_URL) and the in-process TestClient still work.
import ipaddress
import socket


class ExternalNetworkBlocked(RuntimeError):
    pass


BLOCKED_NETWORK_ATTEMPTS: list[str] = []
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _is_local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode()
    if host in ("localhost", "testserver", ""):
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def _block(host) -> None:
    BLOCKED_NETWORK_ATTEMPTS.append(str(host))
    raise ExternalNetworkBlocked(f"Tests may not reach external host {host!r}.")


def _guarded_getaddrinfo(host, *args, **kwargs):
    if not _is_local(host):
        _block(host)
    return _real_getaddrinfo(host, *args, **kwargs)


def _guarded_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(address[0]):
        _block(address[0])
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(address[0]):
        _block(address[0])
    return _real_connect_ex(self, address)


socket.getaddrinfo = _guarded_getaddrinfo
socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401 -- register all models on Base.metadata
from app.core.database import Base, get_session
from app.main import app
from app.core.config import settings as _settings

if not _settings.use_mock_ai or _settings.openai_api_key or _settings.slack_webhook_url:
    raise RuntimeError("Refusing to run tests with real AI or Slack configuration.")


@pytest.fixture()
def db_engine() -> Iterator[Engine]:
    # Opt-in: TEST_DATABASE_URL points the suite at a DISPOSABLE database
    # (e.g. an empty Postgres DB) to exercise dialect-specific behavior --
    # window functions, NULL ordering, savepoints, UUID comparison. Every
    # table is dropped and recreated around each test, so never point this
    # at a database whose contents matter.
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        engine = create_engine(test_url, future=True)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine(
            "sqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        if test_url:
            Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def db_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False, future=True
    )


@pytest.fixture()
def db_session(db_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


TEST_PASSWORD = "correct-horse-battery-staple"


def create_test_user(db_session_factory, username: str, role: str = "operator") -> None:
    from app.services import auth as auth_service

    with db_session_factory() as session:
        auth_service.create_user(session, username, TEST_PASSWORD, role)
        session.commit()


def sign_in(test_client: TestClient, username: str, password: str = TEST_PASSWORD) -> dict:
    """Log in; the session cookie stays in the client and the CSRF token is
    sent on every later request, as the frontend does."""
    response = test_client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    test_client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


@pytest.fixture()
def app_client(db_session_factory: sessionmaker[Session]) -> Iterator:
    """Factory for clients bound to the test database: anonymous unless the
    test signs in (Phase 12)."""
    def _override() -> Iterator[Session]:
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override
    try:
        yield lambda: TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture()
def client(app_client, db_session_factory: sessionmaker[Session]) -> TestClient:
    """A client signed in as an operator (every existing test acts as one)."""
    create_test_user(db_session_factory, "test-operator")
    test_client = app_client()
    sign_in(test_client, "test-operator")
    return test_client


@pytest.fixture()
def anon_client(app_client) -> TestClient:
    return app_client()


SYNTHETIC_SELLER_PROFILE = {
    "profile_kind": "seller",
    "company_name": "Synthetic Seller Co (test fixture)",
    "product_name": "Synthetic Scheduling Service",
    "value_proposition": "A synthetic offer used only by automated tests.",
    "target_customer": "Synthetic target customers for tests.",
    "capabilities": ["Books appointments online", "Sends reminder messages"],
    "proof_points": [
        {"claim": "Used in a synthetic pilot by 3 test clinics", "source": "tests/conftest.py fixture"}
    ],
    "exclusions": [],
}


def save_and_activate(client: TestClient, profile: dict) -> dict:
    """Save a revision and explicitly activate it, as an operator would."""
    status = client.get("/api/seller-profile/status").json()
    saved = client.post(
        "/api/seller-profile",
        json={"expected_version": status["latest_version"] or 0, "profile": profile},
    )
    assert saved.status_code in (200, 201), saved.text
    row = saved.json()
    activated = client.post(
        "/api/seller-profile/activate",
        json={
            "seller_profile_id": row["id"],
            "expected_activation_sequence": status["activation_sequence"],
            "confirm_reviewed": True,
            "acknowledge_demo": profile["profile_kind"] == "demo",
        },
    )
    assert activated.status_code in (200, 201), activated.text
    return row


@pytest.fixture()
def active_seller_profile(client: TestClient) -> dict:
    """A synthetic, explicitly activated seller revision. Tests that generate
    outreach opt in; everything else runs with no active profile."""
    return save_and_activate(client, SYNTHETIC_SELLER_PROFILE)


def review_json(client: TestClient, lead_id: str, output_id: str, reason: str | None = None,
                owner_lead_id: str | None = None) -> dict:
    """A review body naming the exact output AND the content hash the
    reviewer was shown (Phase 6). The hash is read the way the UI reads it:
    from the output as listed for its owning lead. Unknown outputs get a
    placeholder hash so the server's own existence check answers."""
    owner = owner_lead_id or lead_id
    response = client.get(f"/api/leads/{owner}/ai-outputs?limit=200")
    items = response.json().get("items", []) if response.status_code == 200 else []
    content_hash = next((i["content_hash"] for i in items if i["id"] == output_id), "0" * 64)
    body = {"ai_output_id": output_id, "content_hash": content_hash}
    if reason is not None:
        body["reason"] = reason
    return body


def approve_current_draft(client: TestClient, lead_id: str) -> dict:
    """Generate a grounded outreach draft for the lead (needs an active
    seller profile) and approve exactly that draft -- what delivery now
    requires (Phase 6)."""
    draft = client.post(f"/api/leads/{lead_id}/generate-outreach")
    assert draft.status_code == 200, draft.text
    draft = draft.json()
    approved = client.post(
        f"/api/leads/{lead_id}/approve-outreach",
        json={"ai_output_id": draft["id"], "content_hash": draft["content_hash"]},
    )
    assert approved.status_code == 200, approved.text
    return draft
