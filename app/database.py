"""
SQLAlchemy engine/session setup. Works with SQLite locally and
Postgres (Vercel Postgres / Neon / Supabase) in production via DATABASE_URL.
"""
import logging
import uuid

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
# password placeholder still in it. This must NOT raise: a config mistake that
# crashes at import prevents the process from starting at all, which the host
# can only report as an opaque "deploy failed". Fall back to SQLite so the app
# boots and /api/health can state the real problem.
CONFIG_ERROR: str | None = None

for placeholder in ("[YOUR-PASSWORD]", "YOUR-PASSWORD", "<password>", "[PASSWORD]"):
    if placeholder in url:
        CONFIG_ERROR = (
            f"DATABASE_URL still contains the placeholder {placeholder!r}. "
            "Replace it with your actual database password."
        )
        logger.error("%s Falling back to local SQLite.", CONFIG_ERROR)
        url = SQLITE_FALLBACK
        break

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

def describe_connection() -> str:
    """Human-readable connection target with the password stripped.

    Exposed on /api/health only while the database is unreachable, so a
    misconfigured host can be identified without shell access to the host —
    and nothing is disclosed once the connection is healthy.
    """
    import re

    try:
        from sqlalchemy.engine import make_url

        u = make_url(url)
        host = u.host or "(no host)"

        # A password containing unencoded reserved characters (@ : / # ? %)
        # makes the parser mis-split the URI, spilling password bytes into the
        # host. Never echo that back — report the misconfiguration instead.
        if not re.fullmatch(r"[A-Za-z0-9._-]+", host):
            return (
                "(malformed DATABASE_URL: the password almost certainly contains "
                "special characters that must be percent-encoded, e.g. @ -> %40, "
                "# -> %23, $ -> %24, %% -> %25, ! -> %21)"
            )

        return f"{u.drivername}://{u.username or '(no user)'}:***@{host}:{u.port or '(default port)'}/{u.database or ''}"
    except Exception:
        return "(could not parse DATABASE_URL)"


def _build_engine(target_url: str):
    return create_engine(target_url, connect_args=connect_args, pool_pre_ping=True)


try:
    engine = _build_engine(url)
except Exception as exc:  # malformed URL, unknown driver, missing dialect...
    # Same rationale as the placeholder guard: never crash at import over a
    # config value. Boot on SQLite and let /api/health explain.
    CONFIG_ERROR = f"Invalid DATABASE_URL ({type(exc).__name__}: {exc}). Falling back to local SQLite."
    logger.error(CONFIG_ERROR)
    connect_args = {"check_same_thread": False}
    engine = _build_engine(SQLITE_FALLBACK)
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
        ("notify_email", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("notify_push", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("calendar_token", "VARCHAR(64)"),
        ("google_id", "VARCHAR(64)"),
        ("avatar_url", "VARCHAR(512)"),
        ("google_refresh_token", "TEXT"),
        ("google_calendar_id", "VARCHAR(255)"),
        ("google_sync_enabled", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("onboarding_completed", "BOOLEAN DEFAULT 0 NOT NULL"),
    ],
    "events": [
        ("google_event_id", "VARCHAR(255)"),
    ],
    "reminders": [
        ("read_at", "TIMESTAMP"),
        ("dismissed_at", "TIMESTAMP"),
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

        # Columns whose value must be unique per row can't come from a DDL
        # default, so backfill them explicitly for pre-existing accounts.
        if "users" in existing_tables:
            missing = conn.execute(
                text("SELECT id FROM users WHERE calendar_token IS NULL OR calendar_token = ''")
            ).fetchall()
            for (user_id,) in missing:
                conn.execute(
                    text("UPDATE users SET calendar_token = :tok WHERE id = :uid"),
                    {"tok": uuid.uuid4().hex, "uid": user_id},
                )


def init_db():
    """Create tables if they don't exist. Safe to call on every cold start."""
    from app import models  # noqa: F401 ensure models are registered

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()
