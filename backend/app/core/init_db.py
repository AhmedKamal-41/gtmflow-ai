"""Manual database initialization helper -- quick local bring-up ONLY.

Usage from `backend/`:

    python -m app.core.init_db

This is a one-shot `Base.metadata.create_all`: fine for spinning up a throwaway
local/dev database from nothing, but it has no migration history, cannot
alter an existing table, and is not what any real (staging/production)
deployment should use. As of Phase 2, the sanctioned path is Alembic:

    alembic upgrade head                              # fresh database
    python scripts/verify_baseline_schema.py --stamp   # adopt an existing
    alembic upgrade head                               # database first

See docs/upgrade/decisions.md for why this distinction matters and
docs/upgrade/audit.md for the Phase 2 migration work itself.

Tables are not auto-created at app startup because schema changes should be
deliberate, tests must not require a running Postgres, and a process restart
should never silently alter the database.
"""

from app import models  # noqa: F401 -- registers every model on Base.metadata
from app.core.database import Base, get_engine


def init_db() -> None:
    """Create every table defined on Base.metadata. Requires DATABASE_URL."""
    Base.metadata.create_all(bind=get_engine())


if __name__ == "__main__":
    init_db()
    print("All tables created.")
