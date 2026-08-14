"""
Database foundation — SQLite via SQLAlchemy 2.0.

Single-bench deployment: one embedded SQLite file at ``data/atb.db``. The schema
is created on first run with ``create_all`` so the Raspberry Pi needs no manual
migration step. When the schema starts to evolve, layer Alembic on top (it is
already listed in requirements) — point its ``target_metadata`` at ``Base``.

Timestamps are stored as epoch seconds (float), matching the rest of the system
(``time.time()`` is used throughout state_manager / test_engine), so persisted
values line up 1:1 with the in-memory sessions.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
DEFAULT_DB_PATH = _DATA_DIR / "atb.db"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Set by init_db(); use get_session() rather than importing this directly so
# callers always see the engine created at startup.
_engine: Optional[Engine] = None
SessionLocal: Optional[sessionmaker] = None


def init_db(db_path: Optional[Path | str] = None, echo: bool = False) -> Engine:
    """
    Create the engine, apply SQLite pragmas, and ensure the schema exists.
    Idempotent — safe to call once at startup. Returns the engine.
    """
    global _engine, SessionLocal

    if db_path is None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = DEFAULT_DB_PATH

    url = f"sqlite:///{db_path}"
    # check_same_thread=False: the test engine writes from a background thread.
    # Each store operation opens its own short-lived Session, so this is safe.
    engine = create_engine(
        url, echo=echo, future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")   # readers don't block the writer
        cur.execute("PRAGMA foreign_keys=ON")    # enforce FKs
        cur.execute("PRAGMA busy_timeout=5000")  # wait out brief write locks
        cur.close()

    # Import models so their tables are registered on Base.metadata, then create.
    from db import models  # noqa: F401
    Base.metadata.create_all(engine)
    _ensure_columns(engine)   # lightweight additive migration for existing DBs

    _engine = engine
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True,
                                class_=Session)
    log.info(f"Database ready at {db_path}")
    return engine


# Additive columns to backfill onto pre-existing databases. SQLAlchemy's
# create_all() only creates missing *tables*, never missing *columns*, so when a
# model gains a column we add it here (SQLite ALTER TABLE ADD COLUMN is cheap and
# safe). Until Alembic is adopted this is the migration path for new columns.
_ADDITIVE_COLUMNS = [
    # (table, column, column-type SQL)
    ("measurements", "phase_ok", "BOOLEAN"),
]


def _ensure_columns(engine: Engine) -> None:
    from sqlalchemy import text
    with engine.begin() as conn:
        for table, column, coltype in _ADDITIVE_COLUMNS:
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                log.info(f"Migrated: added {table}.{column}")


def get_engine() -> Optional[Engine]:
    return _engine


def get_session() -> Session:
    """Open a new Session. Caller is responsible for closing it (use a `with`)."""
    if SessionLocal is None:
        raise RuntimeError("init_db() has not been called yet")
    return SessionLocal()
