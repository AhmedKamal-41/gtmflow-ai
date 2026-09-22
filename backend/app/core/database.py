from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# Deterministic constraint names so Alembic autogenerate can emit a working
# downgrade() (drop_constraint needs a name to target) instead of the
# database-assigned defaults, which differ across dialects and aren't known
# until after the DDL runs. Added in Phase 2, alongside the first real
# Alembic migrations; existing constraints created before this (via
# `Base.metadata.create_all`) keep whatever name Postgres auto-assigned --
# this only governs names Alembic generates for *new* DDL going forward.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return create_engine(settings.database_url, future=True, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(), autocommit=False, autoflush=False, future=True
    )


def get_session() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
