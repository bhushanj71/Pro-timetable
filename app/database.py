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
        ("active_profile", "VARCHAR(16) DEFAULT 'personal' NOT NULL"),
        ("notify_work_responses", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("notify_work_progress", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("notify_work_completion", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("notify_work_deadlines", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("notify_work_community", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("college_id", "VARCHAR(36)"),
        ("department_id", "VARCHAR(36)"),
        ("admin_college_id", "VARCHAR(36)"),
    ],
    "events": [
        ("google_event_id", "VARCHAR(255)"),
        ("faculty", "VARCHAR(255)"),
        ("location_detail", "TEXT"),
        ("location_url", "VARCHAR(512)"),
    ],
    "reminders": [
        ("read_at", "TIMESTAMP"),
        ("dismissed_at", "TIMESTAMP"),
    ],
    "work_notifications": [
        ("actor_id", "VARCHAR(36)"),
        ("assignment_id", "VARCHAR(36)"),
        ("dedupe_key", "VARCHAR(120)"),
        ("pushed_at", "TIMESTAMP"),
    ],
}


# Composite indexes matching what the app actually filters on. Kept beside the
# column migrations so both are applied by the same boot-time pass.
_COMPOSITE_INDEXES = [
    ("ix_users_department", "users", "department_id"),
    ("ix_users_college", "users", "college_id"),
    ("ix_events_user_start", "events", "user_id, start_datetime"),
    ("ix_reminders_due", "reminders", "is_sent, reminder_datetime"),
    ("ix_reminders_user_due", "reminders", "user_id, reminder_datetime"),
]


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

        # create_all builds indexes only for tables it creates, so an index
        # added after a table already exists never appears on a deployed
        # database -- exactly where it is needed. IF NOT EXISTS makes this
        # safe to run on every boot.
        for name, table, columns in _COMPOSITE_INDEXES:
            if table not in existing_tables:
                continue
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))
            except Exception as exc:
                # A missing index is a slow query, not a broken app.
                logger.warning("Could not create index %s: %s", name, exc)


# The college this deployment is for, and the departments it actually has.
# Seeded rather than hardcoded at the point of use: everything downstream
# joins on the ids these rows get, so a second college added by an admin
# behaves identically to this one.
DEFAULT_COLLEGE = {"name": "DYPCoE, Akurdi", "location": "Akurdi, Pune"}
DEFAULT_DEPARTMENTS = [
    "Computer Engineering",
    "Information Technology",
    "Electronics and Telecommunication Engineering",
    "Mechanical Engineering",
    "Civil Engineering",
    "Robotics and Automation",
    "Instrumentation and Control Engineering",
    "Artificial Intelligence and Data Science",
]


def seed_organisation():
    """Create the default college and its departments, once.

    Idempotent by normalised name, so a redeploy does not produce a second
    "DYPCoE, Akurdi", and an admin who renames or archives one of these
    departments does not have it silently recreated on the next boot.
    """
    from app.models import College, Department, OrgStatus, normalise_org_name

    db = SessionLocal()
    try:
        key = normalise_org_name(DEFAULT_COLLEGE["name"])
        college = db.query(College).filter(College.normalised_name == key).first()
        if not college:
            college = College(
                name=DEFAULT_COLLEGE["name"],
                normalised_name=key,
                location=DEFAULT_COLLEGE["location"],
                status=OrgStatus.ACTIVE.value,
            )
            db.add(college)
            db.flush()

        existing = {d.normalised_name for d in
                    db.query(Department).filter(Department.college_id == college.id).all()}
        for name in DEFAULT_DEPARTMENTS:
            dept_key = normalise_org_name(name)
            if dept_key in existing:
                continue
            db.add(Department(
                college_id=college.id, name=name,
                normalised_name=dept_key, status=OrgStatus.ACTIVE.value,
            ))
        db.commit()
    except Exception:
        db.rollback()
        # A seed failure must not stop the app booting; the admin panel can
        # create these by hand and /api/health still answers.
        logger.exception("Could not seed the default organisation")
    finally:
        db.close()


def link_existing_users():
    """Attach accounts that already typed a college or department name.

    Existing users predate these tables and have free text instead. Where that
    text resolves to a real department, the link is made for them so they are
    not asked to re-enter what they already told us. Where it does not, the
    fields stay empty and Work asks -- never a guess, because being filed
    under the wrong department is worse than being asked.
    """
    from app.models import College, Department, User, normalise_org_name

    db = SessionLocal()
    try:
        candidates = db.query(User).filter(
            User.college_id.is_(None) | User.department_id.is_(None)
        ).all()
        if not candidates:
            return

        colleges = {c.normalised_name: c for c in db.query(College).all()}
        depts: dict[tuple[str, str], Department] = {
            (d.college_id, d.normalised_name): d for d in db.query(Department).all()
        }

        linked = 0
        for user in candidates:
            college = colleges.get(normalise_org_name(user.college or ""))
            if not college:
                continue
            if not user.college_id:
                user.college_id = college.id
            if not user.department_id and user.department:
                dept = depts.get((college.id, normalise_org_name(user.department)))
                if dept:
                    user.department_id = dept.id
            linked += 1
        if linked:
            db.commit()
            logger.info("Linked %s existing account(s) to a college", linked)
    except Exception:
        db.rollback()
        logger.exception("Could not link existing users to the organisation")
    finally:
        db.close()


def init_db():
    """Create tables if they don't exist. Safe to call on every cold start."""
    from app import models  # noqa: F401 ensure models are registered

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()
    seed_organisation()
    link_existing_users()
