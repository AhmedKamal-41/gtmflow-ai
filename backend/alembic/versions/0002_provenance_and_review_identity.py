"""provenance, company identity, and output/review identity

Phase 2 Parts C + D of the GTMFlow upgrade (see docs/engineering-log/decisions.md).

Adds, additively, on top of the 0001 baseline:

  - source_snapshots, import_runs, company_identities: the smallest schema
    needed for Phase 3's PDL import to record where data came from and
    resolve canonical companies, without merging or backdating anything
    existing. All new leads.* FK columns are nullable and untouched by the
    CSV-upload/demo paths, so synthetic data never carries PDL provenance.
  - ai_outputs gains identity/provenance columns (parent_output_id, origin,
    input_snapshot, input_hash, output_schema_version, model_revision,
    adapter_revision). `origin` backfills to 'generated' for every existing
    row via a column server_default -- a true structural fact (no edit
    capability existed before this migration), not a guess. The other new
    columns stay NULL for existing rows; we cannot honestly reconstruct what
    input produced a historical generation.
  - ai_output_reviews is new and is backfilled from existing WorkflowEvent
    rows of type outreach_approved/outreach_rejected (see
    _backfill_ai_output_reviews_from_workflow_events below). A legacy event
    is linked to a specific AIOutput only when its stored ai_output_id
    resolves to a real row belonging to the same lead -- i.e. the link is
    *proven* from data the app itself wrote at approval/reject time, never
    guessed via "latest output" or timestamp proximity. When it can't be
    proven, the row is kept with ai_output_id=NULL and legacy_unlinked=True.

Revision ID: 0002_provenance
Revises: 0001_baseline
Create Date: 2026-09-22

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


# revision identifiers, used by Alembic.
revision: str = '0002_provenance'
down_revision: Union[str, Sequence[str], None] = '0001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_REVIEWER_LABEL = "legacy-migrated-unknown-reviewer"
DECISION_BY_LEGACY_EVENT_TYPE = {
    "outreach_approved": "approved",
    "outreach_rejected": "rejected",
}


def _backfill_ai_output_reviews_from_workflow_events(bind: Connection) -> None:
    """Populate ai_output_reviews from pre-existing approve/reject
    WorkflowEvent rows. See the module docstring for the linking rule.
    """
    workflow_events = sa.table(
        "workflow_events",
        sa.column("id", sa.Uuid()),
        sa.column("lead_id", sa.Uuid()),
        sa.column("event_type", sa.String()),
        sa.column("event_data", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    ai_outputs = sa.table(
        "ai_outputs",
        sa.column("id", sa.Uuid()),
        sa.column("lead_id", sa.Uuid()),
    )
    ai_output_reviews = sa.table(
        "ai_output_reviews",
        sa.column("id", sa.Uuid()),
        sa.column("lead_id", sa.Uuid()),
        sa.column("ai_output_id", sa.Uuid()),
        sa.column("decision", sa.String()),
        sa.column("reason", sa.Text()),
        sa.column("review_kind", sa.String()),
        sa.column("reviewer_label", sa.String()),
        sa.column("legacy_unlinked", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    events = bind.execute(
        sa.select(
            workflow_events.c.lead_id,
            workflow_events.c.event_type,
            workflow_events.c.event_data,
            workflow_events.c.created_at,
        ).where(
            workflow_events.c.event_type.in_(list(DECISION_BY_LEGACY_EVENT_TYPE))
        )
    ).fetchall()
    if not events:
        return

    # Proof set: real (lead_id, ai_output_id) pairs that actually exist.
    # A legacy event's stored ai_output_id is only trusted if it resolves
    # here -- never inferred from "latest" or timestamp proximity.
    existing_pairs = {
        (row.lead_id, row.id)
        for row in bind.execute(
            sa.select(ai_outputs.c.id, ai_outputs.c.lead_id)
        ).fetchall()
    }

    rows_to_insert = []
    for event in events:
        decision = DECISION_BY_LEGACY_EVENT_TYPE[event.event_type]
        event_data = event.event_data if isinstance(event.event_data, dict) else {}
        raw_output_id = event_data.get("ai_output_id")
        reason = event_data.get("reason")

        linked_output_id = None
        legacy_unlinked = True
        if raw_output_id:
            try:
                candidate = uuid.UUID(str(raw_output_id))
            except (ValueError, TypeError, AttributeError):
                candidate = None
            if candidate is not None and (event.lead_id, candidate) in existing_pairs:
                linked_output_id = candidate
                legacy_unlinked = False

        rows_to_insert.append(
            {
                "id": uuid.uuid4(),
                "lead_id": event.lead_id,
                "ai_output_id": linked_output_id,
                "decision": decision,
                "reason": reason if isinstance(reason, str) else None,
                "review_kind": "operational_outreach",
                "reviewer_label": LEGACY_REVIEWER_LABEL,
                "legacy_unlinked": legacy_unlinked,
                "created_at": event.created_at,
            }
        )

    bind.execute(sa.insert(ai_output_reviews), rows_to_insert)


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('source_snapshots',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('mirror_url', sa.String(length=1024), nullable=False),
    sa.Column('source_revision', sa.String(length=128), nullable=True),
    sa.Column('reported_acquisition_date', sa.Date(), nullable=True),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('retrieved_license', sa.String(length=128), nullable=True),
    sa.Column('retrieved_attribution', sa.Text(), nullable=True),
    sa.Column('checksum', sa.String(length=128), nullable=True),
    sa.Column('checksum_algorithm', sa.String(length=32), nullable=True),
    sa.Column('parser_version', sa.String(length=32), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_snapshots'))
    )
    op.create_index(op.f('ix_source_snapshots_provider'), 'source_snapshots', ['provider'], unique=False)
    op.create_table('company_identities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('canonical_name', sa.String(length=255), nullable=False),
    sa.Column('website_domain', sa.String(length=255), nullable=True),
    sa.Column('source_snapshot_id', sa.Uuid(), nullable=True),
    sa.Column('source_record_id', sa.String(length=128), nullable=True),
    sa.Column('source_raw_identity', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_snapshot_id'], ['source_snapshots.id'], name=op.f('fk_company_identities_source_snapshot_id_source_snapshots')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_company_identities'))
    )
    op.create_index(op.f('ix_company_identities_canonical_name'), 'company_identities', ['canonical_name'], unique=False)
    op.create_index(op.f('ix_company_identities_source_record_id'), 'company_identities', ['source_record_id'], unique=False)
    op.create_index(op.f('ix_company_identities_source_snapshot_id'), 'company_identities', ['source_snapshot_id'], unique=False)
    op.create_index(op.f('ix_company_identities_website_domain'), 'company_identities', ['website_domain'], unique=False)
    op.create_table('import_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('source_snapshot_id', sa.Uuid(), nullable=False),
    sa.Column('config_seed', sa.JSON(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('total_records', sa.Integer(), nullable=True),
    sa.Column('imported_count', sa.Integer(), nullable=True),
    sa.Column('skipped_count', sa.Integer(), nullable=True),
    sa.Column('error_count', sa.Integer(), nullable=True),
    sa.Column('error_summary', sa.JSON(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_snapshot_id'], ['source_snapshots.id'], name=op.f('fk_import_runs_source_snapshot_id_source_snapshots')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_runs'))
    )
    op.create_index(op.f('ix_import_runs_source_snapshot_id'), 'import_runs', ['source_snapshot_id'], unique=False)
    op.create_index(op.f('ix_import_runs_status'), 'import_runs', ['status'], unique=False)
    op.create_table('ai_output_reviews',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('lead_id', sa.Uuid(), nullable=False),
    sa.Column('ai_output_id', sa.Uuid(), nullable=True),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('review_kind', sa.String(length=32), nullable=False),
    sa.Column('reviewer_label', sa.String(length=64), nullable=False),
    sa.Column('legacy_unlinked', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['ai_output_id'], ['ai_outputs.id'], name=op.f('fk_ai_output_reviews_ai_output_id_ai_outputs')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_ai_output_reviews_lead_id_leads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_output_reviews'))
    )
    op.create_index(op.f('ix_ai_output_reviews_ai_output_id'), 'ai_output_reviews', ['ai_output_id'], unique=False)
    op.create_index(op.f('ix_ai_output_reviews_created_at'), 'ai_output_reviews', ['created_at'], unique=False)
    op.create_index(op.f('ix_ai_output_reviews_decision'), 'ai_output_reviews', ['decision'], unique=False)
    op.create_index(op.f('ix_ai_output_reviews_lead_id'), 'ai_output_reviews', ['lead_id'], unique=False)
    op.create_index(op.f('ix_ai_output_reviews_review_kind'), 'ai_output_reviews', ['review_kind'], unique=False)
    op.add_column('ai_outputs', sa.Column('parent_output_id', sa.Uuid(), nullable=True))
    # server_default backfills every pre-existing row to 'generated' at ALTER
    # time -- a true structural fact (no edit capability existed before this
    # migration), not a guessed value. New rows always set it explicitly at
    # the ORM layer too; the default just protects any writer that doesn't.
    op.add_column(
        'ai_outputs',
        sa.Column(
            'origin', sa.String(length=32), nullable=False, server_default='generated'
        ),
    )
    op.add_column('ai_outputs', sa.Column('input_snapshot', sa.JSON(), nullable=True))
    op.add_column('ai_outputs', sa.Column('input_hash', sa.String(length=64), nullable=True))
    op.add_column('ai_outputs', sa.Column('output_schema_version', sa.String(length=16), nullable=True))
    op.add_column('ai_outputs', sa.Column('model_revision', sa.String(length=128), nullable=True))
    op.add_column('ai_outputs', sa.Column('adapter_revision', sa.String(length=128), nullable=True))
    op.create_index(op.f('ix_ai_outputs_parent_output_id'), 'ai_outputs', ['parent_output_id'], unique=False)
    op.create_foreign_key(op.f('fk_ai_outputs_parent_output_id_ai_outputs'), 'ai_outputs', 'ai_outputs', ['parent_output_id'], ['id'])
    op.add_column('leads', sa.Column('company_identity_id', sa.Uuid(), nullable=True))
    op.add_column('leads', sa.Column('source_snapshot_id', sa.Uuid(), nullable=True))
    op.add_column('leads', sa.Column('import_run_id', sa.Uuid(), nullable=True))
    op.add_column('leads', sa.Column('source_record_id', sa.String(length=128), nullable=True))
    op.add_column('leads', sa.Column('source_raw_data', sa.JSON(), nullable=True))
    op.create_index(op.f('ix_leads_company_identity_id'), 'leads', ['company_identity_id'], unique=False)
    op.create_index(op.f('ix_leads_import_run_id'), 'leads', ['import_run_id'], unique=False)
    op.create_index(op.f('ix_leads_source_snapshot_id'), 'leads', ['source_snapshot_id'], unique=False)
    op.create_foreign_key(op.f('fk_leads_import_run_id_import_runs'), 'leads', 'import_runs', ['import_run_id'], ['id'])
    op.create_foreign_key(op.f('fk_leads_company_identity_id_company_identities'), 'leads', 'company_identities', ['company_identity_id'], ['id'])
    op.create_foreign_key(op.f('fk_leads_source_snapshot_id_source_snapshots'), 'leads', 'source_snapshots', ['source_snapshot_id'], ['id'])
    # ### end Alembic commands ###

    # Data migration (not autogenerated): backfill ai_output_reviews from
    # existing approve/reject WorkflowEvent rows. Must run after
    # ai_output_reviews exists, which it now does.
    _backfill_ai_output_reviews_from_workflow_events(op.get_bind())


def downgrade() -> None:
    """Downgrade schema.

    HONEST WARNING -- this downgrade is destructive and lossy, not just a
    schema rollback:

      * Every ai_output_reviews row is dropped, including rows backfilled
        from history AND any real reviews recorded after this migration
        ran. There is no way back to the pre-migration WorkflowEvent-only
        state without also having a separate backup of this table.
      * All provenance/identity data written into the new leads.* and
        ai_outputs.* columns (company_identity_id, source_snapshot_id,
        import_run_id, source_record_id, source_raw_data, parent_output_id,
        input_snapshot, input_hash, output_schema_version, model_revision,
        adapter_revision) is dropped and unrecoverable from this database.
      * source_snapshots, import_runs, and company_identities are dropped
        entirely.

    Do not run this against a database that has accumulated real Phase 3+
    data without taking a full backup first (see docs/engineering-log/audit.md's
    Phase 2 handoff for the backup/recovery procedure). This downgrade was
    never run against a populated database during Phase 2 development --
    only against disposable, empty-of-new-data verification databases.
    """
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(op.f('fk_leads_source_snapshot_id_source_snapshots'), 'leads', type_='foreignkey')
    op.drop_constraint(op.f('fk_leads_company_identity_id_company_identities'), 'leads', type_='foreignkey')
    op.drop_constraint(op.f('fk_leads_import_run_id_import_runs'), 'leads', type_='foreignkey')
    op.drop_index(op.f('ix_leads_source_snapshot_id'), table_name='leads')
    op.drop_index(op.f('ix_leads_import_run_id'), table_name='leads')
    op.drop_index(op.f('ix_leads_company_identity_id'), table_name='leads')
    op.drop_column('leads', 'source_raw_data')
    op.drop_column('leads', 'source_record_id')
    op.drop_column('leads', 'import_run_id')
    op.drop_column('leads', 'source_snapshot_id')
    op.drop_column('leads', 'company_identity_id')
    op.drop_constraint(op.f('fk_ai_outputs_parent_output_id_ai_outputs'), 'ai_outputs', type_='foreignkey')
    op.drop_index(op.f('ix_ai_outputs_parent_output_id'), table_name='ai_outputs')
    op.drop_column('ai_outputs', 'adapter_revision')
    op.drop_column('ai_outputs', 'model_revision')
    op.drop_column('ai_outputs', 'output_schema_version')
    op.drop_column('ai_outputs', 'input_hash')
    op.drop_column('ai_outputs', 'input_snapshot')
    op.drop_column('ai_outputs', 'origin')
    op.drop_column('ai_outputs', 'parent_output_id')
    op.drop_index(op.f('ix_ai_output_reviews_review_kind'), table_name='ai_output_reviews')
    op.drop_index(op.f('ix_ai_output_reviews_lead_id'), table_name='ai_output_reviews')
    op.drop_index(op.f('ix_ai_output_reviews_decision'), table_name='ai_output_reviews')
    op.drop_index(op.f('ix_ai_output_reviews_created_at'), table_name='ai_output_reviews')
    op.drop_index(op.f('ix_ai_output_reviews_ai_output_id'), table_name='ai_output_reviews')
    op.drop_table('ai_output_reviews')
    op.drop_index(op.f('ix_import_runs_status'), table_name='import_runs')
    op.drop_index(op.f('ix_import_runs_source_snapshot_id'), table_name='import_runs')
    op.drop_table('import_runs')
    op.drop_index(op.f('ix_company_identities_website_domain'), table_name='company_identities')
    op.drop_index(op.f('ix_company_identities_source_snapshot_id'), table_name='company_identities')
    op.drop_index(op.f('ix_company_identities_source_record_id'), table_name='company_identities')
    op.drop_index(op.f('ix_company_identities_canonical_name'), table_name='company_identities')
    op.drop_table('company_identities')
    op.drop_index(op.f('ix_source_snapshots_provider'), table_name='source_snapshots')
    op.drop_table('source_snapshots')
    # ### end Alembic commands ###
