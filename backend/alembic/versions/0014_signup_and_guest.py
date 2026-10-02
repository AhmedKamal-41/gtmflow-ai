"""Self-service sign-up with email verification, and guest accounts.

Additive: widens users.username (CLI usernames fit unchanged), adds two
nullable user columns and the email_verifications table. Existing accounts,
sessions and audit history are not rewritten. Downgrade removes only what
this revision added; it refuses to run while sign-up or guest accounts exist,
because their usernames may not fit the old column.
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_signup_and_guest"
down_revision = "0013_auth_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("username", existing_type=sa.String(40), type_=sa.String(254), existing_nullable=False)
        batch.add_column(sa.Column("created_via", sa.String(16), nullable=True))
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "email_verifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_email_verifications_user_id_users"),
        sa.PrimaryKeyConstraint("user_id", name="pk_email_verifications"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    remaining = bind.execute(sa.text("SELECT count(*) FROM users WHERE created_via IS NOT NULL")).scalar()
    if remaining:
        raise RuntimeError(f"{remaining} sign-up/guest accounts exist; remove them deliberately before downgrading.")
    op.drop_table("email_verifications")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verified_at")
        batch.drop_column("created_via")
        batch.alter_column("username", existing_type=sa.String(254), type_=sa.String(40), existing_nullable=False)
