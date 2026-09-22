"""logical key uniqueness and source snapshot checksum uniqueness

Phase 3 closeout (see docs/upgrade/decisions.md). Additive only -- does not
touch 0001/0002/0003.

  - lead_batches.logical_key: persisted, full logical-selection key,
    replacing the previous name-prefix (first 12 chars, embedded in the
    batch name) lookup. Batch names stay freely editable.
  - uq_lead_batches_logical_key, uq_source_snapshots_provider_checksum: the
    actual convergence/idempotency guarantees under concurrent writes --
    see the two model docstrings (app/models/lead_batch.py,
    app/models/source_snapshot.py) for the race this closes.
  - Data backfill: any existing PDL-sourced LeadBatch (source='pdl_import')
    with logical_key still NULL gets it computed from its own leads'
    provenance (their shared source_snapshot + the ImportRun config that
    produced them) -- never guessed from the batch's name. If a batch's
    leads reference more than one distinct source snapshot, or its
    ImportRun attempts carry more than one distinct configuration, or any
    required field is missing, this migration ABORTS with a precise error
    naming the batch and the ambiguity -- it does not silently skip or
    guess. See the Phase 3 closeout handoff for the actual backfill run
    against this repository's real data (exactly one batch, unambiguous).

Revision ID: 0004_logical_key
Revises: 0003_pdl_identity
Create Date: 2026-09-22

"""
import hashlib
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004_logical_key'
down_revision: Union[str, Sequence[str], None] = '0003_pdl_identity'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


class AmbiguousBatchBackfillError(RuntimeError):
    """Raised (and left uncaught, aborting the migration) when an existing
    batch's logical_key cannot be unambiguously derived from its own data.
    """


def _compute_logical_key(
    *,
    source_checksum: str,
    mapping_version: str,
    target_per_segment: dict,
    seed: int,
    country_filter: str,
) -> str:
    """Must stay byte-for-byte identical to
    app.pdl.importer.compute_logical_key -- duplicated here (not imported)
    because migrations must stay stable even if application code changes
    later. If that function's algorithm ever changes, it changes for NEW
    keys only; this copy is what past data was backfilled with."""
    payload = "|".join(
        [
            source_checksum,
            mapping_version,
            ",".join(f"{k}={v}" for k, v in sorted(target_per_segment.items())),
            str(seed),
            country_filter,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _backfill_batch_logical_keys(bind) -> None:
    lead_batches = sa.table(
        "lead_batches",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("logical_key", sa.String()),
        sa.column("source", sa.String()),
    )
    leads = sa.table(
        "leads",
        sa.column("id", sa.Uuid()),
        sa.column("batch_id", sa.Uuid()),
        sa.column("source_snapshot_id", sa.Uuid()),
        sa.column("import_run_id", sa.Uuid()),
    )
    import_runs = sa.table(
        "import_runs",
        sa.column("id", sa.Uuid()),
        sa.column("config_seed", sa.JSON()),
    )
    source_snapshots = sa.table(
        "source_snapshots",
        sa.column("id", sa.Uuid()),
        sa.column("checksum", sa.String()),
    )

    batches_to_backfill = bind.execute(
        sa.select(lead_batches.c.id, lead_batches.c.name).where(
            lead_batches.c.source == "pdl_import",
            lead_batches.c.logical_key.is_(None),
        )
    ).fetchall()

    for batch in batches_to_backfill:
        lead_rows = bind.execute(
            sa.select(leads.c.source_snapshot_id, leads.c.import_run_id)
            .where(leads.c.batch_id == batch.id)
            .distinct()
        ).fetchall()

        distinct_snapshot_ids = {r.source_snapshot_id for r in lead_rows if r.source_snapshot_id}
        if len(distinct_snapshot_ids) != 1:
            raise AmbiguousBatchBackfillError(
                f"Batch {batch.id} ({batch.name!r}): its leads reference "
                f"{len(distinct_snapshot_ids)} distinct source_snapshot_id "
                "values, expected exactly 1. Refusing to guess a logical_key."
            )
        snapshot_id = next(iter(distinct_snapshot_ids))

        snapshot_row = bind.execute(
            sa.select(source_snapshots.c.checksum).where(source_snapshots.c.id == snapshot_id)
        ).first()
        if snapshot_row is None or not snapshot_row.checksum:
            raise AmbiguousBatchBackfillError(
                f"Batch {batch.id} ({batch.name!r}): source snapshot "
                f"{snapshot_id} has no checksum. Refusing to guess a logical_key."
            )

        distinct_import_run_ids = {r.import_run_id for r in lead_rows if r.import_run_id}
        if not distinct_import_run_ids:
            raise AmbiguousBatchBackfillError(
                f"Batch {batch.id} ({batch.name!r}): none of its leads "
                "reference an import_run_id. Refusing to guess a logical_key."
            )
        config_rows = bind.execute(
            sa.select(import_runs.c.config_seed).where(
                import_runs.c.id.in_(distinct_import_run_ids)
            )
        ).fetchall()
        distinct_configs = {
            json.dumps(r.config_seed, sort_keys=True) for r in config_rows if r.config_seed
        }
        if len(distinct_configs) != 1:
            raise AmbiguousBatchBackfillError(
                f"Batch {batch.id} ({batch.name!r}): its ImportRun attempts carry "
                f"{len(distinct_configs)} distinct configurations, expected exactly 1. "
                "Refusing to guess a logical_key."
            )
        cfg = json.loads(next(iter(distinct_configs)))
        required = ("seed", "mapping_version", "target_per_segment", "country_filter")
        missing = [k for k in required if cfg.get(k) is None]
        if missing:
            raise AmbiguousBatchBackfillError(
                f"Batch {batch.id} ({batch.name!r}): its ImportRun config_seed is "
                f"missing required field(s) {missing}. Refusing to guess a logical_key."
            )

        logical_key = _compute_logical_key(
            source_checksum=snapshot_row.checksum,
            mapping_version=cfg["mapping_version"],
            target_per_segment=cfg["target_per_segment"],
            seed=cfg["seed"],
            country_filter=cfg["country_filter"],
        )
        bind.execute(
            sa.update(lead_batches)
            .where(lead_batches.c.id == batch.id)
            .values(logical_key=logical_key)
        )


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('lead_batches', sa.Column('logical_key', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_lead_batches_logical_key'), 'lead_batches', ['logical_key'], unique=False)
    # ### end Alembic commands ###

    # Data migration (not autogenerated): must run BEFORE the unique
    # constraint below, since it's the step that gives every existing PDL
    # batch a real (non-NULL, non-colliding) value. Raises and aborts the
    # whole migration (transactional DDL rolls everything back) if any
    # batch's key can't be derived unambiguously -- never silently skipped.
    _backfill_batch_logical_keys(op.get_bind())

    # ### more auto generated commands ###
    op.create_unique_constraint('uq_lead_batches_logical_key', 'lead_batches', ['logical_key'])
    op.create_unique_constraint('uq_source_snapshots_provider_checksum', 'source_snapshots', ['provider', 'checksum'])
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema.

    HONEST WARNING: drops logical_key entirely, including backfilled and
    any newly-written values. A future re-upgrade re-derives it for
    PDL-sourced batches from their leads' provenance (same backfill logic),
    so re-upgrading is not itself lossy -- but any manual edits to
    logical_key made while this migration was applied are gone for good.
    """
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('uq_source_snapshots_provider_checksum', 'source_snapshots', type_='unique')
    op.drop_constraint('uq_lead_batches_logical_key', 'lead_batches', type_='unique')
    op.drop_index(op.f('ix_lead_batches_logical_key'), table_name='lead_batches')
    op.drop_column('lead_batches', 'logical_key')
    # ### end Alembic commands ###
