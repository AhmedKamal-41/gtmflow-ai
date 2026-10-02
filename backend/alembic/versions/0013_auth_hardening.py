"""Shared login throttling and stable actor identity for new jobs.

Additive: existing accounts, sessions, jobs, data and audit history are not
rewritten. Downgrade removes only the new throttle table and nullable FK.
Never test downgrades on a populated working database.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_auth_hardening"
down_revision = "0012_operator_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_throttles",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_login_throttles"),
    )
    with op.batch_alter_table("background_jobs") as batch:
        batch.add_column(sa.Column("created_by_user_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_background_jobs_created_by_user_id_users", "users", ["created_by_user_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("background_jobs") as batch:
        batch.drop_constraint("fk_background_jobs_created_by_user_id_users", type_="foreignkey")
        batch.drop_column("created_by_user_id")
    op.drop_table("login_throttles")
