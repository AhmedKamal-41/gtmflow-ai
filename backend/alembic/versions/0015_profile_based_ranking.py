"""Profile-based lead ranking: scope fit scores to a seller-profile revision
and its target preferences.

Additive: two nullable columns and one index on lead_fit_scores. Existing
rows (scored against the retired `demo-us-sectors-v1` rubric) keep every
value; with no targeting_hash they are never "current" again, so they stay
queryable as history and their leads read as needing a new ranking. The
legacy `lead_scores` table is not touched: no code writes or reads it any
more, and its rows are kept as history. Seller-profile target preferences
live in the existing seller_profiles.profile JSON, so that table does not
change. Downgrade drops only what this revision added.
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_profile_based_ranking"
down_revision = "0014_signup_and_guest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lead_fit_scores") as batch:
        batch.add_column(sa.Column("seller_profile_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("targeting_hash", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_lead_fit_scores_seller_profile_id_seller_profiles",
            "seller_profiles", ["seller_profile_id"], ["id"],
        )
    op.create_index("ix_lead_fit_scores_seller_profile_id", "lead_fit_scores", ["seller_profile_id"])
    op.create_index("ix_lead_fit_scores_targeting_hash", "lead_fit_scores", ["targeting_hash"])
    op.create_index(
        "ix_lead_fit_scores_lead_targeting_created", "lead_fit_scores",
        ["lead_id", "targeting_hash", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_lead_fit_scores_lead_targeting_created", table_name="lead_fit_scores")
    op.drop_index("ix_lead_fit_scores_targeting_hash", table_name="lead_fit_scores")
    op.drop_index("ix_lead_fit_scores_seller_profile_id", table_name="lead_fit_scores")
    with op.batch_alter_table("lead_fit_scores") as batch:
        batch.drop_constraint("fk_lead_fit_scores_seller_profile_id_seller_profiles", type_="foreignkey")
        batch.drop_column("targeting_hash")
        batch.drop_column("seller_profile_id")
