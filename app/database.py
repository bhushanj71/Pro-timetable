"""
SQLAlchemy engine/session setup. Works with SQLite locally and
Postgres (Vercel Postgres / Neon / Supabase) in production via DATABASE_URL.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {}
url = settings.DATABASE_URL
if url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

# Vercel Postgres / Neon URLs sometimes use postgres:// which SQLAlchemy 2.x rejects
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql+psycopg://", 1)
elif url.startswith("postgresql://") and "+psycopg" not in url:
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added after the initial release. `create_all` only creates missing
# tables, never missing columns, so existing databases need these backfilled.
# Keyed by table -> (column name, DDL type + default).
_ADDITIVE_COLUMNS = {
    "users": [
        ("is_admin", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("is_active", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("last_login_at", "TIMESTAMP"),
    ],
}


def _apply_additive_migrations():
    """Add any missing columns in-place.

    This is a pragmatic stand-in for Alembic so existing SQLite/Postgres
    databases keep working across upgrades. For schema changes beyond adding
    nullable/defaulted columns, switch to real migrations.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    is_postgres = engine.dialect.name == "postgresql"

    with engine.begin() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue  # create_all will build it with every column already
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name in present:
                    continue
                if is_postgres:
                    ddl = ddl.replace("DEFAULT 0", "DEFAULT FALSE").replace("DEFAULT 1", "DEFAULT TRUE")
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db():
    """Create tables if they don't exist. Safe to call on every cold start."""
    from app import models  # noqa: F401 ensure models are registered

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()
