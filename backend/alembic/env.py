"""Alembic migration environment for GTMFlow AI.

Migrations are run explicitly (``alembic upgrade head``), never
automatically on application startup -- see app/main.py, which only wires
CORS middleware, and docs/upgrade/decisions.md's Phase 2 notes for why.

The database URL is never hardcoded here or in alembic.ini: it's read at
runtime from the same ``app.core.config.settings.database_url`` every other
part of this app uses (i.e. the ``DATABASE_URL`` environment variable /
``.env`` file), so nothing resembling a credential is ever committed.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Import every model so they all register on Base.metadata before Alembic
# diffs against it -- mirrors app/core/init_db.py's `from app import models`.
from app import models  # noqa: F401
from app.core.config import settings
from app.core.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Alembic needs a real database "
            "URL to run migrations -- set it in backend/.env or the "
            "environment before running `alembic upgrade`/`alembic revision`."
        )
    return settings.database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (``--sql`` mode)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    ini_section = config.get_section(config.config_ini_section, {})
    ini_section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        ini_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
