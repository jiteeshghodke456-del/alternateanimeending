"""SQLite engine and session helpers (SQLModel)."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        path = Path(get_settings().db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    return _engine


def reset_engine() -> None:
    """Dispose the engine (tests switch DB_PATH between runs)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(get_engine())


def drop_db() -> None:
    from app import models  # noqa: F401

    SQLModel.metadata.drop_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Background tasks must open their own `Session(get_engine())`."""
    with Session(get_engine()) as session:
        yield session
