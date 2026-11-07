"""SQLite engine and session helpers (SQLModel)."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app import workspaces
from app.config import get_settings

_engine: Engine | None = None


def install_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


def get_engine() -> Engine:
    """The engine of the current workspace (app/workspaces.py), or the default DB at DB_PATH when
    no workspace is active (workspaces off, CLI scripts, startup)."""
    ref = workspaces.current_ref()
    if ref is not None:
        return workspaces.engine_for(workspaces.ensure(ref))
    global _engine
    if _engine is None:
        path = Path(get_settings().db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        install_pragmas(_engine)

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
    add_missing_columns()


def add_missing_columns() -> list[str]:
    """create_all() never alters existing tables. When a DB file from an older build is reused,
    add any new *nullable or defaulted* columns so it keeps working (SQLite ADD COLUMN only)."""
    from sqlalchemy import inspect, text

    eng = get_engine()
    added: list[str] = []
    from app import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(eng)  # new tables (e.g. precedent) in a DB from an older build; existing ones untouched
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or col.primary_key:
                    continue
                ddl = col.type.compile(dialect=eng.dialect)
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                if default is None and not col.nullable:
                    continue  # can't add a NOT NULL column without a default; a reset fixes it
                sql = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'
                if default is not None:
                    sql += f" DEFAULT {default!r}" if isinstance(default, str) else f" DEFAULT {int(default) if isinstance(default, bool) else default}"
                conn.execute(text(sql))
                added.append(f"{table.name}.{col.name}")
    return added


def drop_db() -> None:
    from app import models  # noqa: F401

    SQLModel.metadata.drop_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency (per workspace). Background tasks open their own `Session(get_engine())`;
    pipeline.run._schedule binds them to the workspace that scheduled them."""
    with Session(get_engine()) as session:
        yield session
