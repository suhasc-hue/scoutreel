"""Engine, session factory and init. SQLite now, Postgres-swappable via DATABASE_URL."""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str | None = None):
    url = url or get_settings().database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _auto_migrate() -> None:
    """Pragmatic SQLite migration: add columns/indexes that new code expects
    but an existing DB lacks. Handles additive changes only (the common case);
    renames/drops still need manual work."""
    from sqlalchemy import inspect, text

    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue  # create_all will make it
            existing_cols = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing_cols:
                    ddl = (
                        f'ALTER TABLE "{table.name}" ADD COLUMN '
                        f'"{col.name}" {col.type.compile(engine.dialect)}'
                    )
                    # carry simple scalar defaults so existing rows don't
                    # become NULL where the model expects a value
                    default = getattr(col.default, "arg", None)
                    if isinstance(default, bool):
                        ddl += f" DEFAULT {int(default)}"
                    elif isinstance(default, (int, float)):
                        ddl += f" DEFAULT {default}"
                    elif isinstance(default, str):
                        escaped = default.replace("'", "''")
                        ddl += f" DEFAULT '{escaped}'"
                    conn.execute(text(ddl))
    # Indexes: create_all skips tables that already exist, so create these
    # explicitly (no-op when present).
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(bind=engine, checkfirst=True)


def init_db() -> None:
    # Import models so tables register on Base before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    _auto_migrate()


def get_db():
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    """For jobs/scripts."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
