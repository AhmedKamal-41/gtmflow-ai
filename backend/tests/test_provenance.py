"""Phase 2 Part C/D: provenance and output-identity round-trips.

These are ORM-level tests against the live SQLAlchemy models (SQLite in
this suite). They do not exercise the Alembic migrations themselves -- that
verification requires a disposable Postgres database and is documented
separately in docs/upgrade/audit.md's Phase 2 handoff, since SQLite cannot
establish PostgreSQL migration correctness.
"""
from __future__ import annotations

import hashlib
import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import CompanyIdentity, ImportRun, Lead, SourceSnapshot

CSV = (
    "company_name,industry,contact_title,contact_name,contact_email,website,company_size,source,notes\n"
    "Cascade Modular,Housing,VP Operations,Sarah Chen,sarah@cascade.com,cascade.com,240,referral,tenant maintenance leasing scheduling pain\n"
)


def _upload_and_score(client: TestClient) -> dict:
    files = {"file": ("leads.csv", CSV.encode("utf-8"), "text/csv")}
    up = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "provenance-test"}
    ).json()
    client.post(f"/api/batches/{up['batch_id']}/score")
    return client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"][0]


# --------------------------------------------------------------------------
# Part E.6: company + provenance records round-trip through the ORM
# --------------------------------------------------------------------------


def test_source_snapshot_import_run_company_identity_round_trip(
    db_session: Session,
) -> None:
    snapshot = SourceSnapshot(
        provider="people_data_labs",
        mirror_url="https://huggingface.co/datasets/andreaaltomani/company-dataset",
        source_revision=None,
        reported_acquisition_date=None,
        retrieved_license="CC-BY-4.0",
        retrieved_attribution="People Data Labs, mirrored via Hugging Face",
        checksum=None,  # not computed in this test -- must stay NULL, not guessed
        parser_version="phase3-v0",
    )
    db_session.add(snapshot)
    db_session.flush()

    run = ImportRun(
        source_snapshot_id=snapshot.id,
        config_seed={"target_country": "US", "limit": 5000},
        status="completed",
        total_records=5000,
        imported_count=4980,
        skipped_count=20,
        error_count=0,
        error_summary=[],
    )
    db_session.add(run)

    identity = CompanyIdentity(
        canonical_name="Acme Property Group",
        website_domain="acmeproperty.com",
        source_snapshot_id=snapshot.id,
        source_record_id="pdl-abc123",
        source_raw_identity={
            "name": "Acme Property Group",
            "website": "acmeproperty.com",
            "size": "51-200",
            "locality": "Denver",
            "region": "Colorado",
            "country": "United States",
        },
    )
    db_session.add(identity)
    db_session.commit()

    db_session.refresh(snapshot)
    db_session.refresh(run)
    db_session.refresh(identity)

    assert run.source_snapshot_id == snapshot.id
    assert run.source_snapshot.provider == "people_data_labs"
    assert identity.source_snapshot_id == snapshot.id
    assert identity.source_raw_identity["locality"] == "Denver"
    assert snapshot.checksum is None  # never guessed
    assert len(snapshot.import_runs) == 1
    assert len(snapshot.company_identities) == 1


def test_lead_provenance_fields_are_null_for_csv_upload(
    client: TestClient,
) -> None:
    """Invariant: CSV-upload leads never carry PDL provenance (Part C)."""
    lead = _upload_and_score(client)
    assert lead["company_identity_id"] is None
    assert lead["source_snapshot_id"] is None
    assert lead["import_run_id"] is None
    assert lead["source_record_id"] is None
    assert lead["source_raw_data"] is None


def test_lead_can_link_to_company_identity_and_source_snapshot(
    db_session: Session, client: TestClient
) -> None:
    """Schema hook round-trip: a Lead CAN point at a CompanyIdentity +
    SourceSnapshot once something (Phase 3) resolves it. Phase 2 does not
    perform this resolution itself -- this only proves the plumbing works."""
    lead_api = _upload_and_score(client)
    lead = db_session.get(Lead, UUID(lead_api["id"]))
    assert lead is not None

    snapshot = SourceSnapshot(
        provider="people_data_labs",
        mirror_url="https://huggingface.co/datasets/andreaaltomani/company-dataset",
    )
    db_session.add(snapshot)
    db_session.flush()
    identity = CompanyIdentity(canonical_name="Cascade Modular", source_snapshot_id=snapshot.id)
    db_session.add(identity)
    db_session.flush()

    lead.company_identity_id = identity.id
    lead.source_snapshot_id = snapshot.id
    lead.source_record_id = "pdl-xyz"
    lead.source_raw_data = {"name": "Cascade Modular Homes Inc"}
    db_session.commit()
    db_session.refresh(lead)

    assert lead.company_identity.canonical_name == "Cascade Modular"
    assert lead.source_snapshot.provider == "people_data_labs"
    assert identity.leads[0].id == lead.id


# --------------------------------------------------------------------------
# Part E.7: new generation records store actual input/provider metadata
# --------------------------------------------------------------------------


@pytest.mark.usefixtures("active_seller_profile")
def test_generated_output_stores_real_input_snapshot_and_hash(
    client: TestClient,
) -> None:
    lead = _upload_and_score(client)
    output = client.post(f"/api/leads/{lead['id']}/generate-outreach").json()

    assert output["origin"] == "generated"
    assert output["model_revision"] == "mock-deterministic-v2-grounded"
    assert output["prompt_version"] == "grounded-v2"
    assert output["output_schema_version"] == "v2"
    assert output["parent_output_id"] is None
    assert output["adapter_revision"] is None  # no adapters exist yet (Phase 8)

    snapshot = output["input_snapshot"]
    assert snapshot is not None
    facts = {fact["field"]: fact["value"] for fact in snapshot["lead_facts"]}
    assert facts["company_name"] == "Cascade Modular"
    assert facts["industry"] == "Housing"
    assert snapshot["lead_provenance"]["lead_id"] == lead["id"]

    recomputed = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    assert output["input_hash"] == recomputed


@pytest.mark.usefixtures("active_seller_profile")
def test_generated_summary_and_outreach_have_independent_input_snapshots(
    client: TestClient,
) -> None:
    """Each output's snapshot reflects what was *actually* sent for that
    specific call (the task differs), not a shared/reused blob. Neither
    contains previously generated content."""
    lead = _upload_and_score(client)
    summary = client.post(f"/api/leads/{lead['id']}/generate-summary").json()
    outreach = client.post(f"/api/leads/{lead['id']}/generate-outreach").json()

    assert summary["input_snapshot"]["task"] == "company_summary"
    assert outreach["input_snapshot"]["task"] == "outreach_email"
    assert "latest_summary" not in outreach["input_snapshot"]
    assert outreach["input_hash"] != summary["input_hash"]


# --------------------------------------------------------------------------
# Part E.8 (ORM-level slice): unpopulated provenance stays NULL, is never
# defaulted to a guessed value. The full historical-backfill behavior is
# exercised by the Alembic migration itself against a disposable database
# (see the Phase 2 handoff for that run's command + output).
# --------------------------------------------------------------------------


def test_ai_output_provenance_columns_default_to_null_not_guessed(
    db_session: Session, client: TestClient
) -> None:
    from app.models import AIOutput

    lead = _upload_and_score(client)
    # Constructed the way pre-Phase-2 code would have -- no provenance kwargs.
    output = AIOutput(
        lead_id=UUID(lead["id"]),
        output_type="outreach_email",
        content={"subject": "x", "email_body": "y"},
        model_used="mock",
        prompt_version="v1",
    )
    db_session.add(output)
    db_session.commit()
    db_session.refresh(output)

    assert output.origin == "generated"  # true structural default, not a guess
    assert output.input_snapshot is None
    assert output.input_hash is None
    assert output.output_schema_version is None
    assert output.model_revision is None
    assert output.adapter_revision is None
    assert output.parent_output_id is None
