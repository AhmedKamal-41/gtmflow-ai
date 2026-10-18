"""Exercise migrations 0007, 0008, 0009 and 0015 on a restored SQLite fixture with existing data.

Earlier migrations use PostgreSQL-only ALTER operations. This fixture starts
from the current model metadata excluding seller_profiles (the 0006 shape);
it does not claim to replay migrations 0001-0006 or verify real PostgreSQL.
"""
import importlib.util
import sqlite3
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import ForeignKeyConstraint, Index, MetaData, Table, create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import AIOutput, Lead, LeadBatch, LeadFitScore, SellerProfile


def copy_database(source, destination):
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def existing_data(path, tables):
    connection = sqlite3.connect(path)
    try:
        return {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables}
    finally:
        connection.close()


def load_migration(filename):
    path = Path(__file__).parents[1] / "alembic/versions" / filename
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


PHASE5_TABLES = {
    "seller_profiles", "seller_profile_activations",
    # Phase 6 (0009)
    "split_manifests", "company_split_assignments", "annotation_candidates", "training_annotations",
}
PHASE5_OUTPUT_COLUMNS = {
    "seller_profile_id",
    "seller_profile_version",
    "seller_profile_content_hash",
    "seller_profile_kind",
    # Phase 6 (0009)
    "purpose",
    "author_label",
}
RANKING_COLUMNS = {"seller_profile_id", "targeting_hash"}  # 0015
NEW_COLUMNS = {"ai_outputs": PHASE5_OUTPUT_COLUMNS, "ai_output_reviews": {"content_hash"},
               "lead_fit_scores": RANKING_COLUMNS}


def test_additive_migrations_preserve_restored_fixture_and_match_model(tmp_path):
    original = tmp_path / "original.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    engine = create_engine(f"sqlite:///{original}")
    # The 0006 shape: current metadata minus the Phase 5 tables and columns.
    pre_phase5 = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.sorted_tables:
        if table.name in PHASE5_TABLES:
            continue
        if table.name in NEW_COLUMNS:
            excluded = NEW_COLUMNS[table.name]
            foreign_keys = [
                ForeignKeyConstraint(
                    [c.name for c in fk.columns],
                    [element.target_fullname for element in fk.elements],
                    name=fk.name,
                )
                for fk in table.foreign_key_constraints
                if not {c.name for c in fk.columns} & excluded
            ]
            copied = Table(table.name, pre_phase5, *[
                column._copy() for column in table.columns
                if column.name not in excluded
            ], *foreign_keys)
            for index in table.indexes:
                names = {c.name for c in index.columns}
                if not names & excluded and index.name not in {i.name for i in copied.indexes}:
                    Index(index.name, *[copied.c[c.name] for c in index.columns])
        else:
            table.to_metadata(pre_phase5)
    tables = list(pre_phase5.sorted_tables)
    pre_phase5.create_all(engine)
    with engine.begin() as connection:
        batch_id = "11111111111111111111111111111111"
        lead_id = "22222222222222222222222222222222"
        connection.exec_driver_sql(
            "INSERT INTO lead_batches (id, name, source, total_leads, processed_leads, status, created_at, updated_at) "
            "VALUES (?, 'Existing partial fixture', 'csv', 1, 1, 'partial', '2026-09-22', '2026-09-22')",
            (batch_id,),
        )
        connection.exec_driver_sql(
            "INSERT INTO leads (id, batch_id, company_name, status, created_at, updated_at) "
            "VALUES (?, ?, 'Preserved fixture', 'do_not_contact', '2026-09-22', '2026-09-22')",
            (lead_id, batch_id),
        )
        # A historical v1 output that must keep NULL seller provenance.
        # A ranking from the retired demo rubric, kept as history by 0015.
        connection.exec_driver_sql(
            "INSERT INTO lead_fit_scores (id, lead_id, scorer_version, profile_id, profile_version, "
            "normalization_version, input_fingerprint, fit_score, evidence_coverage_pct, band, criteria, "
            "readiness, eligibility_excluded, eligibility_reasons, computed_at, computation_ms, created_at) "
            "VALUES ('44444444444444444444444444444444', ?, 'fit-scorer-v1', 'demo-us-sectors-v1', '1', "
            "'fit-norm-v1', ?, 100, 100.0, 'strong_match', '[]', '{}', 1, '[]', '2026-09-22', 0.1, '2026-09-22')",
            (lead_id, "f" * 64),
        )
        connection.exec_driver_sql(
            "INSERT INTO ai_outputs (id, lead_id, output_type, content, model_used, prompt_version, origin, created_at) "
            "VALUES ('33333333333333333333333333333333', ?, 'outreach_email', '{\"subject\": \"legacy\"}', 'mock', 'v1', 'generated', '2026-09-22')",
            (lead_id,),
        )
    engine.dispose()
    table_names = [table.name for table in tables]
    before = existing_data(original, table_names)
    copy_database(original, backup)
    copy_database(backup, restored)
    assert existing_data(restored, table_names) == before

    drafts = load_migration("0007_seller_profile_drafts.py")
    activation = load_migration("0008_seller_activation_and_output_grounding.py")
    review = load_migration("0009_review_revisions_and_annotation.py")
    assert drafts.down_revision == "0006_fit_scores"
    assert activation.down_revision == drafts.revision
    assert review.down_revision == activation.revision
    ranking = load_migration("0015_profile_based_ranking.py")
    assert ranking.down_revision == "0014_signup_and_guest"
    restored_engine = create_engine(f"sqlite:///{restored}")
    try:
        with restored_engine.begin() as connection:
            context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
            with Operations.context(context):
                drafts.upgrade()
                activation.upgrade()
                review.upgrade()
                ranking.upgrade()
            assert PHASE5_TABLES <= set(inspect(connection).get_table_names())
            assert compare_metadata(context, Base.metadata) == []
        # Existing rows survive; the new output columns read NULL for them.
        preserved = existing_data(restored, table_names)
        assert {t: len(rows) for t, rows in preserved.items()} == {t: len(rows) for t, rows in before.items()}
        assert existing_data(original, table_names) == before
        with Session(restored_engine) as session:
            assert list(session.scalars(select(SellerProfile))) == []
            legacy = session.scalars(select(AIOutput)).one()
            assert legacy.prompt_version == "v1"
            assert legacy.content == {"subject": "legacy"}
            assert legacy.seller_profile_id is None
            assert legacy.seller_profile_content_hash is None
            # 0009: existing outputs are operational (server default), not relabeled.
            assert legacy.purpose == "operational"
            assert legacy.author_label is None
            # 0015: the old ranking is kept, unchanged, with no targeting hash
            # (so it can never read as current).
            old = session.scalars(select(LeadFitScore)).one()
            assert (old.profile_id, old.fit_score, old.band) == ("demo-us-sectors-v1", 100, "strong_match")
            assert old.targeting_hash is None and old.seller_profile_id is None
            lead = session.scalars(select(Lead)).one()
            assert lead.status == "do_not_contact"
            assert session.scalars(select(LeadBatch)).one().status == "partial"
        with restored_engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                ranking.downgrade()
                review.downgrade()
                activation.downgrade()
                drafts.downgrade()
            assert not PHASE5_TABLES & set(inspect(connection).get_table_names())
            columns = {c["name"] for c in inspect(connection).get_columns("ai_outputs")}
            assert not PHASE5_OUTPUT_COLUMNS & columns
            assert not RANKING_COLUMNS & {c["name"] for c in inspect(connection).get_columns("lead_fit_scores")}
        assert existing_data(restored, table_names) == before
    finally:
        restored_engine.dispose()
