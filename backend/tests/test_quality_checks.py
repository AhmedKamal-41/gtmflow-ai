"""Phase 10: runtime quality checks (runtime-checks-v1) and the approval
acknowledgement they require. The frozen heldout-criteria-v1 lints are
untouched and still behave as recorded in Phase 9."""
from __future__ import annotations

import copy

from sqlalchemy import select

from app.ai import quality_checks as Q
from app.evaluation import criteria, metrics
from app.models import AIOutputReview, WorkflowEvent
from tests.conftest import SYNTHETIC_SELLER_PROFILE, save_and_activate
from tests.test_ai_generation import _context
from tests.test_grounded_generation import ScriptedClient, count, upload, use_client
from app.ai.mock_client import MockAIClient


def _outreach(ctx, **changes):
    out = MockAIClient().generate_outreach(ctx)
    out.update(changes)
    return out


def _with_name(ctx, name):
    ctx = copy.deepcopy(ctx)
    for fact in ctx["lead_facts"]:
        if fact["field"] == "company_name":
            fact["value"] = name
    return ctx


def test_mock_outputs_carry_no_flags():
    for task in ("company_summary", "outreach_email"):
        ctx = _context(task)
        out = MockAIClient().generate_company_summary(ctx) if task == "company_summary" else MockAIClient().generate_outreach(ctx)
        assert Q.check_output(task, out, ctx) == []


def test_documented_outreach_problems_are_flagged():
    ctx = _context("outreach_email")
    cases = {
        "presumed_outreach_activity": {"email_body": "I would love to learn more about your current outreach strategies."},
        "commercial_opportunity_framing": {"subject": "Exploring Opportunities in Healthcare"},
        "placeholder_text": {"email_body": "Best,\n[Your Name]"},
        "invented_phrasing": {"email_body": "You are a key player in the market."},
        "literal_escape_text": {"email_body": "Hi\\nthere"},
    }
    for code, change in cases.items():
        assert Q.flag_codes(Q.check_output("outreach_email", _outreach(ctx, **change), ctx)) == [code], code


def test_a_company_name_is_not_invented_phrasing_but_the_frozen_lint_is_unchanged():
    ctx = _with_name(_context("outreach_email"), "Allergy & Immunology Specialists, LLC")
    body = "I noticed that Allergy & Immunology Specialists, LLC is a medical practice in Goodyear, Arizona."
    out = _outreach(ctx, email_body=body)
    assert "invented_phrasing" not in Q.flag_codes(Q.check_output("outreach_email", out, ctx))
    # heldout-criteria-v1 still flags it, exactly as recorded in Phase 9 §13.
    assert metrics.lints("outreach_email", out, ctx)["no_invented_phrasing"] is False
    assert criteria.CRITERIA_ID == "heldout-criteria-v1"
    # An invented specialization next to the name is still caught.
    out = _outreach(ctx, email_body=body + " We specialize in practices like yours.")
    assert "invented_phrasing" in Q.flag_codes(Q.check_output("outreach_email", out, ctx))


def test_collaboration_wording_in_the_internal_call_note_is_not_flagged():
    ctx = _context("outreach_email")
    out = _outreach(ctx, call_note="Explore potential collaboration.")
    assert Q.check_output("outreach_email", out, ctx) == []


def test_demo_label_is_required_only_for_demonstration_sellers():
    ctx = _context("outreach_email")
    demo = copy.deepcopy(ctx)
    demo["seller"]["profile_kind"] = "demo"
    plain = _outreach(ctx, email_body="Hello, a short note about scheduling.")
    assert Q.check_output("outreach_email", plain, ctx) == []
    assert Q.flag_codes(Q.check_output("outreach_email", plain, demo)) == ["missing_demo_label"]
    for label in ("This is a portfolio demonstration and not a commercial offer.",
                  "[Demonstration draft. Not a real offer.]"):
        assert Q.check_output("outreach_email", _outreach(ctx, email_body=label), demo) == []


def test_invented_need_hypotheses_are_flagged_but_stated_unknowns_are_not():
    ctx = _context("company_summary")
    out = MockAIClient().generate_company_summary(ctx)
    out["hypotheses"] = ["The company may have unmet needs in lead generation."]
    assert Q.flag_codes(Q.check_output("company_summary", out, ctx)) == ["need_or_interest_hypothesis"]
    out["hypotheses"] = ["It is unconfirmed whether the company has a current interest in purchasing services."]
    assert Q.check_output("company_summary", out, ctx) == []


# ------------------------------------------------ API: flags shown, acknowledgement required

def _flagged_draft(client, monkeypatch):
    save_and_activate(client, SYNTHETIC_SELLER_PROFILE)
    lead = upload(client)
    use_client(monkeypatch, ScriptedClient(outreach=lambda ctx: _outreach(ctx, subject="Exploring Opportunities together")))
    draft = client.post(f"/api/leads/{lead['id']}/generate-outreach").json()
    return lead, draft


def test_flagged_drafts_show_their_flags_and_need_an_exact_acknowledgement(client, db_session, monkeypatch):
    lead, draft = _flagged_draft(client, monkeypatch)
    assert [f["code"] for f in draft["quality_flags"]] == ["commercial_opportunity_framing"]
    assert draft["quality_checks_version"] == "runtime-checks-v1"
    state = client.get(f"/api/leads/{lead['id']}/review-state").json()
    assert [f["code"] for f in state["draft_quality_flags"]] == ["commercial_opportunity_framing"]
    event = db_session.scalar(select(WorkflowEvent).where(WorkflowEvent.event_type == "outreach_generated"))
    assert event.event_data["quality_flags"] == ["commercial_opportunity_framing"]

    body = {"ai_output_id": draft["id"], "content_hash": draft["content_hash"]}
    for ack in ([], ["placeholder_text"], ["commercial_opportunity_framing", "placeholder_text"]):
        response = client.post(f"/api/leads/{lead['id']}/approve-outreach", json={**body, "acknowledged_quality_flags": ack})
        assert response.status_code == 409 and "commercial_opportunity_framing" in response.json()["detail"]
    assert count(db_session, AIOutputReview) == 0  # nothing approved, nothing auto-rejected

    ok = client.post(f"/api/leads/{lead['id']}/approve-outreach",
                     json={**body, "acknowledged_quality_flags": ["commercial_opportunity_framing"]})
    assert ok.status_code == 200
    approved = db_session.scalar(select(WorkflowEvent).where(WorkflowEvent.event_type == "outreach_approved"))
    assert approved.event_data["acknowledged_quality_flags"] == ["commercial_opportunity_framing"]
    assert approved.event_data["quality_checks_version"] == "runtime-checks-v1"


def test_rejecting_a_flagged_draft_needs_no_acknowledgement(client, db_session, monkeypatch):
    lead, draft = _flagged_draft(client, monkeypatch)
    response = client.post(f"/api/leads/{lead['id']}/reject-outreach",
                           json={"ai_output_id": draft["id"], "content_hash": draft["content_hash"], "reason": "framing"})
    assert response.status_code == 200


def test_a_human_edit_that_fixes_the_flag_approves_without_acknowledgement(client, db_session, monkeypatch):
    lead, draft = _flagged_draft(client, monkeypatch)
    fixed = dict(draft["content"], subject="A note about Synthetic Scheduler")
    revision = client.post(f"/api/leads/{lead['id']}/ai-outputs/{draft['id']}/revisions",
                           json={"expected_content_hash": draft["content_hash"], "content": fixed}).json()
    assert revision["quality_flags"] == []
    response = client.post(f"/api/leads/{lead['id']}/approve-outreach",
                           json={"ai_output_id": revision["id"], "content_hash": revision["content_hash"]})
    assert response.status_code == 200
