"""Phase 3 closeout Part E: reimporting the same PDL selection must not
reset a lead's blocked status, its AI outputs, or its reviews -- the
importer only ever inserts NEW leads for identities it hasn't seen before;
an existing lead (and everything hanging off it) is left completely alone.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.models import AIOutput, AIOutputReview, Lead
from app.pdl.importer import (
    find_or_create_batch,
    find_or_create_source_snapshot,
    import_companies,
)
from app.pdl.normalize import NormalizedCompany
from app.scoring.lead_scoring import DISQUALIFIED_STATUSES


def _company(i: int, segment: str = "healthcare") -> NormalizedCompany:
    return NormalizedCompany(
        company_name=f"Reimport Co {i}",
        website=None,
        domain=None,
        raw_industry="medical practice",
        normalized_industry=segment,
        candidate_segment=segment,
        company_size="1-10",
        locality="austin",
        region="texas",
        country_raw="united states",
        country_normalized="united states",
        location="austin, texas, united states",
        source_record_id=f"reimport-id-{i}",
        founded=None,
        linkedin_url=None,
        source_raw_data={"id": f"reimport-id-{i}"},
    )


def test_reimport_preserves_blocked_status_and_reviews_and_outputs(
    db_session_factory,
) -> None:
    session_factory = db_session_factory
    session = session_factory()

    snapshot, _ = find_or_create_source_snapshot(
        session,
        provider="reimport_test",
        mirror_url="https://example.com/x.json.gz",
        source_revision=None,
        reported_acquisition_date=date(2025, 1, 1),
        retrieved_license=None,
        retrieved_attribution=None,
        checksum="9" * 64,
        checksum_algorithm="sha256",
        parser_version="v1",
    )
    session.commit()
    session.refresh(snapshot)
    batch, _ = find_or_create_batch(session, logical_key="reimport-workflow-test")
    session.commit()
    session.refresh(batch)
    source_snapshot_id, batch_id = snapshot.id, batch.id
    session.close()

    companies = {"healthcare": [_company(0), _company(1)]}

    # --- first import: 2 leads land ---
    from app.models import ImportRun

    session = session_factory()
    run1 = ImportRun(source_snapshot_id=source_snapshot_id, status="running", started_at=datetime.now(timezone.utc))
    session.add(run1)
    session.commit()
    session.refresh(run1)
    run1_id = run1.id
    session.close()

    import_companies(
        session_factory,
        companies_by_segment=companies,
        source_snapshot_id=source_snapshot_id,
        batch_id=batch_id,
        import_run_id=run1_id,
    )

    # --- simulate real workflow state accruing on one of the leads ---
    session = session_factory()
    lead = session.query(Lead).filter(Lead.company_name == "Reimport Co 0").one()
    lead.status = "do_not_contact"  # blocked disposition, set by hand (e.g. a human marked it)
    output = AIOutput(
        lead_id=lead.id,
        output_type="outreach_email",
        content={"subject": "hello", "email_body": "hi"},
        model_used="mock",
        prompt_version="v1",
    )
    session.add(output)
    session.flush()
    review = AIOutputReview(
        lead_id=lead.id,
        ai_output_id=output.id,
        decision="rejected",
        reviewer_label="local-demo-unauthenticated",
        review_kind="operational_outreach",
        legacy_unlinked=False,
    )
    session.add(review)
    session.commit()
    lead_id, output_id, review_id = lead.id, output.id, review.id
    session.close()

    # --- second import: same manifest/companies, must reuse both leads,
    # touching NEITHER lead's status, outputs, nor reviews ---
    run2 = ImportRun(source_snapshot_id=source_snapshot_id, status="running", started_at=datetime.now(timezone.utc))
    session = session_factory()
    session.add(run2)
    session.commit()
    session.refresh(run2)
    run2_id = run2.id
    session.close()

    summary = import_companies(
        session_factory,
        companies_by_segment=companies,
        source_snapshot_id=source_snapshot_id,
        batch_id=batch_id,
        import_run_id=run2_id,
    )
    assert summary.inserted == 0
    assert summary.reused == 2

    session = session_factory()
    lead_after = session.get(Lead, lead_id)
    assert lead_after.status == "do_not_contact"  # untouched by reimport
    assert lead_after.status in DISQUALIFIED_STATUSES

    outputs_after = session.query(AIOutput).filter(AIOutput.lead_id == lead_id).all()
    assert len(outputs_after) == 1
    assert outputs_after[0].id == output_id  # same row, not duplicated or replaced

    reviews_after = session.query(AIOutputReview).filter(AIOutputReview.lead_id == lead_id).all()
    assert len(reviews_after) == 1
    assert reviews_after[0].id == review_id
    assert reviews_after[0].decision == "rejected"

    total_leads = session.query(Lead).filter(Lead.batch_id == batch_id).count()
    assert total_leads == 2  # still exactly 2, not 4
    session.close()
