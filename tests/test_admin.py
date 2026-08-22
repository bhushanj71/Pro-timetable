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
    client.post("/api/auth/register", json={"name": "Admin", "email": email, "password": password})
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
