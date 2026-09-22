"""lead fit scores (Phase 4 Part D.1/D.2)

Additive only -- does not touch 0001-0005, and does not modify the existing
`lead_scores` table (the v1 scorer's single mutable row per lead stays
exactly as-is, still authoritative for every existing consumer). Adds
`lead_fit_scores`: a new, append-only, versioned table for the v2
deterministic company-fit scorer (app/scoring/fit.py). Deliberately NOT
unique on lead_id -- rescoring a lead inserts a new row rather than
overwriting the previous one, so historical results and their exact
scorer/profile/normalization versions remain queryable.

Revision ID: 0006_fit_scores
Revises: 0005_csv_retry
Create Date: 2026-09-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006_fit_scores'
down_revision: Union[str, Sequence[str], None] = '0005_csv_retry'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'lead_fit_scores',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('lead_id', sa.Uuid(), nullable=False),
        sa.Column('scorer_version', sa.String(length=64), nullable=False),
        sa.Column('profile_id', sa.String(length=64), nullable=False),
        sa.Column('profile_version', sa.String(length=32), nullable=False),
        sa.Column('normalization_version', sa.String(length=64), nullable=False),
        sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('fit_score', sa.Integer(), nullable=False),
        sa.Column('evidence_coverage_pct', sa.Float(), nullable=False),
        sa.Column('band', sa.String(length=32), nullable=False),
        sa.Column('criteria', sa.JSON(), nullable=False),
        sa.Column('readiness', sa.JSON(), nullable=False),
        sa.Column('eligibility_excluded', sa.Boolean(), nullable=False),
        sa.Column('eligibility_reasons', sa.JSON(), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('computation_ms', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_lead_fit_scores_lead_id'), 'lead_fit_scores', ['lead_id'], unique=False)
    op.create_index(op.f('ix_lead_fit_scores_scorer_version'), 'lead_fit_scores', ['scorer_version'], unique=False)
    op.create_index(op.f('ix_lead_fit_scores_profile_id'), 'lead_fit_scores', ['profile_id'], unique=False)
    op.create_index(op.f('ix_lead_fit_scores_profile_version'), 'lead_fit_scores', ['profile_version'], unique=False)
    op.create_index(op.f('ix_lead_fit_scores_input_fingerprint'), 'lead_fit_scores', ['input_fingerprint'], unique=False)
    op.create_index(op.f('ix_lead_fit_scores_fit_score'), 'lead_fit_scores', ['fit_score'], unique=False)
    op.create_index(op.f('ix_lead_fit_scores_band'), 'lead_fit_scores', ['band'], unique=False)
    op.create_index(op.f('ix_lead_fit_scores_created_at'), 'lead_fit_scores', ['created_at'], unique=False)
    # Bulk per-batch/per-page lookups filter by (lead_id, profile_id,
    # profile_version) and order by created_at desc -- this composite index
    # is what keeps "latest fit score per lead" queries from scanning the
    # whole table as it grows across rescoring runs.
    op.create_index(
        'ix_lead_fit_scores_lead_profile_created',
        'lead_fit_scores',
        ['lead_id', 'profile_id', 'profile_version', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_lead_fit_scores_lead_profile_created', table_name='lead_fit_scores')
    op.drop_index(op.f('ix_lead_fit_scores_created_at'), table_name='lead_fit_scores')
    op.drop_index(op.f('ix_lead_fit_scores_band'), table_name='lead_fit_scores')
    op.drop_index(op.f('ix_lead_fit_scores_fit_score'), table_name='lead_fit_scores')
    op.drop_index(op.f('ix_lead_fit_scores_input_fingerprint'), table_name='lead_fit_scores')
    op.drop_index(op.f('ix_lead_fit_scores_profile_version'), table_name='lead_fit_scores')
    op.drop_index(op.f('ix_lead_fit_scores_profile_id'), table_name='lead_fit_scores')
    op.drop_index(op.f('ix_lead_fit_scores_scorer_version'), table_name='lead_fit_scores')
    op.drop_index(op.f('ix_lead_fit_scores_lead_id'), table_name='lead_fit_scores')
    op.drop_table('lead_fit_scores')
