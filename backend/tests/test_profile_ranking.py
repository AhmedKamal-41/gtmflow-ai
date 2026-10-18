"""Profile-based ranking end to end: leads are ranked against the ACTIVE
seller profile's target customers, drafts use that same profile, and a
change to the target customers flows consistently through rankings, draft
context, approvals, lists, jobs, the assistant and delivery. Mock AI and
mock Slack only."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models import AIOutputReview, IntegrationPush, Lead, LeadFitScore, LeadScore
from tests.conftest import SYNTHETIC_SELLER_PROFILE, activate_preferences, save_and_activate
from tests.test_background_jobs import _enqueue, _run

NO_PREFERENCES = {"industries": [], "locations": [], "company_sizes": [], "contact_roles": [], "keywords": []}
CLINIC_SELLER = {
    "industries": ["Healthcare", "Clinic"], "locations": ["United States"],
    "company_sizes": [{"min_employees": 201, "max_employees": None}],
    "contact_roles": ["Practice Manager"], "keywords": [],
}
SMALL_SOFTWARE_SELLER = {
    "industries": ["Software", "SaaS"], "locations": [],
    "company_sizes": [{"min_employees": 1, "max_employees": 50}],
    "contact_roles": ["Founder", "CTO"], "keywords": [],
}
CSV = (
    "company_name,industry,company_size,location,contact_title,contact_name,contact_email\n"
    "Northbridge Clinics,Healthcare,1200,USA,Practice Manager,Anika Rao,anika@northbridge.example\n"
    "Loopline Software,SaaS,22,UK,Founder,Daniel Park,dan@loopline.example\n"
    "Grand Hospitality Group,Hospitality,900,USA,Practice Manager,Ida Moss,ida@grand.example\n"
    "Bare Record Co,,,,,,\n"
)


def _upload(client, csv: str = CSV) -> tuple[str, dict[str, str]]:
    up = client.post("/api/batches/upload", files={"file": ("leads.csv", csv.encode(), "text/csv")}).json()
    leads = client.get(f"/api/leads?batch_id={up['batch_id']}&limit=50").json()["items"]
    return up["batch_id"], {lead["company_name"]: lead["id"] for lead in leads}


def _inbox(client) -> dict[str, dict]:
    return {item["company_name"]: item for item in client.get("/api/inbox?limit=100").json()["items"]}


def _rank(client, lead_id: str) -> dict:
    return client.get(f"/api/leads/{lead_id}/fit-score").json()


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model).where(*where)) or 0


# ------------------------------------------------------------ ranking follows the seller

def test_the_same_company_ranks_differently_for_sellers_with_different_target_customers(client):
    activate_preferences(client, CLINIC_SELLER)
    _, ids = _upload(client)
    assert client.post("/api/scoring/recalculate").status_code == 200
    clinic_view = {name: (row["priority"], row["score"]) for name, row in _inbox(client).items()}
    assert clinic_view["Northbridge Clinics"] == ("Hot", 100)
    assert clinic_view["Loopline Software"][0] == "Cold"

    activate_preferences(client, SMALL_SOFTWARE_SELLER)
    client.post("/api/scoring/recalculate")
    software_view = {name: (row["priority"], row["score"]) for name, row in _inbox(client).items()}
    assert software_view["Loopline Software"] == ("Hot", 100)  # small company, small-company seller
    assert software_view["Northbridge Clinics"][0] == "Cold"  # larger is not better here
    # Lists, sorting and filters follow the same current ranking.
    hot = client.get("/api/leads?fit_band=strong_match").json()["items"]
    assert [lead["company_name"] for lead in hot] == ["Loopline Software"]
    ordered = client.get("/api/leads?sort=fit_score_desc&limit=50").json()["items"]
    assert ordered[0]["company_name"] == "Loopline Software"


def test_hospitality_is_not_ranked_as_a_hospital_seller_target(client):
    activate_preferences(client, {**NO_PREFERENCES, "industries": ["Hospital"]})
    _, ids = _upload(client)
    client.post("/api/scoring/recalculate")
    industry = next(c for c in _rank(client, ids["Grand Hospitality Group"])["criteria"] if c["name"] == "industry")
    assert industry["result"] == "mismatch"


def test_missing_information_is_reported_honestly(client):
    activate_preferences(client, CLINIC_SELLER)
    _, ids = _upload(client)
    client.post("/api/scoring/recalculate")
    bare = _rank(client, ids["Bare Record Co"])
    assert bare["band"] == "insufficient_evidence" and bare["priority"] is None
    assert bare["fit_score"] == 0 and bare["evidence_coverage_pct"] == 0.0
    assert bare["summary"]["mismatched"] == []
    assert bare["summary"]["missing"] == ["Industry", "Company size", "Location", "Contact role"]
    assert bare["explanation"].startswith("Not enough information")
    inbox = client.get("/api/inbox?priority=needs_data").json()
    assert [item["company_name"] for item in inbox["items"]] == ["Bare Record Co"]
    assert inbox["counts"]["needs_data"] == 1
    # Contact completeness is reported separately from fit.
    assert "missing_contact_email" in bare["readiness"]["outbound_email"]["gaps"]


def test_without_target_preferences_setup_is_required_and_no_fallback_is_used(client, db_session):
    _, ids = _upload(client)
    assert client.get("/api/scoring/profile").json()["state"] == "no_active_profile"
    save_and_activate(client, {**SYNTHETIC_SELLER_PROFILE, "target_preferences": NO_PREFERENCES})
    status = client.get("/api/scoring/profile").json()
    assert status["state"] == "setup_required" and "no target customers" in status["message"]
    for response in (client.post("/api/scoring/recalculate"),
                     client.post(f"/api/leads/{ids['Northbridge Clinics']}/fit-score")):
        assert response.status_code == 409 and "target customers" in response.json()["detail"]
    assert _count(db_session, LeadFitScore) == 0
    page = client.get("/api/inbox").json()
    assert page["ranking"]["state"] == "setup_required"
    assert all(item["priority"] is None for item in page["items"])


def test_old_demo_criteria_and_retired_scores_no_longer_affect_anything(client, db_session, db_session_factory):
    """A lead carrying a retired v1 'Hot' score and a demo-rubric 'strong'
    ranking is ranked only by the active seller's targets everywhere."""
    from app.models import LeadBatch

    activate_preferences(client, SMALL_SOFTWARE_SELLER)
    batch = LeadBatch(name="pdl", source="pdl", status="uploaded")
    db_session.add(batch)
    db_session.flush()
    lead = Lead(id=uuid4(), batch_id=batch.id, company_name="US Health System", industry="Hospital & Health Care",
                company_size="1001-5000", cleaned_data={"country": "united states"}, source="pdl_import",
                status="new")
    db_session.add(lead)
    now = datetime.now(timezone.utc)
    db_session.add(LeadScore(lead_id=lead.id, total_score=95, priority="Hot", score_breakdown={}, reasoning="v1"))
    db_session.add(LeadFitScore(
        lead_id=lead.id, scorer_version="fit-scorer-v1", profile_id="demo-us-sectors-v1", profile_version="1",
        normalization_version="fit-norm-v1", input_fingerprint="f" * 64, fit_score=100,
        evidence_coverage_pct=100.0, band="strong_match", criteria=[], readiness={}, eligibility_excluded=False,
        eligibility_reasons=[], computed_at=now, computation_ms=0.1, created_at=now))
    db_session.commit()

    # Before ranking: outdated, not Hot, anywhere.
    row = _inbox(client)["US Health System"]
    assert (row["priority"], row["ranking_outdated"], row["stage"]) == (None, True, "needs_score")
    assert client.get("/api/metrics/dashboard").json()["hot_leads"] == 0
    assert client.get("/api/leads?fit_band=strong_match").json()["total"] == 0
    # After ranking against the active profile: Cold, by the new rules only.
    client.post("/api/scoring/recalculate")
    current = _rank(client, str(lead.id))
    assert current["is_current"] and current["priority"] == "Cold" and current["fit_score"] == 0
    assert _inbox(client)["US Health System"]["priority"] == "Cold"
    # Delivery and the push job ignore the retired Hot score.
    push = client.post(f"/api/leads/{lead.id}/push", json={"integration_type": "slack"})
    assert push.status_code == 400 and "Only Hot leads" in push.json()["detail"]
    job = _enqueue(client, str(batch.id), "push_hot")
    assert _run(db_session_factory, job["id"]) == "completed"
    assert client.get(f"/api/jobs/{job['id']}").json()["total_items"] == 0
    # The assistant searches by the current ranking too.
    plan = client.post("/api/assistant/plan", json={"batch_id": str(batch.id), "request": "Hot leads"}).json()
    assert plan["status"] == "no_matches" and plan["leads"] == []


def test_push_hot_job_selects_only_current_hot_rankings(client, db_session_factory, db_session):
    activate_preferences(client, SMALL_SOFTWARE_SELLER)
    batch_id, ids = _upload(client)
    client.post("/api/scoring/recalculate")
    job = _enqueue(client, batch_id, "push_hot")
    assert _run(db_session_factory, job["id"]) == "completed"
    items = client.get(f"/api/jobs/{job['id']}/items").json()["items"]
    assert [i["lead_id"] for i in items] == [ids["Loopline Software"]]
    assert items[0]["outcome"]["reason"] == "not_approved"  # Hot alone never sends
    assert _count(db_session, IntegrationPush) == 0


# ------------------------------------------------------------ recalculation and history

def test_changing_targets_marks_rankings_outdated_and_recalculation_preserves_history(client, db_session):
    activate_preferences(client, CLINIC_SELLER)
    _, ids = _upload(client)
    first = client.post("/api/scoring/recalculate").json()
    assert (first["attempted"], first["newly_scored"]) == (4, 4)
    assert client.post("/api/scoring/recalculate").json()["skipped_unchanged"] == 4  # idempotent
    old_rows = {(r.id, r.fit_score, r.band) for r in db_session.scalars(select(LeadFitScore))}

    activate_preferences(client, SMALL_SOFTWARE_SELLER)
    status = client.get("/api/scoring/profile").json()
    assert (status["current_rankings"], status["outdated_rankings"]) == (0, 4)
    assert client.get("/api/inbox").json()["counts"]["ranking_outdated"] == 4
    outdated = _rank(client, ids["Northbridge Clinics"])
    assert outdated["is_current"] is False and outdated["priority"] == "Hot"  # labeled history

    run = client.post("/api/scoring/recalculate").json()
    assert run["newly_scored"] == 4 and run["profile_version"] == "2"
    db_session.expire_all()
    rows = {(r.id, r.fit_score, r.band) for r in db_session.scalars(select(LeadFitScore))}
    assert old_rows < rows and len(rows) == 8  # earlier rankings kept unchanged
    status = client.get("/api/scoring/profile").json()
    assert (status["current_rankings"], status["outdated_rankings"]) == (4, 0)


def test_a_revision_that_keeps_the_same_targets_keeps_rankings_current(client):
    activate_preferences(client, CLINIC_SELLER)
    _, ids = _upload(client)
    client.post("/api/scoring/recalculate")
    activate_preferences(client, CLINIC_SELLER, value_proposition="Reworded offer, same customers.")
    assert client.get("/api/scoring/profile").json()["outdated_rankings"] == 0
    assert _rank(client, ids["Northbridge Clinics"])["is_current"] is True


# ------------------------------------------------------------ drafts follow the ranking profile

def _approve(client, lead_id: str, draft: dict):
    return client.post(f"/api/leads/{lead_id}/approve-outreach", json={
        "ai_output_id": draft["id"], "content_hash": draft["content_hash"],
        "acknowledged_quality_flags": sorted({f["code"] for f in draft.get("quality_flags") or []})})


def test_a_profile_change_updates_ranking_and_draft_context_together(client, db_session):
    clinic = activate_preferences(client, CLINIC_SELLER)
    _, ids = _upload(client)
    lead_id = ids["Northbridge Clinics"]
    client.post("/api/scoring/recalculate")
    ranking = _rank(client, lead_id)
    draft = client.post(f"/api/leads/{lead_id}/generate-outreach").json()
    context = draft["input_snapshot"]
    # The draft uses the same seller revision that ranked the lead, its
    # approved claims, and the lead's verified facts -- and is told the
    # ranking is not evidence of need or intent.
    assert draft["seller_profile_id"] == clinic["id"] == ranking["seller_profile_id"]
    assert context["company_fit"]["targeting_hash"] == ranking["targeting_hash"]
    assert context["company_fit"]["band"] == "strong_match"
    assert "do not say or imply that the company needs" in context["company_fit"]["meaning"]
    assert [c["claim"] for c in context["seller"]["approved_claims"]] == \
        [p["claim"] for p in SYNTHETIC_SELLER_PROFILE["proof_points"]]
    assert "buying_intent" in context["unknowns"]
    assert draft["quality_flags"] == []
    assert _approve(client, lead_id, draft).status_code == 200
    assert client.get(f"/api/leads/{lead_id}/review-state").json()["approval_applicable"] is True

    # New target customers: the approval stops applying everywhere at once.
    software = activate_preferences(client, SMALL_SOFTWARE_SELLER)
    state = client.get(f"/api/leads/{lead_id}/review-state").json()
    assert state["approval_applicable"] is False
    assert "draft_not_from_active_seller_profile" in state["delivery_blockers"]
    assert _inbox(client)["Northbridge Clinics"]["stage"] == "outdated"
    push = client.post(f"/api/leads/{lead_id}/push", json={"integration_type": "slack", "force": True})
    assert push.status_code == 400  # no current ranking -> refused before delivery
    client.post("/api/scoring/recalculate")
    push = client.post(f"/api/leads/{lead_id}/push", json={"integration_type": "slack", "force": True})
    assert push.status_code == 409 and "no approval that applies" in push.json()["detail"]

    # A new draft uses the new revision and the new ranking, together.
    new_ranking = _rank(client, lead_id)
    redraft = client.post(f"/api/leads/{lead_id}/generate-outreach").json()
    assert redraft["seller_profile_id"] == software["id"] == new_ranking["seller_profile_id"]
    assert redraft["input_snapshot"]["company_fit"]["targeting_hash"] == new_ranking["targeting_hash"]
    assert redraft["input_snapshot"]["company_fit"]["band"] == new_ranking["band"] == "weak_match"
    assert _count(db_session, AIOutputReview) == 1  # nothing was approved automatically


def test_selected_ranked_leads_get_drafts_from_the_active_profile_only(client, db_session_factory, db_session):
    seller = activate_preferences(client, CLINIC_SELLER)
    batch_id, ids = _upload(client)
    client.post("/api/scoring/recalculate")
    blocked = db_session.get(Lead, UUID(ids["Grand Hospitality Group"]))
    blocked.status = "do_not_contact"
    db_session.commit()
    picked = [ids["Northbridge Clinics"], ids["Grand Hospitality Group"]]
    job = _enqueue(client, batch_id, "selected_outreach", lead_ids=picked)
    assert _run(db_session_factory, job["id"]) == "completed"
    items = {i["lead_id"]: i for i in client.get(f"/api/jobs/{job['id']}/items").json()["items"]}
    assert set(items) == set(picked)  # only the selection
    assert items[ids["Northbridge Clinics"]]["status"] == "succeeded"
    assert items[ids["Grand Hospitality Group"]]["outcome"]["reason"] == "lead_status:do_not_contact"
    draft = client.get(f"/api/leads/{ids['Northbridge Clinics']}/latest-ai-output?output_type=outreach_email")
    assert draft.json()["seller_profile_id"] == seller["id"]
    assert _count(db_session, AIOutputReview) == 0  # drafting never approves


def test_selected_outreach_refuses_a_lead_whose_ranking_is_outdated(client, db_session_factory):
    activate_preferences(client, CLINIC_SELLER)
    batch_id, ids = _upload(client)
    client.post("/api/scoring/recalculate")
    activate_preferences(client, SMALL_SOFTWARE_SELLER)  # rankings now outdated
    job = _enqueue(client, batch_id, "selected_outreach", lead_ids=[ids["Loopline Software"]])
    assert _run(db_session_factory, job["id"]) == "completed"
    [item] = client.get(f"/api/jobs/{job['id']}/items").json()["items"]
    assert (item["status"], item["outcome"]["reason"]) == ("blocked", "ranking_not_current")


@pytest.mark.parametrize("params", [{}, {"lead_ids": []}, {"lead_ids": ["not-a-uuid"]},
                                    {"lead_ids": [str(uuid4())] * 2}, {"lead_ids": [str(uuid4())], "x": 1}])
def test_selected_outreach_validates_its_selection(client, params):
    activate_preferences(client, CLINIC_SELLER)
    batch_id, _ = _upload(client)
    response = client.post(f"/api/batches/{batch_id}/jobs", json={"job_type": "selected_outreach", "params": params})
    assert response.status_code == 400


# ------------------------------------------------------------ protections unchanged

def test_hot_ranking_never_overrides_blocks_and_delivery_is_not_duplicated(client, db_session):
    activate_preferences(client, CLINIC_SELLER)
    _, ids = _upload(client)
    client.post("/api/scoring/recalculate")
    lead_id = ids["Northbridge Clinics"]
    draft = client.post(f"/api/leads/{lead_id}/generate-outreach").json()
    # Exact-draft approval: a different content hash is refused.
    wrong = client.post(f"/api/leads/{lead_id}/approve-outreach",
                        json={"ai_output_id": draft["id"], "content_hash": "0" * 64})
    assert wrong.status_code == 409
    assert _approve(client, lead_id, draft).status_code == 200
    first = client.post(f"/api/leads/{lead_id}/push", json={"integration_type": "slack"})
    again = client.post(f"/api/leads/{lead_id}/push", json={"integration_type": "slack"})
    assert first.status_code == again.status_code == 200
    assert again.json()["replay"] is True and _count(db_session, IntegrationPush) == 1

    lead = db_session.get(Lead, UUID(lead_id))
    lead.status = "unsubscribed"
    db_session.commit()
    assert _rank(client, lead_id)["priority"] == "Hot"
    blocked = client.post(f"/api/leads/{lead_id}/push",
                          json={"integration_type": "slack", "force": True, "redeliver": True})
    assert blocked.status_code == 400 and "unsubscribed" in blocked.json()["detail"]
    client.post(f"/api/leads/{lead_id}/fit-score")
    db_session.expire_all()
    assert db_session.get(Lead, UUID(lead_id)).status == "unsubscribed"  # ranking never clears it
    assert _count(db_session, IntegrationPush) == 1
