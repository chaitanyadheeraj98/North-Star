"""SQLite engine and session management.

Personal-use scale: one file, one writer, no connection pool worth the name.
The settings below exist because SQLite's defaults are wrong for a web app
holding a database on a bind-mounted volume.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from .models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _ensure_parent_directory(database_url: str) -> None:
    """Create the data directory if the volume mount produced an empty one."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    raw = database_url[len(prefix) :]
    if raw.startswith("/") and not raw.startswith("//"):
        raw = raw  # absolute POSIX path
    path = Path(raw)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _ensure_parent_directory(settings.database_url)
        _engine = create_engine(
            settings.database_url,
            # FastAPI hands requests to a thread pool; SQLite objects would
            # otherwise refuse to cross threads.
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
        )

        @event.listens_for(_engine, "connect")
        def _configure_sqlite(dbapi_connection, _record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            # Foreign keys are off by default in SQLite, which would silently
            # let orphaned metrics rows survive a deleted execution.
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL keeps reads from blocking on the import write path.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, future=True
        )
    return _SessionLocal


def init_db() -> None:
    """Create tables if they do not exist.

    The schema is small and additive so far, so `create_all` is honest here.
    Alembic is wired in the moment a destructive migration is needed; see
    README "Known V1 limitations".
    """
    Base.metadata.create_all(bind=get_engine())


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for code outside a request."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop cached engine/session factory. Used by the test suite."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
