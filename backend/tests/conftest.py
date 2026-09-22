import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401 -- register all models on Base.metadata
from app.core.database import Base, get_session
from app.main import app


@pytest.fixture()
def db_engine() -> Iterator[Engine]:
    # Opt-in: TEST_DATABASE_URL points the suite at a DISPOSABLE database
    # (e.g. an empty Postgres DB) to exercise dialect-specific behavior --
    # window functions, NULL ordering, savepoints, UUID comparison. Every
    # table is dropped and recreated around each test, so never point this
    # at a database whose contents matter.
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        engine = create_engine(test_url, future=True)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine(
            "sqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        if test_url:
            Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def db_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False, future=True
    )


@pytest.fixture()
def db_session(db_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def _override() -> Iterator[Session]:
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)
