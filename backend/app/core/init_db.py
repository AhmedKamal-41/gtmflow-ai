"""Manual database initialization helper.

Usage from `backend/`:

    python -m app.core.init_db

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
