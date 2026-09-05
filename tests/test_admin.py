"""
Admin authorization boundaries and user-management behaviour.

The most important property under test: a regular professor must never be
able to reach an admin endpoint, and the system must never end up with zero
administrators.
"""


from app.models import User


def _make_admin(client, db_session, email="admin@example.com", password="adminpass123"):
    """Register a user, then promote them directly in the DB.

    Promotion has to bypass the API on purpose: there is no self-service way
    to become an admin, which is the property the rest of these tests rely on.
    """
    client.post("/api/auth/register", json={"name": "Admin", "email": email, "password": password, "accepted_terms": True})
    user = db_session.query(User).filter(User.email == email).first()
    user.is_admin = True
    db_session.commit()
    return client


def test_regular_user_cannot_access_admin_endpoints(auth_client):
    for path in ("/api/admin/stats", "/api/admin/users"):
        resp = auth_client.get(path)
        assert resp.status_code == 403, f"{path} should be forbidden for non-admins"


def test_anonymous_cannot_access_admin_endpoints(client):
    assert client.get("/api/admin/users").status_code == 401


def test_admin_can_list_users_and_see_stats(client, db_session):
    _make_admin(client, db_session)
    resp = client.get("/api/admin/users")
    assert resp.status_code == 200
    assert any(u["email"] == "admin@example.com" for u in resp.json())

    stats = client.get("/api/admin/stats")
    assert stats.status_code == 200
    assert stats.json()["admin_users"] == 1


def test_admin_can_create_and_delete_user(client, db_session):
    _make_admin(client, db_session)
    resp = client.post(
        "/api/admin/users",
        json={"name": "Prof B", "email": "b@example.com", "password": "password123", "is_admin": False},
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    assert client.delete(f"/api/admin/users/{user_id}").status_code == 204


def test_admin_cannot_delete_own_account(client, db_session):
    _make_admin(client, db_session)
    me = [u for u in client.get("/api/admin/users").json() if u["email"] == "admin@example.com"][0]
    resp = client.delete(f"/api/admin/users/{me['id']}")
    assert resp.status_code == 400


def test_cannot_remove_last_administrator(client, db_session):
    _make_admin(client, db_session)
    me = [u for u in client.get("/api/admin/users").json() if u["email"] == "admin@example.com"][0]
    resp = client.put(f"/api/admin/users/{me['id']}", json={"is_admin": False})
    assert resp.status_code == 400


def test_admin_can_reset_another_users_password(client, db_session):
    _make_admin(client, db_session)
    created = client.post(
        "/api/admin/users",
        json={"name": "Prof C", "email": "c@example.com", "password": "password123"},
    ).json()

    resp = client.post(f"/api/admin/users/{created['id']}/reset-password", json={"new_password": "brandnew456"})
    assert resp.status_code == 200

    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "c@example.com", "password": "brandnew456"}).status_code == 200


def test_deactivated_user_cannot_log_in(client, db_session):
    _make_admin(client, db_session)
    created = client.post(
        "/api/admin/users",
        json={"name": "Prof D", "email": "d@example.com", "password": "password123"},
    ).json()
    client.put(f"/api/admin/users/{created['id']}", json={"is_active": False})
    client.post("/api/auth/logout")

    resp = client.post("/api/auth/login", json={"email": "d@example.com", "password": "password123"})
    assert resp.status_code == 403


def test_duplicate_email_rejected_on_admin_create(client, db_session):
    _make_admin(client, db_session)
    payload = {"name": "Dup", "email": "dup@example.com", "password": "password123"}
    assert client.post("/api/admin/users", json=payload).status_code == 201
    assert client.post("/api/admin/users", json=payload).status_code == 400


def _bootstrap(db_session, email, password=None):
    """Run the bootstrap with patched settings, as a deploy would."""
    from app.services import bootstrap as bs

    original = (bs.settings.BOOTSTRAP_ADMIN_EMAIL, bs.settings.BOOTSTRAP_ADMIN_PASSWORD)
    bs.settings.BOOTSTRAP_ADMIN_EMAIL = email
    bs.settings.BOOTSTRAP_ADMIN_PASSWORD = password
    try:
        bs.bootstrap_admin(db_session)
    finally:
        bs.settings.BOOTSTRAP_ADMIN_EMAIL, bs.settings.BOOTSTRAP_ADMIN_PASSWORD = original


def test_bootstrap_creates_admin_when_password_given(client, db_session):
    _bootstrap(db_session, "boot@example.com", "bootpass123")

    user = db_session.query(User).filter(User.email == "boot@example.com").first()
    assert user is not None and user.is_admin

    assert client.post("/api/auth/login", json={"email": "boot@example.com", "password": "bootpass123"}).status_code == 200
    assert client.get("/api/admin/stats").status_code == 200


def test_bootstrap_promotes_existing_account(client, db_session):
    client.post("/api/auth/register", json={"name": "Prof", "email": "promote@example.com", "password": "password123", "accepted_terms": True})
    assert client.get("/api/admin/stats").status_code == 403, "should not be admin yet"

    _bootstrap(db_session, "promote@example.com")

    # Same password as before — bootstrap must never overwrite it.
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "promote@example.com", "password": "password123"}).status_code == 200
    assert client.get("/api/admin/stats").status_code == 200


def test_bootstrap_without_password_does_not_create_account(client, db_session):
    _bootstrap(db_session, "ghost@example.com")
    assert db_session.query(User).filter(User.email == "ghost@example.com").first() is None


def test_bootstrap_is_idempotent(client, db_session):
    _bootstrap(db_session, "twice@example.com", "bootpass123")
    _bootstrap(db_session, "twice@example.com", "bootpass123")
    assert db_session.query(User).filter(User.email == "twice@example.com").count() == 1


def test_bootstrap_noop_when_unset(client, db_session):
    before = db_session.query(User).count()
    _bootstrap(db_session, None)
    assert db_session.query(User).count() == before
