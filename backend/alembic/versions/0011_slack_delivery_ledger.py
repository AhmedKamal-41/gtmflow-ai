"""Phase 11: Slack delivery ledger columns on integration_pushes.

Additive only: nullable columns, a unique delivery_key and an index. Existing
push rows keep their values; their new columns stay NULL (never backfilled --
which draft they delivered was not recorded). Take a backup and verify
restoration before upgrading a populated database.
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_slack_delivery_ledger"
down_revision = "0010_background_jobs"
branch_labels = None
depends_on = None

COLUMNS = [
    sa.Column("delivery_key", sa.String(200), nullable=True),
    sa.Column("approved_output_id", sa.Uuid(), nullable=True),
    sa.Column("approved_content_hash", sa.String(64), nullable=True),
    sa.Column("attempt", sa.Integer(), nullable=True),
    sa.Column("delivery_mode", sa.String(8), nullable=True),
    sa.Column("claimed_by", sa.String(128), nullable=True),
    sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("outcome_code", sa.String(48), nullable=True),
    sa.Column("resolution", sa.String(32), nullable=True),
    sa.Column("resolution_note", sa.String(500), nullable=True),
    sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    with op.batch_alter_table("integration_pushes") as batch:
        for column in COLUMNS:
            batch.add_column(column)
        batch.create_unique_constraint("uq_integration_pushes_delivery_key", ["delivery_key"])
        batch.create_foreign_key("fk_integration_pushes_approved_output_id_ai_outputs", "ai_outputs",
                                 ["approved_output_id"], ["id"])
        batch.create_index("ix_integration_pushes_approved_output_id", ["approved_output_id"])
        batch.create_index("ix_integration_pushes_delivery_mode", ["delivery_mode"])


def downgrade() -> None:
    with op.batch_alter_table("integration_pushes") as batch:
        batch.drop_index("ix_integration_pushes_delivery_mode")
        batch.drop_index("ix_integration_pushes_approved_output_id")
        batch.drop_constraint("fk_integration_pushes_approved_output_id_ai_outputs", type_="foreignkey")
        batch.drop_constraint("uq_integration_pushes_delivery_key", type_="unique")
        for column in reversed(COLUMNS):
            batch.drop_column(column.name)
