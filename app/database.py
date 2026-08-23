"""
SQLAlchemy engine/session setup. Works with SQLite locally and
Postgres (Vercel Postgres / Neon / Supabase) in production via DATABASE_URL.
"""
import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SQLITE_FALLBACK = "sqlite:///./profschedule.db"

url = (settings.DATABASE_URL or "").strip()

# A declared-but-empty DATABASE_URL (easy to end up with on Render, where the
# blueprint creates the key and you fill the value in later) otherwise crashes
# at import with an opaque "Could not parse SQLAlchemy URL from string ''".
if not url:
    logger.warning(
        "DATABASE_URL is not set; falling back to local SQLite (%s). "
        "On a host with an ephemeral disk this loses all data on redeploy.",
        SQLITE_FALLBACK,
    )
    url = SQLITE_FALLBACK

# Catch a connection string pasted straight from a provider's docs with the
# password placeholder still in it — the failure would otherwise surface much
# later as a confusing auth/DNS error.
for placeholder in ("[YOUR-PASSWORD]", "YOUR-PASSWORD", "<password>", "[PASSWORD]"):
    if placeholder in url:
        raise RuntimeError(
            f"DATABASE_URL still contains the placeholder {placeholder!r}. "
            "Replace it with your actual database password."
        )

connect_args = {}
if url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
else:
    # Without an explicit timeout an unreachable host (a firewalled port, or
    # Supabase's IPv6-only direct endpoint seen from an IPv4-only host) hangs
    # the connection attempt for minutes, which reads as a stuck deploy rather
    # than a clear error.
    connect_args = {"connect_timeout": 10}

# Vercel Postgres / Neon URLs sometimes use postgres:// which SQLAlchemy 2.x rejects
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql+psycopg://", 1)
elif url.startswith("postgresql://") and "+psycopg" not in url:
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)

# Supabase's direct endpoint resolves to IPv6 only. Render (and most IPv4-only
# hosts) cannot reach it, and the symptom is a silent hang at startup.
if "db." in url and ".supabase.co" in url:
    logger.warning(
        "DATABASE_URL uses Supabase's direct endpoint (db.*.supabase.co), which is "
        "IPv6-only and unreachable from IPv4-only hosts such as Render. If startup "
        "hangs or the database is unreachable, switch to the pooler host "
        "(aws-<region>.pooler.supabase.com) with username postgres.<project-ref>."
    )

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
