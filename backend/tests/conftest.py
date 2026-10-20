"""
Test-Setup: SQLite statt PostgreSQL.

DATABASE_URL zeigt auf einen Dummy-Postgres (Engines verbinden lazy, es wird nie
verbunden); die für die Tests relevanten Sessions werden auf SQLite umgebogen.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
# Die meisten Tests prüfen Fachlogik ohne Cookie; die Anmeldung testet test_auth.py gezielt
os.environ.setdefault("AUTH_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.models  # noqa: F401  (registriert alle Tabellen)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # workflow_tasks.payload u.a.
    return "JSON"


@pytest.fixture(autouse=True)
def _clear_query_caches():
    from app.services.query_guard import clear_all_caches
    clear_all_caches()
    yield
    clear_all_caches()


@pytest.fixture()
def sqlite_session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr("app.routers.documents.SessionLocal", factory)
    monkeypatch.setattr("app.services.workflow_runner.SessionLocal", factory)
    monkeypatch.setattr("app.services.change_log.SessionLocal", factory)
    monkeypatch.setattr("app.services.data_backup.SessionLocal", factory)
    return factory


@pytest.fixture()
def db(sqlite_session_factory):
    session = sqlite_session_factory()
    try:
        yield session
    finally:
        session.close()
