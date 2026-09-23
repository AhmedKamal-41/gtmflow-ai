"""Exercise migration 0007 on a restored SQLite fixture with existing data.

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
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import Lead, LeadBatch, SellerProfile


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


def test_additive_migration_preserves_restored_fixture_and_matches_model(tmp_path):
    original = tmp_path / "original.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    engine = create_engine(f"sqlite:///{original}")
    tables = [table for table in Base.metadata.sorted_tables if table.name != "seller_profiles"]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        batch = LeadBatch(name="Existing partial fixture", source="csv", status="partial")
        session.add(batch)
        session.flush()
        session.add(Lead(batch_id=batch.id, company_name="Preserved fixture", status="do_not_contact"))
        session.commit()
    engine.dispose()
    table_names = [table.name for table in tables]
    before = existing_data(original, table_names)
    copy_database(original, backup)
    copy_database(backup, restored)
    assert existing_data(restored, table_names) == before

    path = Path(__file__).parents[1] / "alembic/versions/0007_seller_profile_drafts.py"
    spec = importlib.util.spec_from_file_location("seller_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0006_fit_scores"
    restored_engine = create_engine(f"sqlite:///{restored}")
    try:
        with restored_engine.begin() as connection:
            context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
            with Operations.context(context):
                migration.upgrade()
            assert "seller_profiles" in inspect(connection).get_table_names()
            assert compare_metadata(context, Base.metadata) == []
        assert existing_data(restored, table_names) == before
        assert existing_data(original, table_names) == before
        with Session(restored_engine) as session:
            assert list(session.scalars(select(SellerProfile))) == []
        with restored_engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert "seller_profiles" not in inspect(connection).get_table_names()
        assert existing_data(restored, table_names) == before
    finally:
        restored_engine.dispose()
