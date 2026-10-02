"""Add immutable seller-profile drafts for the single workspace.

No seed data, activation, score changes or changes to existing tables.
Take a backup and verify restoration before upgrading a populated database.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_seller_profiles"
down_revision = "0006_fit_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seller_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("editor_label", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )


def downgrade() -> None:
    """Destructive: removes stored seller-profile drafts. Restore from backup."""
    op.drop_table("seller_profiles")
