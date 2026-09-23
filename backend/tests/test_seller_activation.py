"""Phase 5: explicit activation of an immutable seller-profile revision."""
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.models import LeadFitScore, SellerProfileActivation, WorkflowEvent
from app.scoring import fit
from tests.conftest import review_json, SYNTHETIC_SELLER_PROFILE, save_and_activate


def save(client, profile, expected_version):
    response = client.post(
        "/api/seller-profile", json={"expected_version": expected_version, "profile": profile}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def activate(client, profile_id, expected, **extra):
    return client.post("/api/seller-profile/activate", json={
        "seller_profile_id": profile_id,
        "expected_activation_sequence": expected,
        "confirm_reviewed": True,
        **extra,
    })


def variant(**changes):
    profile = deepcopy(SYNTHETIC_SELLER_PROFILE)
    profile.update(changes)
    return profile


def events(db_session, event_type):
    return db_session.scalars(
        select(WorkflowEvent).where(WorkflowEvent.event_type == event_type)
    ).all()


def test_status_moves_from_missing_to_draft_only_to_active(client):
    assert client.get("/api/seller-profile/status").json()["state"] == "missing"
    v1 = save(client, SYNTHETIC_SELLER_PROFILE, 0)
    status = client.get("/api/seller-profile/status").json()
    assert status["state"] == "draft_only"
    assert status["active_profile"] is None
    assert v1["status"] == "draft"

    assert activate(client, v1["id"], 0).status_code == 201
    status = client.get("/api/seller-profile/status").json()
    assert status["state"] == "active"
    assert status["active_profile"]["id"] == v1["id"]
    assert status["active_profile"]["status"] == "active"
    assert status["latest_is_active"] is True
    assert status["last_activation"]["content_hash"] == v1["content_hash"]


def test_saving_a_new_draft_never_changes_the_active_revision(client):
    v1 = save(client, SYNTHETIC_SELLER_PROFILE, 0)
    activate(client, v1["id"], 0)
    v2 = save(client, variant(product_name="Edited, not yet reviewed"), 1)

    status = client.get("/api/seller-profile/status").json()
    assert status["active_profile"]["id"] == v1["id"]
    assert status["latest_version"] == 2
    assert status["latest_is_active"] is False
    versions = client.get("/api/seller-profile/versions").json()["items"]
    assert [(row["version"], row["status"]) for row in versions] == [(2, "draft"), (1, "active")]
    # The editor's "latest" read reports the new revision as a draft.
    assert client.get("/api/seller-profile").json()["id"] == v2["id"]
    assert client.get("/api/seller-profile").json()["status"] == "draft"


def test_activation_requires_explicit_review_confirmation(client, db_session):
    v1 = save(client, SYNTHETIC_SELLER_PROFILE, 0)
    for body in (
        {"seller_profile_id": v1["id"], "expected_activation_sequence": 0},
        {"seller_profile_id": v1["id"], "expected_activation_sequence": 0, "confirm_reviewed": False},
    ):
        assert client.post("/api/seller-profile/activate", json=body).status_code == 422
    assert db_session.scalar(select(func.count()).select_from(SellerProfileActivation)) == 0
    assert client.get("/api/seller-profile/status").json()["state"] == "draft_only"


def test_demo_profile_needs_explicit_acknowledgement_and_never_makes_email_ready(client, db_session):
    demo = client.get("/api/seller-profile/demonstration-template").json()
    assert demo["profile_kind"] == "demo"
    assert demo["proof_points"] == []
    # Fetching the template saved and activated nothing.
    assert client.get("/api/seller-profile/status").json()["state"] == "missing"

    v1 = save(client, demo, 0)
    refused = activate(client, v1["id"], 0)
    assert refused.status_code == 422
    assert "demonstration" in refused.json()["detail"]
    assert client.get("/api/seller-profile/status").json()["state"] == "draft_only"

    accepted = activate(client, v1["id"], 0, acknowledge_demo=True)
    assert accepted.status_code == 201
    assert accepted.json()["demo_acknowledged"] is True

    upload = client.post("/api/batches/upload", files={
        "file": ("f.csv", b"company_name,contact_email\nDemo Target,a@demo-target.test\n", "text/csv")
    }).json()
    lead = client.get(f"/api/leads?batch_id={upload['batch_id']}").json()["items"][0]
    draft = client.post(f"/api/leads/{lead['id']}/generate-outreach").json()
    client.post(f"/api/leads/{lead['id']}/approve-outreach", json=review_json(client, lead['id'], draft["id"]))
    email = client.get(f"/api/leads/{lead['id']}/readiness").json()["readiness"]["outbound_email"]
    assert email["status"] == "not_ready"
    assert email["gaps"] == ["seller_profile_is_demonstration"]


def test_stale_activation_is_rejected_and_identical_retry_is_idempotent(client, db_session):
    v1 = save(client, SYNTHETIC_SELLER_PROFILE, 0)
    v2 = save(client, variant(product_name="Second"), 1)
    first = activate(client, v1["id"], 0)
    assert first.status_code == 201

    retry = activate(client, v1["id"], 0)
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]

    # A second tab that still believes nothing is active cannot switch.
    stale = activate(client, v2["id"], 0)
    assert stale.status_code == 409
    assert client.get("/api/seller-profile/status").json()["active_profile"]["id"] == v1["id"]
    assert len(events(db_session, "seller_profile_activated")) == 1

    assert activate(client, v2["id"], 1).status_code == 201
    assert client.get("/api/seller-profile/status").json()["active_profile"]["id"] == v2["id"]


def test_activation_and_its_audit_event_match(client, db_session):
    v1 = save(client, SYNTHETIC_SELLER_PROFILE, 0)
    row = activate(client, v1["id"], 0).json()
    [event] = events(db_session, "seller_profile_activated")
    assert event.event_data["activation_id"] == row["id"]
    assert event.event_data["seller_profile_id"] == v1["id"]
    assert event.event_data["seller_profile_version"] == 1
    assert event.event_data["content_hash"] == v1["content_hash"]
    assert event.event_data["reviewed_confirmation"] is True
    assert event.event_data["actor_label"] == "local-demo-unauthenticated"


def test_concurrent_activation_race_is_reported_as_conflict(client, db_session_factory, monkeypatch):
    from app.schemas.seller_profile import SellerProfileActivate
    from app.services import seller_profiles

    v1 = save(client, SYNTHETIC_SELLER_PROFILE, 0)
    v2 = save(client, variant(product_name="Competing"), 1)
    real_current = seller_profiles.current_activation
    raced = False

    def competitor_wins_after_lookup(session):
        nonlocal raced
        seen = real_current(session)
        if not raced:
            raced = True
            with db_session_factory() as other:
                seller_profiles.activate_seller_profile(other, SellerProfileActivate(
                    seller_profile_id=v2["id"], expected_activation_sequence=0, confirm_reviewed=True,
                ))
        return seen

    monkeypatch.setattr(seller_profiles, "current_activation", competitor_wins_after_lookup)
    result = activate(client, v1["id"], 0)
    assert result.status_code == 409
    monkeypatch.undo()
    status = client.get("/api/seller-profile/status").json()
    assert status["active_profile"]["id"] == v2["id"]
    assert status["activation_sequence"] == 1


def test_concurrent_identical_activation_returns_the_winner(client, db_session_factory, monkeypatch):
    from app.schemas.seller_profile import SellerProfileActivate
    from app.services import seller_profiles

    v1 = save(client, SYNTHETIC_SELLER_PROFILE, 0)
    real_current = seller_profiles.current_activation
    raced = False

    def same_request_wins_after_lookup(session):
        nonlocal raced
        seen = real_current(session)
        if not raced:
            raced = True
            with db_session_factory() as other:
                seller_profiles.activate_seller_profile(other, SellerProfileActivate(
                    seller_profile_id=v1["id"], expected_activation_sequence=0, confirm_reviewed=True,
                ))
        return seen

    monkeypatch.setattr(seller_profiles, "current_activation", same_request_wins_after_lookup)
    result = activate(client, v1["id"], 0)
    assert result.status_code == 200
    assert result.json()["sequence"] == 1


def test_deactivation_returns_to_draft_only_and_blocks_outreach(client, db_session):
    v1 = save_and_activate(client, SYNTHETIC_SELLER_PROFILE)
    assert client.post("/api/seller-profile/deactivate", json={"expected_activation_sequence": 0}).status_code == 422
    # A tab that saw a different activation state cannot deactivate.
    assert client.post("/api/seller-profile/deactivate", json={"expected_activation_sequence": 2}).status_code == 409
    response = client.post("/api/seller-profile/deactivate", json={"expected_activation_sequence": 1})
    assert response.status_code == 201
    assert response.json()["action"] == "deactivate"
    status = client.get("/api/seller-profile/status").json()
    assert status["state"] == "draft_only"
    # History is preserved: the revision still exists, just not active.
    assert client.get("/api/seller-profile/versions").json()["items"][0]["id"] == v1["id"]
    assert len(events(db_session, "seller_profile_deactivated")) == 1

    upload = client.post("/api/batches/upload", files={
        "file": ("f.csv", b"company_name\nAfter Deactivation Co\n", "text/csv")
    }).json()
    lead = client.get(f"/api/leads?batch_id={upload['batch_id']}").json()["items"][0]
    assert client.post(f"/api/leads/{lead['id']}/generate-outreach").status_code == 409


def test_unknown_revision_cannot_be_activated(client):
    missing = activate(client, "00000000-0000-0000-0000-000000000000", 0)
    assert missing.status_code == 404


def test_activation_does_not_change_the_fit_rubric_or_stored_scores(client, db_session):
    upload = client.post("/api/batches/upload", files={
        "file": ("f.csv", b"company_name,industry,location\nFit Co,medical practice,united states\n", "text/csv")
    }).json()
    lead = client.get(f"/api/leads?batch_id={upload['batch_id']}").json()["items"][0]
    scored = client.post(f"/api/leads/{lead['id']}/fit-score").json()
    rubric = (fit.SCORER_VERSION, fit.PROFILE_ID, fit.PROFILE_VERSION, fit.NORMALIZATION_VERSION)

    def stored_rows():
        db_session.expire_all()
        return [
            (row.id, row.fit_score, row.band, row.criteria, row.readiness, row.input_fingerprint)
            for row in db_session.scalars(select(LeadFitScore))
        ]

    before = stored_rows()
    save_and_activate(client, SYNTHETIC_SELLER_PROFILE)
    assert stored_rows() == before
    assert (fit.SCORER_VERSION, fit.PROFILE_ID, fit.PROFILE_VERSION, fit.NORMALIZATION_VERSION) == rubric

    after = client.get(f"/api/leads/{lead['id']}/fit-score").json()
    assert (after["fit_score"], after["band"], after["criteria"]) == (
        scored["fit_score"], scored["band"], scored["criteria"],
    )
    # Current readiness reflects the activation; the stored snapshot does not.
    assert "no_seller_profile_configured" not in after["readiness"]["outbound_email"]["gaps"]
    assert "no_seller_profile_configured" in after["at_scoring"]["readiness"]["outbound_email"]["gaps"]


@pytest.mark.parametrize("body", [
    {"expected_activation_sequence": 0, "confirm_reviewed": True},
    {"seller_profile_id": "not-a-uuid", "expected_activation_sequence": 0, "confirm_reviewed": True},
    {"seller_profile_id": "00000000-0000-0000-0000-000000000000", "expected_activation_sequence": -1, "confirm_reviewed": True},
    {"seller_profile_id": "00000000-0000-0000-0000-000000000000", "expected_activation_sequence": 0, "confirm_reviewed": True, "extra": 1},
])
def test_malformed_activation_requests_are_rejected(client, body):
    assert client.post("/api/seller-profile/activate", json=body).status_code == 422
