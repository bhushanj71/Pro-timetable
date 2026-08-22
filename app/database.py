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


def init_db():
    """Create tables if they don't exist. Safe to call on every cold start."""
    from app import models  # noqa: F401 ensure models are registered

    Base.metadata.create_all(bind=engine)
