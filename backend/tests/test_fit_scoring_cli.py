"""The bounded, reproducible, dry-run-capable CLI command that ranks
imported leads against the active seller profile (app/scoring/cli.py's
score_cohort). Tested against a disposable session factory, never the real
database.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session, sessionmaker

from app.models import Lead, LeadBatch, LeadFitScore, SellerProfile, SellerProfileActivation
from app.scoring.cli import main, score_cohort
from app.services.seller_profiles import profile_content_hash
from tests.conftest import SYNTHETIC_SELLER_PROFILE, US_SECTOR_PREFERENCES


def _activate_profile(db_session: Session) -> None:
    """An active seller revision targeting US healthcare and real estate,
    written directly (this CLI has no HTTP layer)."""
    content = {**SYNTHETIC_SELLER_PROFILE, "target_preferences": US_SECTOR_PREFERENCES}
    row = SellerProfile(id=uuid.uuid4(), version=1, profile=content,
                        content_hash=profile_content_hash(content), editor_label="test")
    db_session.add(row)
    db_session.flush()
    db_session.add(SellerProfileActivation(
        id=uuid.uuid4(), sequence=1, seller_profile_id=row.id, seller_profile_version=1,
        content_hash=row.content_hash, action="activate", reviewed_confirmation=True,
        demo_acknowledged=False, actor_label="test"))
    db_session.commit()


def _seed_leads(db_session: Session) -> LeadBatch:
    _activate_profile(db_session)
    batch = LeadBatch(name="cli-test", source="pdl", status="uploaded")
    db_session.add(batch)
    db_session.flush()

    def _lead(company_name: str, industry: str, country: str, segment: str) -> Lead:
        return Lead(
            id=uuid.uuid4(),
            batch_id=batch.id,
            company_name=company_name,
            industry=industry,
            cleaned_data={"country": country, "candidate_segment": segment},
            source="pdl_import",
            status="new",
        )

    db_session.add_all(
        [
            _lead("Strong Health Co", "Hospital & Health Care", "United States", "healthcare"),
            _lead("Strong Realty Co", "Real Estate", "United States", "real_estate"),
            _lead("Weak Health Co", "Retail", "United States", "healthcare"),
        ]
    )
    db_session.commit()
    return batch


def test_dry_run_computes_but_writes_nothing(
    db_session: Session, db_session_factory: sessionmaker[Session]
) -> None:
    _seed_leads(db_session)

    summary = score_cohort(db_session_factory, dry_run=True, chunk_size=2)

    assert summary["dry_run"] is True
    assert summary["total_leads_considered"] == 3
    assert summary["total_attempted"] == 3
    assert summary["total_succeeded"] == 3
    assert summary["total_failed"] == 0
    assert summary["attempted_by_segment"] == {"healthcare": 2, "real_estate": 1}

    rows = db_session.execute(
        __import__("sqlalchemy").select(LeadFitScore)
    ).scalars().all()
    assert rows == []  # dry run must not persist anything
    from sqlalchemy import func, select
    from app.models import WorkflowEvent
    assert db_session.scalar(select(func.count()).select_from(WorkflowEvent)) == 0


def test_real_run_persists_one_row_per_lead(
    db_session: Session, db_session_factory: sessionmaker[Session]
) -> None:
    _seed_leads(db_session)

    summary = score_cohort(db_session_factory, dry_run=False, chunk_size=2)

    assert summary["total_succeeded"] == 3
    rows = db_session.execute(
        __import__("sqlalchemy").select(LeadFitScore)
    ).scalars().all()
    assert len(rows) == 3

    assert summary["band_counts"].get("strong_match") == 2  # both full matches
    assert summary["fit_score_distribution"]["max"] == 100
    assert summary["fit_score_distribution"]["min"] == 36  # Weak Health Co: location only (20 of 55)


def test_default_rerun_skips_unchanged_leads_and_writes_nothing(
    db_session: Session, db_session_factory: sessionmaker[Session]
) -> None:
    """Rerunning after a successful run finds every lead's latest row
    already has the identical input fingerprint -- nothing is written, and
    every lead is reported as skipped (not silently dropped)."""
    _seed_leads(db_session)

    score_cohort(db_session_factory, dry_run=False, chunk_size=2)
    second = score_cohort(db_session_factory, dry_run=False, chunk_size=2)

    assert second["total_attempted"] == 3
    assert second["total_succeeded"] == 0
    assert second["total_skipped_unchanged"] == 3
    assert second["total_failed"] == 0
    import sqlalchemy as sa

    assert len(db_session.execute(sa.select(LeadFitScore)).scalars().all()) == 3


def test_rescore_unchanged_writes_bounded_second_generation(
    db_session: Session, db_session_factory: sessionmaker[Session]
) -> None:
    """--rescore-unchanged inserts a SECOND generation of rows (versioned
    history, Part D.1) -- 3 leads x 2 runs = 6 rows, never more, and each
    lead has exactly 2 historical rows."""
    _seed_leads(db_session)

    score_cohort(db_session_factory, dry_run=False, chunk_size=2)
    score_cohort(db_session_factory, dry_run=False, chunk_size=2, rescore_unchanged=True)

    import sqlalchemy as sa

    rows = db_session.execute(sa.select(LeadFitScore)).scalars().all()
    assert len(rows) == 6
    counts_per_lead: dict = {}
    for row in rows:
        counts_per_lead[row.lead_id] = counts_per_lead.get(row.lead_id, 0) + 1
    assert set(counts_per_lead.values()) == {2}


def test_changed_inputs_are_rescored_even_by_default(
    db_session: Session, db_session_factory: sessionmaker[Session]
) -> None:
    _seed_leads(db_session)
    score_cohort(db_session_factory, dry_run=False, chunk_size=2)

    import sqlalchemy as sa

    weak = db_session.execute(
        sa.select(Lead).where(Lead.company_name == "Weak Health Co")
    ).scalar_one()
    weak.industry = "Hospital & Health Care"
    db_session.commit()

    second = score_cohort(db_session_factory, dry_run=False, chunk_size=2)
    assert second["total_succeeded"] == 1
    assert second["total_skipped_unchanged"] == 2
    assert second["band_counts"] == {"strong_match": 1}


def test_one_failing_lead_does_not_sink_its_chunk(
    db_session: Session, db_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    _seed_leads(db_session)
    from app.scoring import fit as fit_module

    real = fit_module.compute_fit

    def flaky(lead, profile):
        if lead.company_name == "Strong Realty Co":
            raise ValueError("bad input")
        return real(lead, profile)

    monkeypatch.setattr(fit_module, "compute_fit", flaky)
    summary = score_cohort(db_session_factory, dry_run=False, chunk_size=10)

    assert summary["total_attempted"] == 3
    assert summary["total_succeeded"] == 2
    assert summary["failed_by_segment"] == {"real_estate": 1}
    assert summary["errors"][0]["error"] == "bad input"
    import sqlalchemy as sa

    assert len(db_session.execute(sa.select(LeadFitScore)).scalars().all()) == 2


def test_limit_bounds_how_many_leads_are_considered(
    db_session: Session, db_session_factory: sessionmaker[Session]
) -> None:
    _seed_leads(db_session)
    summary = score_cohort(db_session_factory, dry_run=True, chunk_size=2, limit=1)
    assert summary["total_leads_considered"] == 1
    assert summary["total_attempted"] == 1


def test_commit_failure_rolls_back_the_whole_chunk_and_retry_completes(
    db_session: Session, db_session_factory: sessionmaker[Session]
) -> None:
    from sqlalchemy import func, select
    from app.models import WorkflowEvent

    _seed_leads(db_session)
    calls = 0

    def flaky_factory():
        nonlocal calls
        calls += 1
        session = db_session_factory()
        if calls == 2:  # after the read-only cohort lookup
            def fail():
                raise RuntimeError("chunk commit failed")
            session.commit = fail
        return session

    run = score_cohort(flaky_factory, dry_run=False, chunk_size=2)
    assert (run["total_attempted"], run["total_succeeded"], run["total_failed"]) == (3, 1, 2)
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(LeadFitScore)) == 1
    assert db_session.scalar(select(func.count()).select_from(WorkflowEvent)) == 1
    retry = score_cohort(db_session_factory, dry_run=False, chunk_size=2)
    assert (retry["total_succeeded"], retry["total_skipped_unchanged"], retry["total_failed"]) == (2, 1, 0)


def test_a_failed_audit_write_rolls_back_its_score_without_losing_other_leads(
    db_session: Session, db_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    from sqlalchemy import func, select
    from app.models import WorkflowEvent

    _seed_leads(db_session)
    failing_id = db_session.scalar(select(Lead.id).where(Lead.company_name == "Strong Realty Co"))
    real_flush = Session.flush

    def fail_audit(session, *args, **kwargs):
        for row in session.new:
            if isinstance(row, WorkflowEvent) and row.lead_id == failing_id:
                raise RuntimeError("audit write failed")
        return real_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", fail_audit)
    run = score_cohort(db_session_factory, dry_run=False, chunk_size=3)
    assert (run["total_succeeded"], run["total_failed"]) == (2, 1)
    assert run["failed_by_segment"] == {"real_estate": 1}
    assert db_session.scalar(select(func.count()).select_from(LeadFitScore)) == 2
    assert db_session.scalar(select(func.count()).select_from(WorkflowEvent)) == 2
    assert db_session.scalar(select(LeadFitScore.id).where(LeadFitScore.lead_id == failing_id)) is None


def test_without_an_active_target_profile_the_cli_refuses_and_writes_nothing(
    db_session: Session, db_session_factory: sessionmaker[Session], monkeypatch, capsys
) -> None:
    from app.scoring import cli

    batch = LeadBatch(name="cli-test", source="pdl", status="uploaded")
    db_session.add(batch)
    db_session.flush()
    db_session.add(Lead(id=uuid.uuid4(), batch_id=batch.id, company_name="Any Co",
                        industry="Hospital & Health Care", source="pdl_import", status="new"))
    db_session.commit()
    monkeypatch.setattr(cli, "get_sessionmaker", lambda: db_session_factory)
    assert main([]) == 2
    assert "ranking_setup_required" in capsys.readouterr().out
    import sqlalchemy as sa

    assert db_session.execute(sa.select(LeadFitScore)).scalars().all() == []
