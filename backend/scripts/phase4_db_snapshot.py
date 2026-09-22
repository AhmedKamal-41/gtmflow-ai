"""Phase 4: read-only integrity snapshot of a database, printed as JSON.

Used before and after the real-cohort scoring run to prove that scoring
only ever adds `lead_fit_scores` rows: every other table's row count, the
leads' status distribution, a fingerprint over the source-derived columns
of leads / company identities / snapshots / batches, and the counts of
drafts, reviews, pushes and workflow events (which must not move).

Usage:
    DATABASE_URL=... python scripts/phase4_db_snapshot.py
"""
from __future__ import annotations

import hashlib
import json
import sys

from sqlalchemy import func, select, text

from app.core.database import get_sessionmaker
from app.models import (
    AIOutput,
    AIOutputReview,
    CompanyIdentity,
    ImportRun,
    IntegrationPush,
    Lead,
    LeadBatch,
    LeadFitScore,
    LeadScore,
    SourceSnapshot,
    WorkflowEvent,
)
from app.scoring import fit


def _digest(session, stmt) -> str:
    h = hashlib.sha256()
    for row in session.execute(stmt):
        h.update(json.dumps([str(v) for v in row], sort_keys=True).encode())
    return h.hexdigest()


def main() -> int:
    session = get_sessionmaker()()
    try:
        counts = {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (
                Lead, CompanyIdentity, SourceSnapshot, LeadBatch, ImportRun,
                LeadFitScore, LeadScore, AIOutput, AIOutputReview,
                IntegrationPush, WorkflowEvent,
            )
        }
        snapshot = {
            "alembic_version": session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one(),
            "row_counts": counts,
            "lead_status_counts": dict(
                session.execute(
                    select(Lead.status, func.count()).group_by(Lead.status).order_by(Lead.status)
                ).all()
            ),
            "batch_statuses": [
                [str(b.id), b.name, b.source, b.status, b.total_leads, b.processed_leads]
                for b in session.execute(select(LeadBatch).order_by(LeadBatch.id)).scalars()
            ],
            "distinct_leads_with_current_fit_score": session.scalar(
                select(func.count(func.distinct(LeadFitScore.lead_id))).where(
                    LeadFitScore.scorer_version == fit.SCORER_VERSION,
                    LeadFitScore.profile_id == fit.PROFILE_ID,
                    LeadFitScore.profile_version == fit.PROFILE_VERSION,
                    LeadFitScore.normalization_version == fit.NORMALIZATION_VERSION,
                )
            ),
            "digests": {
                "leads": _digest(session, select(
                    Lead.id, Lead.batch_id, Lead.company_name, Lead.website, Lead.industry,
                    Lead.company_size, Lead.location, Lead.status, Lead.cleaned_data,
                    Lead.source_record_id, Lead.company_identity_id, Lead.source_snapshot_id,
                ).order_by(Lead.id)),
                "company_identities": _digest(
                    session, select(CompanyIdentity).order_by(CompanyIdentity.id)
                    .with_only_columns(*CompanyIdentity.__table__.columns)
                ),
                "source_snapshots": _digest(
                    session, select(*SourceSnapshot.__table__.columns).order_by(SourceSnapshot.id)
                ),
                "lead_batches": _digest(
                    session, select(*LeadBatch.__table__.columns).order_by(LeadBatch.id)
                ),
                "import_runs": _digest(
                    session, select(*ImportRun.__table__.columns).order_by(ImportRun.id)
                ),
            },
        }
    finally:
        session.close()
    json.dump(snapshot, sys.stdout, indent=2, sort_keys=True, default=str)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
