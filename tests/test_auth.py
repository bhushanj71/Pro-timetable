def test_register_creates_user_and_sets_cookie(client):
    resp = client.post(
        "/api/auth/register",
        json={"name": "Dr. Jane", "email": "jane@example.com", "password": "password123"},
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "jane@example.com"
    assert "access_token" in resp.cookies


def test_register_duplicate_email_rejected(client):
    payload = {"name": "Dr. Jane", "email": "jane@example.com", "password": "password123"}
    client.post("/api/auth/register", json=payload)
    resp = client.post("/api/auth/register", json=payload)
    assert resp.status_code == 400


def test_login_with_wrong_password_fails(client):
    client.post("/api/auth/register", json={"name": "Dr. Jane", "email": "jane@example.com", "password": "password123"})
    resp = client.post("/api/auth/login", json={"email": "jane@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_me_requires_authentication(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user(auth_client):
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


def test_events_are_isolated_per_user(client):
    client.post("/api/auth/register", json={"name": "A", "email": "a@example.com", "password": "password123"})
    client.post(
        "/api/events",
        json={
            "title": "A's Lecture",
            "start_datetime": "2026-09-01T10:00:00Z",
            "end_datetime": "2026-09-01T11:00:00Z",
        },
    )
    client.post("/api/auth/logout")

    client.post("/api/auth/register", json={"name": "B", "email": "b@example.com", "password": "password123"})
    resp = client.get("/api/events")
    assert resp.status_code == 200
    assert resp.json() == []
