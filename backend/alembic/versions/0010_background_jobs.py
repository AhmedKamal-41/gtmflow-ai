"""Phase 10: durable background jobs (background_jobs, background_job_items).

Additive only: two new tables, no change to existing rows. Take a backup and
verify restoration before upgrading a populated database.
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_background_jobs"
down_revision = "0009_review_annotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(48), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("max_item_attempts", sa.Integer(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=True),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["batch_id"], ["lead_batches.id"], name="fk_background_jobs_batch_id_lead_batches"),
        sa.PrimaryKeyConstraint("id", name="pk_background_jobs"),
        sa.UniqueConstraint("dedupe_key", name="uq_background_jobs_dedupe_key"),
    )
    op.create_index("ix_background_jobs_job_type", "background_jobs", ["job_type"])
    op.create_index("ix_background_jobs_batch_id", "background_jobs", ["batch_id"])
    op.create_index("ix_background_jobs_status", "background_jobs", ["status"])
    op.create_index("ix_background_jobs_lease_expires_at", "background_jobs", ["lease_expires_at"])

    op.create_table(
        "background_job_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["background_jobs.id"], name="fk_background_job_items_job_id_background_jobs"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_background_job_items_lead_id_leads"),
        sa.PrimaryKeyConstraint("id", name="pk_background_job_items"),
        sa.UniqueConstraint("job_id", "lead_id", name="uq_background_job_items_job_lead"),
    )
    op.create_index("ix_background_job_items_job_id", "background_job_items", ["job_id"])
    op.create_index("ix_background_job_items_status", "background_job_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_background_job_items_status", table_name="background_job_items")
    op.drop_index("ix_background_job_items_job_id", table_name="background_job_items")
    op.drop_table("background_job_items")
    op.drop_index("ix_background_jobs_lease_expires_at", table_name="background_jobs")
    op.drop_index("ix_background_jobs_status", table_name="background_jobs")
    op.drop_index("ix_background_jobs_batch_id", table_name="background_jobs")
    op.drop_index("ix_background_jobs_job_type", table_name="background_jobs")
    op.drop_table("background_jobs")
