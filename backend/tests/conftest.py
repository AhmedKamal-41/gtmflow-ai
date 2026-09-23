import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401 -- register all models on Base.metadata
from app.core.database import Base, get_session
from app.main import app


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


@pytest.fixture()
def client(db_session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def _override() -> Iterator[Session]:
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


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
