from copy import deepcopy

import pytest
from sqlalchemy import event, func, select

from app.models import AIOutput, AIOutputReview, IntegrationPush, Lead, SellerProfile, WorkflowEvent


def payload(version=0):
    return {
        "expected_version": version,
        "profile": {
            "profile_kind": "demo",
            "company_name": "Fixture Seller",
            "product_name": "Fixture scheduling tool",
            "value_proposition": "Organizes appointment requests in a shared queue.",
            "target_customer": "US medical practices; no size preference; office managers.",
            "capabilities": ["Shared request queue"],
            "proof_points": [{"claim": "Supports request assignment", "source": "Synthetic feature specification"}],
            "exclusions": ["Hospitals"],
        },
    }


def test_no_profile_is_an_explicit_empty_state(client):
    assert client.get("/api/seller-profile").status_code == 404
    result = client.get("/api/seller-profile/versions").json()
    assert result["items"] == []
    assert result["total"] == 0


def test_revisions_preserve_content_and_emit_matching_audit_events(client, db_session):
    first_response = client.post("/api/seller-profile", json=payload())
    assert first_response.status_code == 201
    first = first_response.json()
    changed = payload(1)
    changed["profile"]["value_proposition"] = "New wording supplied for review."
    second_response = client.post("/api/seller-profile", json=changed)
    assert second_response.status_code == 201
    second = second_response.json()
    assert first["id"] != second["id"]
    assert first["content_hash"] != second["content_hash"]
    assert (first["version"], second["version"]) == (1, 2)
    assert first["status"] == second["status"] == "draft"
    assert first["editor_label"] == "user:test-operator"
    assert client.get("/api/seller-profile").json() == second
    history = client.get("/api/seller-profile/versions?limit=1").json()
    assert history["items"] == [second]
    assert history["total"] == 2
    assert history["has_more"] is True
    older = client.get("/api/seller-profile/versions?limit=1&offset=1").json()
    assert older["items"] == [first]
    assert older["has_more"] is False
    events = db_session.scalars(select(WorkflowEvent).where(
        WorkflowEvent.event_type == "seller_profile_draft_saved"
    )).all()
    assert len(events) == 2
    assert {row.event_data["seller_profile_id"] for row in events} == {first["id"], second["id"]}
    assert {row.event_data["content_hash"] for row in events} == {first["content_hash"], second["content_hash"]}


def test_same_save_can_retry_without_new_version_or_event(client, db_session):
    first = client.post("/api/seller-profile", json=payload()).json()
    retry = client.post("/api/seller-profile", json=payload())
    assert retry.status_code == 200
    assert retry.json() == first
    unchanged = client.post("/api/seller-profile", json=payload(1))
    assert unchanged.status_code == 200
    assert unchanged.json() == first
    assert db_session.scalar(select(func.count()).select_from(SellerProfile)) == 1
    assert db_session.scalar(select(func.count()).select_from(WorkflowEvent).where(~WorkflowEvent.event_type.like("auth_%"))) == 1


def test_stale_tab_cannot_replace_newer_content(client):
    client.post("/api/seller-profile", json=payload())
    changed = payload(1)
    changed["profile"]["company_name"] = "Newer fixture seller"
    newer = client.post("/api/seller-profile", json=changed).json()
    old_tab = payload(1)
    old_tab["profile"]["product_name"] = "Stale tab change"
    response = client.post("/api/seller-profile", json=old_tab)
    assert response.status_code == 409
    assert client.get("/api/seller-profile").json() == newer
    assert client.get("/api/seller-profile/versions").json()["total"] == 2


@pytest.mark.parametrize("change", [
    {"company_name": "   "},
    {"product_name": ""},
    {"value_proposition": " "},
    {"target_customer": ""},
    {"profile_kind": "approved"},
    {"capabilities": ["   "]},
    {"capabilities": ["Feature"] * 13},
    {"proof_points": [{"claim": "Claim", "source": " "}]},
    {"proof_points": [{"claim": "", "source": "Reference"}]},
    {"unexpected": "Ignored data would be misleading"},
])
def test_invalid_profile_is_rejected_before_any_write(client, db_session, change):
    request = payload()
    request["profile"].update(change)
    assert client.post("/api/seller-profile", json=request).status_code == 422
    assert db_session.scalar(select(func.count()).select_from(SellerProfile)) == 0
    assert db_session.scalar(select(func.count()).select_from(WorkflowEvent).where(~WorkflowEvent.event_type.like("auth_%"))) == 0


def test_unsupported_claims_can_be_left_empty_and_whitespace_is_normalized(client):
    request = payload()
    request["profile"]["company_name"] = "  Fixture Seller  "
    request["profile"]["proof_points"] = []
    result = client.post("/api/seller-profile", json=request)
    assert result.status_code == 201
    assert result.json()["profile"]["company_name"] == "Fixture Seller"
    assert result.json()["profile"]["proof_points"] == []


def test_audit_failure_rolls_back_the_profile_too(client, db_engine, db_session):
    def fail_event(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO workflow_events"):
            raise RuntimeError("Synthetic audit failure")

    event.listen(db_engine, "before_cursor_execute", fail_event)
    try:
        with pytest.raises(RuntimeError, match="Synthetic audit failure"):
            client.post("/api/seller-profile", json=payload())
    finally:
        event.remove(db_engine, "before_cursor_execute", fail_event)
    assert db_session.scalar(select(func.count()).select_from(SellerProfile)) == 0
    assert db_session.scalar(select(func.count()).select_from(WorkflowEvent).where(~WorkflowEvent.event_type.like("auth_%"))) == 0
    assert client.post("/api/seller-profile", json=payload()).status_code == 201


def test_database_unique_version_race_is_reported_as_conflict(client, db_session_factory, monkeypatch):
    from app.services import seller_profiles

    real_latest = seller_profiles.latest_seller_profile
    raced = False

    def insert_winner_after_lookup(session):
        nonlocal raced
        previous = real_latest(session)
        if not raced:
            raced = True
            with db_session_factory() as other:
                request = deepcopy(payload())
                request["profile"]["company_name"] = "Concurrent winner"
                from app.schemas.seller_profile import SellerProfileCreate
                seller_profiles.save_seller_profile(other, SellerProfileCreate.model_validate(request))
        return previous

    monkeypatch.setattr(seller_profiles, "latest_seller_profile", insert_winner_after_lookup)
    result = client.post("/api/seller-profile", json=payload())
    assert result.status_code == 409
    assert client.get("/api/seller-profile").json()["profile"]["company_name"] == "Concurrent winner"
    assert client.get("/api/seller-profile/versions").json()["total"] == 1


def test_profile_draft_does_not_activate_generation_or_modify_leads(client, db_session):
    upload = client.post("/api/batches/upload", files={
        "file": ("fixture.csv", b"company_name,status\nFixture lead,do_not_contact\n", "text/csv")
    }).json()
    lead = client.get(f"/api/leads?batch_id={upload['batch_id']}").json()["items"][0]
    before = client.get(f"/api/leads/{lead['id']}/readiness").json()
    assert client.post("/api/seller-profile", json=payload()).status_code == 201
    after = client.get(f"/api/leads/{lead['id']}/readiness").json()
    assert before["readiness"] == after["readiness"]
    assert "no_seller_profile_configured" in after["readiness"]["outbound_email"]["gaps"]
    assert client.get(f"/api/leads/{lead['id']}").json()["status"] == "do_not_contact"
    for model in (AIOutput, AIOutputReview, IntegrationPush):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0


def test_history_page_limit_is_bounded(client):
    assert client.get("/api/seller-profile/versions?limit=201").status_code == 422
