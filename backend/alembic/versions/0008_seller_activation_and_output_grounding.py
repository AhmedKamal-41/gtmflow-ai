"""Add explicit seller-profile activation and per-output seller provenance.

Additive only: one new append-only table and four nullable columns on
ai_outputs. Existing outputs keep NULL seller provenance -- they were not
produced by grounded prompting and are never relabeled. No data is written.
Take a backup and verify restoration before upgrading a populated database.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_seller_activation"
down_revision = "0007_seller_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seller_profile_activations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("seller_profile_id", sa.Uuid(), nullable=True),
        sa.Column("seller_profile_version", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reviewed_confirmation", sa.Boolean(), nullable=False),
        sa.Column("demo_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("actor_label", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["seller_profile_id"],
            ["seller_profiles.id"],
            name="fk_seller_profile_activations_seller_profile_id_seller_profiles",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_seller_profile_activations"),
        sa.UniqueConstraint("sequence", name="uq_seller_profile_activations_sequence"),
    )
    op.create_index(
        "ix_seller_profile_activations_seller_profile_id",
        "seller_profile_activations",
        ["seller_profile_id"],
    )
    with op.batch_alter_table("ai_outputs") as batch:
        batch.add_column(sa.Column("seller_profile_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("seller_profile_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("seller_profile_content_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("seller_profile_kind", sa.String(16), nullable=True))
        batch.create_foreign_key(
            "fk_ai_outputs_seller_profile_id_seller_profiles",
            "seller_profiles",
            ["seller_profile_id"],
            ["id"],
        )
        batch.create_index("ix_ai_outputs_seller_profile_id", ["seller_profile_id"])


def downgrade() -> None:
    """Destructive: drops activation history and output seller provenance.
    Restore from backup instead on a database with real Phase 5 data."""
    with op.batch_alter_table("ai_outputs") as batch:
        batch.drop_index("ix_ai_outputs_seller_profile_id")
        batch.drop_constraint("fk_ai_outputs_seller_profile_id_seller_profiles", type_="foreignkey")
        batch.drop_column("seller_profile_kind")
        batch.drop_column("seller_profile_content_hash")
        batch.drop_column("seller_profile_version")
        batch.drop_column("seller_profile_id")
    op.drop_index(
        "ix_seller_profile_activations_seller_profile_id",
        table_name="seller_profile_activations",
    )
    op.drop_table("seller_profile_activations")
