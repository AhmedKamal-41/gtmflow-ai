"""Phase 6: split manifest isolation, pilot selection, the annotation
workbench API, separation from operational review, and export."""
from __future__ import annotations

import json
import random
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import (
    AIOutput,
    AIOutputReview,
    AnnotationCandidate,
    CompanyIdentity,
    CompanySplitAssignment,
    Lead,
    LeadBatch,
    SourceSnapshot,
    TrainingAnnotation,
)
from app.services import splits
from tests.conftest import SYNTHETIC_SELLER_PROFILE, save_and_activate

SHARED_DOMAINS = {3: "shared-a.example", 4: "shared-a.example", 5: "shared-a.example",
                  10: "shared-b.example", 11: "shared-b.example"}


def seed_cohort(session: Session, n: int = 120, order_seed: int | None = None) -> list[Lead]:
    """PDL-like leads: one snapshot, one identity per lead (never merged),
    a few identities sharing a website domain, two segments."""
    snapshot = SourceSnapshot(provider="people_data_labs", mirror_url="https://example.invalid/fixture",
                              retrieved_at=datetime(2026, 9, 22, tzinfo=timezone.utc))
    batch = LeadBatch(name="fixture import", source="pdl_import", status="uploaded")
    session.add_all([snapshot, batch])
    session.flush()
    indices = list(range(n))
    if order_seed is not None:
        random.Random(order_seed).shuffle(indices)
    leads = []
    for i in indices:
        domain = SHARED_DOMAINS.get(i, f"company-{i}.example")
        identity = CompanyIdentity(canonical_name=f"Company {i}", website_domain=domain,
                                   source_snapshot_id=snapshot.id, source_record_id=f"rec-{i}")
        session.add(identity)
        session.flush()
        segment = "healthcare" if i % 2 else "real_estate"
        lead = Lead(batch_id=batch.id, company_name=f"Company {i}", website=domain,
                    industry="medical practice" if i % 2 else "real estate",
                    company_size="11-50", location="austin, texas, united states",
                    source="pdl", company_identity_id=identity.id, source_snapshot_id=snapshot.id,
                    source_record_id=f"rec-{i}", source_raw_data={"id": f"rec-{i}"},
                    cleaned_data={"candidate_segment": segment, "country": "united states"})
        session.add(lead)
        leads.append(lead)
    session.commit()
    return leads


@pytest.fixture()
def cohort(db_session):
    return seed_cohort(db_session)


def fresh_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)()


# ------------------------------------------------ manifest

def test_manifest_keeps_shared_domains_together_without_merging_identities(db_session, cohort):
    manifest = splits.freeze_manifest(db_session)
    db_session.commit()
    by_record = {
        lead.source_record_id: a for a, lead in db_session.execute(
            select(CompanySplitAssignment, Lead).join(Lead, Lead.id == CompanySplitAssignment.lead_id))
    }
    shared_a = {by_record[f"rec-{i}"].group_key for i in (3, 4, 5)}
    shared_b = {by_record[f"rec-{i}"].group_key for i in (10, 11)}
    assert len(shared_a) == 1 and len(shared_b) == 1
    assert {by_record[f"rec-{i}"].split for i in (3, 4, 5)} == {by_record["rec-3"].split}
    assert manifest.lead_count == 120
    assert manifest.group_count == 120 - 3  # 5 leads collapse into 2 groups
    assert db_session.scalar(select(func.count()).select_from(CompanyIdentity)) == 120
    assert splits.verify_manifest(db_session)["groups_spanning_splits"] == 0
    assert set(manifest.counts["leads_by_split"]) == {"train", "validation", "test"}


def test_manifest_is_reproducible_regardless_of_row_order():
    digests = []
    assignments = []
    for order in (None, 7):
        session = fresh_session()
        seed_cohort(session, order_seed=order)
        manifest = splits.freeze_manifest(session)
        session.commit()
        digests.append(manifest.manifest_sha256)
        assignments.append({
            lead.source_record_id: a.split for a, lead in session.execute(
                select(CompanySplitAssignment, Lead).join(Lead, Lead.id == CompanySplitAssignment.lead_id))
        })
        session.close()
    assert digests[0] == digests[1]
    assert assignments[0] == assignments[1]


def test_manifest_is_frozen_and_verification_detects_tampering(db_session, cohort):
    splits.freeze_manifest(db_session)
    db_session.commit()
    with pytest.raises(splits.ManifestError):
        splits.freeze_manifest(db_session)
    assert splits.verify_manifest(db_session)["matches"] is True
    row = db_session.scalars(select(CompanySplitAssignment)).first()
    row.split = "test" if row.split != "test" else "train"
    db_session.commit()
    result = splits.verify_manifest(db_session)
    assert result["matches"] is False and result["mismatched_leads"] == 1


def test_name_aliases_are_grouped_conservatively():
    assert splits.normalize_name("Acme Dental, LLC") == splits.normalize_name("ACME Dental Inc.")
    assert splits.normalize_domain("https://www.Acme.com/about") == "acme.com"


# ------------------------------------------------ pilot

def test_pilot_uses_only_training_companies_and_keeps_tasks_together(db_session, cohort):
    splits.freeze_manifest(db_session)
    rows = splits.create_pilot_queue(db_session, max_examples=40)
    db_session.commit()
    assert len(rows) == 40
    split_by_lead = {a.lead_id: a.split for a in db_session.scalars(select(CompanySplitAssignment))}
    assert {split_by_lead[r.lead_id] for r in rows} == {"train"}
    assert {r.split for r in rows} == {"train"}
    per_lead = {}
    for r in rows:
        per_lead.setdefault(r.lead_id, set()).add(r.task)
    assert all(tasks == {"company_summary", "outreach_email"} for tasks in per_lead.values())
    assert len(per_lead) == 20
    # One company per group: shared-domain groups contribute at most one.
    groups = [r.group_key for r in rows if r.task == "company_summary"]
    assert len(groups) == len(set(groups))
    segments = [db_session.get(Lead, r.lead_id).cleaned_data["candidate_segment"] for r in rows if r.task == "company_summary"]
    assert segments.count("healthcare") == segments.count("real_estate") == 10
    with pytest.raises(splits.ManifestError):
        splits.create_pilot_queue(db_session)


def test_pilot_selection_is_reproducible_and_skips_excluded_leads():
    picks = []
    for order in (None, 3):
        session = fresh_session()
        leads = seed_cohort(session, order_seed=order)
        blocked = next(l for l in leads if l.source_record_id == "rec-0")
        blocked.status = "do_not_contact"
        session.commit()
        splits.freeze_manifest(session)
        rows = splits.create_pilot_queue(session)
        session.commit()
        picks.append([(r.position, session.get(Lead, r.lead_id).source_record_id, r.task) for r in rows])
        assert "rec-0" not in {p[1] for p in picks[-1]}
        session.close()
    assert picks[0] == picks[1]
    assert len(picks[0]) == 2 * len({p[1] for p in picks[0]})


# ------------------------------------------------ workbench API

@pytest.fixture()
def queue(client, db_session, cohort):
    splits.freeze_manifest(db_session)
    splits.create_pilot_queue(db_session, max_examples=6)
    db_session.commit()
    items = client.get("/api/annotation/queues/pilot-v1/candidates").json()["items"]
    return items


def generate(client, candidate_id, provider="mock"):
    return client.post(f"/api/annotation/candidates/{candidate_id}/generate", json={"provider": provider})


def submit(client, candidate_id, detail, **fields):
    body = {
        "submission_id": str(uuid4()),
        "source_output_id": detail["source_output"]["id"],
        "source_content_hash": detail["source_output"]["content_hash"],
        **fields,
    }
    return client.post(f"/api/annotation/candidates/{candidate_id}/annotations", json=body)


ASSESSED = {"factual_support": "supported", "writing_quality": 4, "missing_info_handling": "good"}


def test_candidates_start_awaiting_generation_with_explicit_provider(client, queue):
    summary = next(c for c in queue if c["task"] == "company_summary")
    outreach = next(c for c in queue if c["task"] == "outreach_email")
    assert {c["status"] for c in queue} == {"awaiting_generation"}
    assert generate(client, summary["id"], provider="openai").status_code == 409
    assert generate(client, outreach["id"]).status_code == 409  # no active seller

    detail = generate(client, summary["id"]).json()
    assert detail["status"] == "pending_review"
    assert detail["is_mock"] is True
    assert detail["source_output"]["purpose"] == "annotation"
    assert detail["source_output"]["model_used"] == "mock"
    assert generate(client, summary["id"]).status_code == 409  # never replaced
    info = client.get("/api/annotation/provider").json()
    assert info["configured_provider"] == "mock" and info["is_mock"] is True


def test_accept_requires_full_support_and_skip_requires_reason(client, queue):
    candidate = next(c for c in queue if c["task"] == "company_summary")
    detail = generate(client, candidate["id"]).json()
    weak = dict(ASSESSED, factual_support="partially_supported")
    assert submit(client, candidate["id"], detail, decision="accepted", **weak).status_code == 422
    assert submit(client, candidate["id"], detail, decision="accepted").status_code == 422
    assert submit(client, candidate["id"], detail, decision="skipped").status_code == 422
    assert submit(client, candidate["id"], detail, decision="accepted", reviewer_label="Me", **ASSESSED).status_code == 422
    assert submit(client, candidate["id"], detail, decision="accepted", **ASSESSED).status_code == 201


def test_stale_output_or_hash_is_refused_and_retries_are_idempotent(client, queue):
    candidate = next(c for c in queue if c["task"] == "company_summary")
    detail = generate(client, candidate["id"]).json()
    stale = deepcopy(detail)
    stale["source_output"]["content_hash"] = "b" * 64
    assert submit(client, candidate["id"], stale, decision="accepted", **ASSESSED).status_code == 409

    body = {"submission_id": str(uuid4()), "source_output_id": detail["source_output"]["id"],
            "source_content_hash": detail["source_output"]["content_hash"], "decision": "accepted", **ASSESSED}
    url = f"/api/annotation/candidates/{candidate['id']}/annotations"
    first, retry = client.post(url, json=body), client.post(url, json=body)
    assert (first.status_code, retry.status_code) == (201, 200)
    assert first.json()["id"] == retry.json()["id"]


def test_correction_is_exported_exactly_and_unreviewed_or_skipped_are_not(client, db_session, queue):
    save_and_activate(client, dict(SYNTHETIC_SELLER_PROFILE, profile_kind="demo"))
    summary_c, outreach_c, skip_c, untouched = queue[0], queue[1], queue[2], queue[3]
    for c in (summary_c, outreach_c, skip_c, untouched):
        assert generate(client, c["id"]).status_code == 200

    outreach_detail = client.get(f"/api/annotation/candidates/{outreach_c['id']}").json()
    corrected = deepcopy(outreach_detail["source_output"]["content"])
    corrected_text = "Hi there,\n\nA carefully corrected first line that states only record facts."
    corrected["email_body"] = corrected_text
    response = submit(client, outreach_c["id"], outreach_detail, decision="corrected",
                      corrected_content=corrected, notes="Tightened the opening.",
                      **dict(ASSESSED, factual_support="partially_supported", writing_quality=2))
    assert response.status_code == 201, response.text
    target_id = response.json()["target_output_id"]
    assert target_id != outreach_detail["source_output"]["id"]
    revision = db_session.get(AIOutput, UUID(target_id))
    assert revision.purpose == "annotation" and revision.origin == "human_edited"
    assert revision.parent_output_id == UUID(outreach_detail["source_output"]["id"])
    # The model output is unchanged.
    source = db_session.get(AIOutput, UUID(outreach_detail["source_output"]["id"]))
    assert source.content == outreach_detail["source_output"]["content"]

    summary_detail = client.get(f"/api/annotation/candidates/{summary_c['id']}").json()
    assert submit(client, summary_c["id"], summary_detail, decision="accepted", **ASSESSED).status_code == 201
    skip_detail = client.get(f"/api/annotation/candidates/{skip_c['id']}").json()
    assert submit(client, skip_c["id"], skip_detail, decision="skipped", skip_reason="Record too sparse").status_code == 201

    lines = [json.loads(l) for l in client.get("/api/annotation/export").text.splitlines()]
    assert {l["candidate_id"] for l in lines} == {summary_c["id"], outreach_c["id"]}
    row = next(l for l in lines if l["candidate_id"] == outreach_c["id"])
    assert row["target"]["email_body"] == corrected_text
    assert row["decision"] == "corrected" and row["review_mode"] == "correct"
    assert row["target_output_id"] == target_id
    assert row["source_output_id"] == outreach_detail["source_output"]["id"]
    assert row["is_mock"] is True and row["is_demo_seller"] is True
    assert row["reviewer_label"] == "local-demo-unauthenticated" and row["reviewer_authenticated"] is False
    assert row["notes"] == "Tightened the opening."
    assert row["split"] == "train" and row["manifest_version"] == "company-groups-v1"
    assert row["input_hash"] == outreach_detail["source_output"]["input_hash"]
    for key in ("prompt_version", "output_schema_version", "model_revision", "seller_profile_content_hash",
                "company_group_key", "input_snapshot", "reviewed_at", "source_content_hash", "target_content_hash"):
        assert row[key]

    summary = client.get("/api/annotation/queues/pilot-v1/summary").json()
    assert summary["reviewed_examples"] == 2 and summary["skipped"] == 1
    assert summary["pending_review"] == 1 and summary["awaiting_generation"] == 2
    assert summary["reviewed_unique_companies"] == 1  # both tasks of the first company


def test_changed_annotation_decision_is_a_new_row_and_latest_wins(client, db_session, queue):
    candidate = next(c for c in queue if c["task"] == "company_summary")
    detail = generate(client, candidate["id"]).json()
    submit(client, candidate["id"], detail, decision="accepted", **ASSESSED)
    submit(client, candidate["id"], detail, decision="skipped", skip_reason="Changed my mind")
    assert db_session.scalar(select(func.count()).select_from(TrainingAnnotation)) == 2
    assert client.get(f"/api/annotation/candidates/{candidate['id']}").json()["status"] == "skipped"
    assert client.get("/api/annotation/export").text == ""


def test_annotation_and_operational_approval_never_grant_each_other(client, db_session, queue):
    save_and_activate(client, SYNTHETIC_SELLER_PROFILE)
    candidate = next(c for c in queue if c["task"] == "outreach_email")
    detail = generate(client, candidate["id"]).json()
    submit(client, candidate["id"], detail, decision="accepted", **ASSESSED)
    lead_id = candidate["lead_id"]
    # Annotation acceptance created no operational review or draft.
    assert db_session.scalar(select(func.count()).select_from(AIOutputReview)) == 0
    assert client.get(f"/api/leads/{lead_id}/review-state").json()["status"] == "no_draft"
    # Operational approval of a real draft creates no training example.
    draft = client.post(f"/api/leads/{lead_id}/generate-outreach").json()
    client.post(f"/api/leads/{lead_id}/approve-outreach", json={"ai_output_id": draft["id"], "content_hash": draft["content_hash"]})
    exported = [json.loads(l) for l in client.get("/api/annotation/export").text.splitlines()]
    assert [r["source_output_id"] for r in exported] == [detail["source_output"]["id"]]
    assert client.get(f"/api/leads/{lead_id}").json()["status"] == "outreach_approved"


@pytest.mark.parametrize("timing, status", [
    ({"active_ms": 5000, "wall_ms": 4000, "hidden_ms": 0, "idle_ms": 0, "interaction_count": 3, "idle_threshold_ms": 60000}, 422),
    ({"active_ms": -1, "wall_ms": 4000, "hidden_ms": 0, "idle_ms": 0, "interaction_count": 3, "idle_threshold_ms": 60000}, 422),
    ({"active_ms": 0, "wall_ms": 4000, "hidden_ms": 4000, "idle_ms": 0, "interaction_count": 0, "idle_threshold_ms": 60000, "flags": ["was_hidden"]}, 201),
])
def test_timing_is_validated_and_incomplete_sessions_are_flagged(client, db_session, queue, timing, status):
    candidate = next(c for c in queue if c["task"] == "company_summary")
    detail = generate(client, candidate["id"]).json()
    response = submit(client, candidate["id"], detail, decision="accepted", timing=timing, **ASSESSED)
    assert response.status_code == status
    if status == 201:
        stored = response.json()["timing"]
        assert stored["incomplete"] is True and stored["source"] == "ui"
        assert stored["flags"] == ["was_hidden"]


def test_pilot_queue_candidates_never_come_from_validation_or_test(client, db_session, queue):
    lead_ids = {UUID(c["lead_id"]) for c in queue}
    held_out = set(db_session.scalars(
        select(CompanySplitAssignment.lead_id).where(CompanySplitAssignment.split != "train")))
    assert lead_ids.isdisjoint(held_out)
    assert db_session.scalar(select(func.count()).select_from(AnnotationCandidate)) == 6
