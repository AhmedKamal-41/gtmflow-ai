"""Phase 4 Part F: the bounded, reproducible, dry-run-capable CLI command
that scores the real cohort (app/scoring/cli.py's score_cohort). Tested
against a disposable session factory, never the real database.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session, sessionmaker

from app.models import Lead, LeadBatch, LeadFitScore
from app.scoring.cli import score_cohort


def _seed_leads(db_session: Session) -> LeadBatch:
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
    assert summary["fit_score_distribution"]["min"] == 40  # Weak Health Co: country only


def test_rerun_is_bounded_and_does_not_duplicate_beyond_expected_rows(
    db_session: Session, db_session_factory: sessionmaker[Session]
) -> None:
    """Rescoring the same cohort again inserts a SECOND generation of rows
    (versioned history, Part D.1) -- 3 leads x 2 runs = 6 rows, never more,
    and each lead still has exactly 2 historical rows."""
    _seed_leads(db_session)

    score_cohort(db_session_factory, dry_run=False, chunk_size=2)
    score_cohort(db_session_factory, dry_run=False, chunk_size=2)

    import sqlalchemy as sa

    rows = db_session.execute(sa.select(LeadFitScore)).scalars().all()
    assert len(rows) == 6
    counts_per_lead: dict = {}
    for row in rows:
        counts_per_lead[row.lead_id] = counts_per_lead.get(row.lead_id, 0) + 1
    assert set(counts_per_lead.values()) == {2}


def test_limit_bounds_how_many_leads_are_considered(
    db_session: Session, db_session_factory: sessionmaker[Session]
) -> None:
    _seed_leads(db_session)
    summary = score_cohort(db_session_factory, dry_run=True, chunk_size=2, limit=1)
    assert summary["total_leads_considered"] == 1
    assert summary["total_attempted"] == 1
