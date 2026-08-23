"""
Shared pytest fixtures: an isolated SQLite database per test, and a
TestClient wired to it.

The database lives in a temp file rather than `:memory:` because each
in-memory SQLite *connection* is its own separate database. The app engine
and any session a test opens directly must see the same tables, so a real
file is the simplest way to guarantee one shared database.
"""
import os
import tempfile
from pathlib import Path

_TEST_DB_PATH = Path(tempfile.gettempdir()) / "profschedule_test.db"
try:
    _TEST_DB_PATH.unlink(missing_ok=True)
except OSError:
    # Already open (e.g. this module got imported twice under different
    # names); the per-test drop/create below still gives a clean slate.
    pass

# Force test values so a developer's local .env can't change test behavior.
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["AI_PROVIDER"] = "none"
os.environ["AI_API_KEY"] = ""
os.environ["CRON_SECRET"] = ""
# Pin every outbound-delivery credential too, so a developer's populated .env
# can't make the suite attempt real emails or push requests.
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["VAPID_PUBLIC_KEY"] = ""
os.environ["VAPID_PRIVATE_KEY"] = ""
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = ""
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

# Import after the env is pinned so the app builds its engine against the test DB.
from app.database import Base, engine, get_db
from app.main import app

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
def db_session():
    """Direct DB access for tests that need to set up state the API won't expose
    (e.g. promoting the very first admin)."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_client(client):
    client.post(
        "/api/auth/register",
        json={"name": "Dr. Test", "email": "test@example.com", "password": "password123", "timezone": "Asia/Kolkata"},
    )
    return client
