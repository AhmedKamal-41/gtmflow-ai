"""Phase 6: output purpose/author, reviewed content hash, split manifests and
the training-annotation queue.

Additive only. Existing ai_outputs rows become purpose='operational' via the
server default (true: every existing row is a lead's operational output).
Existing reviews keep content_hash NULL -- they never identified the exact
content, so they are not relabeled. No other data is written.
Take a backup and verify restoration before upgrading a populated database.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_review_annotation"
down_revision = "0008_seller_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_outputs") as batch:
        batch.add_column(sa.Column(
            "purpose", sa.String(16), nullable=False, server_default="operational"
        ))
        batch.add_column(sa.Column("author_label", sa.String(64), nullable=True))
        batch.create_index("ix_ai_outputs_purpose", ["purpose"])
    with op.batch_alter_table("ai_output_reviews") as batch:
        batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))

    op.create_table(
        "split_manifests",
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("seed", sa.String(64), nullable=False),
        sa.Column("algorithm", sa.String(200), nullable=False),
        sa.Column("ratios", sa.JSON(), nullable=False),
        sa.Column("lead_count", sa.Integer(), nullable=False),
        sa.Column("group_count", sa.Integer(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("version", name="pk_split_manifests"),
    )
    op.create_table(
        "company_split_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("manifest_version", sa.String(64), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("company_identity_id", sa.Uuid(), nullable=True),
        sa.Column("group_key", sa.String(64), nullable=False),
        sa.Column("split", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["manifest_version"], ["split_manifests.version"],
                                name="fk_company_split_assignments_manifest_version_split_manifests"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"],
                                name="fk_company_split_assignments_lead_id_leads"),
        sa.ForeignKeyConstraint(["company_identity_id"], ["company_identities.id"],
                                name="fk_split_assignments_company_identity_id"),
        sa.PrimaryKeyConstraint("id", name="pk_company_split_assignments"),
        sa.UniqueConstraint("manifest_version", "lead_id",
                            name="uq_company_split_assignments_manifest_lead"),
    )
    op.create_index("ix_company_split_assignments_manifest_version", "company_split_assignments", ["manifest_version"])
    op.create_index("ix_company_split_assignments_lead_id", "company_split_assignments", ["lead_id"])
    op.create_index("ix_company_split_assignments_group_key", "company_split_assignments", ["group_key"])
    op.create_index("ix_company_split_assignments_split", "company_split_assignments", ["split"])

    op.create_table(
        "annotation_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("queue", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("manifest_version", sa.String(64), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("group_key", sa.String(64), nullable=False),
        sa.Column("split", sa.String(16), nullable=False),
        sa.Column("task", sa.String(32), nullable=False),
        sa.Column("source_output_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["manifest_version"], ["split_manifests.version"],
                                name="fk_annotation_candidates_manifest_version_split_manifests"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_annotation_candidates_lead_id_leads"),
        sa.ForeignKeyConstraint(["source_output_id"], ["ai_outputs.id"],
                                name="fk_annotation_candidates_source_output_id_ai_outputs"),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_candidates"),
        sa.UniqueConstraint("queue", "lead_id", "task", name="uq_annotation_candidates_queue_lead_task"),
        sa.UniqueConstraint("queue", "position", name="uq_annotation_candidates_queue_position"),
    )
    op.create_index("ix_annotation_candidates_queue", "annotation_candidates", ["queue"])
    op.create_index("ix_annotation_candidates_lead_id", "annotation_candidates", ["lead_id"])

    op.create_table(
        "training_annotations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("source_output_id", sa.Uuid(), nullable=False),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("target_output_id", sa.Uuid(), nullable=True),
        sa.Column("target_content_hash", sa.String(64), nullable=True),
        sa.Column("factual_support", sa.String(24), nullable=True),
        sa.Column("writing_quality", sa.Integer(), nullable=True),
        sa.Column("missing_info_handling", sa.String(16), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("reviewer_label", sa.String(64), nullable=False),
        sa.Column("review_mode", sa.String(16), nullable=False),
        sa.Column("timing", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["annotation_candidates.id"],
                                name="fk_training_annotations_candidate_id_annotation_candidates"),
        sa.ForeignKeyConstraint(["source_output_id"], ["ai_outputs.id"],
                                name="fk_training_annotations_source_output_id_ai_outputs"),
        sa.ForeignKeyConstraint(["target_output_id"], ["ai_outputs.id"],
                                name="fk_training_annotations_target_output_id_ai_outputs"),
        sa.PrimaryKeyConstraint("id", name="pk_training_annotations"),
        sa.UniqueConstraint("submission_id", name="uq_training_annotations_submission_id"),
    )
    op.create_index("ix_training_annotations_candidate_id", "training_annotations", ["candidate_id"])


def downgrade() -> None:
    """Destructive: drops annotation data, split manifests, reviewed-content
    hashes and human revision metadata. Restore from backup instead on a
    database with real Phase 6 data."""
    op.drop_index("ix_training_annotations_candidate_id", table_name="training_annotations")
    op.drop_table("training_annotations")
    op.drop_index("ix_annotation_candidates_lead_id", table_name="annotation_candidates")
    op.drop_index("ix_annotation_candidates_queue", table_name="annotation_candidates")
    op.drop_table("annotation_candidates")
    for name in ("split", "group_key", "lead_id", "manifest_version"):
        op.drop_index(f"ix_company_split_assignments_{name}", table_name="company_split_assignments")
    op.drop_table("company_split_assignments")
    op.drop_table("split_manifests")
    with op.batch_alter_table("ai_output_reviews") as batch:
        batch.drop_column("content_hash")
    with op.batch_alter_table("ai_outputs") as batch:
        batch.drop_index("ix_ai_outputs_purpose")
        batch.drop_column("author_label")
        batch.drop_column("purpose")
