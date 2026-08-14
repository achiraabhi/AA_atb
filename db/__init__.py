"""
ATB persistence layer.

Single-bench deployment: an embedded SQLite database at ``data/atb.db``, driven
through SQLAlchemy 2.0. The schema is created automatically on first run, so the
bench needs no migration step. The public surface is intentionally small:

    from db.base import init_db, get_session
    from db.store import ResultStore

See db/base.py for the engine/session and db/models.py for the schema.
"""
