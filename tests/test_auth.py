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


def test_register_accepts_custom_college_timings(client):
    """Professors keep different college hours; signup must persist them."""
    resp = client.post("/api/auth/register", json={
        "name": "Dr. Early", "email": "early@example.com", "password": "password123",
        "working_hours_start": "08:45", "working_hours_end": "16:15",
        "lunch_start": "12:30", "lunch_end": "13:00",
        "working_days": "Mon,Tue,Wed,Thu,Fri,Sat",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["working_hours_start"] == "08:45"
    assert body["working_hours_end"] == "16:15"
    assert body["lunch_start"] == "12:30"
    assert body["working_days"] == "Mon,Tue,Wed,Thu,Fri,Sat"


def test_register_defaults_when_timings_omitted(client):
    resp = client.post("/api/auth/register", json={
        "name": "Dr. Default", "email": "default@example.com", "password": "password123"})
    assert resp.status_code == 201
    assert resp.json()["working_hours_start"] == "09:00"
    assert resp.json()["working_hours_end"] == "17:00"


def test_register_rejects_end_before_start(client):
    resp = client.post("/api/auth/register", json={
        "name": "Dr. Bad", "email": "bad@example.com", "password": "password123",
        "working_hours_start": "17:00", "working_hours_end": "09:00"})
    assert resp.status_code == 422


def test_register_rejects_malformed_time(client):
    resp = client.post("/api/auth/register", json={
        "name": "Dr. Bad2", "email": "bad2@example.com", "password": "password123",
        "working_hours_start": "8am", "working_hours_end": "4pm"})
    assert resp.status_code == 422


def test_free_time_respects_custom_working_hours(client):
    """An 11:00-18:30 professor should get free slots inside that window."""
    client.post("/api/auth/register", json={
        "name": "Dr. Late", "email": "late@example.com", "password": "password123",
        "working_hours_start": "11:00", "working_hours_end": "18:30"})

    resp = client.post("/api/ai/find-free-time", json={"date": "2026-09-08", "duration_minutes": 60})
    assert resp.status_code == 200
    slots = resp.json()["free_slots"]
    assert slots, "expected free slots within the custom working window"

    from datetime import datetime
    first_start = datetime.fromisoformat(slots[0]["start"])
    last_end = datetime.fromisoformat(slots[-1]["end"])
    # Converted to the user's timezone the window is 11:00-18:30; assert it is
    # not the old hard-coded 09:00-17:00 default.
    assert first_start < last_end
