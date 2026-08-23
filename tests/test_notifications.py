"""
Multi-channel reminder delivery: push subscriptions, the calendar feed, and
per-user notification preferences.
"""
from datetime import datetime, timedelta, timezone

from app.models import PushSubscription, Reminder, User


def _sub_payload(endpoint="https://fcm.googleapis.com/fcm/send/abc123"):
    return {"endpoint": endpoint, "keys": {"p256dh": "BKxQ" + "a" * 40, "auth": "sEcRe7" + "b" * 10}}


# --- Push subscriptions ----------------------------------------------------

def test_push_subscribe_requires_auth(client):
    assert client.post("/api/push/subscribe", json=_sub_payload()).status_code == 401


def test_push_subscribe_and_unsubscribe(auth_client, db_session):
    assert auth_client.post("/api/push/subscribe", json=_sub_payload()).status_code == 201
    assert db_session.query(PushSubscription).count() == 1

    auth_client.post("/api/push/unsubscribe", json={"endpoint": _sub_payload()["endpoint"]})
    assert db_session.query(PushSubscription).count() == 0


def test_resubscribing_same_endpoint_does_not_duplicate(auth_client, db_session):
    auth_client.post("/api/push/subscribe", json=_sub_payload())
    resp = auth_client.post("/api/push/subscribe", json=_sub_payload())
    assert resp.json()["updated"] is True
    assert db_session.query(PushSubscription).count() == 1, "same device must not create a second row"


def test_public_key_endpoint_reports_disabled_without_config(client):
    body = client.get("/api/push/public-key").json()
    assert body["enabled"] is False


# --- Calendar feed ---------------------------------------------------------

def test_calendar_feed_is_public_but_token_gated(auth_client, db_session):
    url = auth_client.get("/api/calendar-feed-url").json()["url"]
    token = url.rsplit("/", 1)[-1].replace(".ics", "")

    auth_client.post("/api/events", json={
        "title": "Feed Lecture", "start_datetime": "2026-09-14T09:00:00Z",
        "end_datetime": "2026-09-14T10:00:00Z", "location": "Room 12"})

    # No cookie: a calendar client can't send one.
    auth_client.cookies.clear()
    resp = auth_client.get(f"/api/calendar/{token}.ics")
    assert resp.status_code == 200
    assert "text/calendar" in resp.headers["content-type"]
    body = resp.text
    assert "BEGIN:VCALENDAR" in body and "Feed Lecture" in body
    assert "BEGIN:VALARM" in body, "events need an alarm so the phone raises its own reminder"


def test_calendar_feed_rejects_unknown_token(client):
    assert client.get("/api/calendar/deadbeef.ics").status_code == 404


def test_rotating_token_invalidates_the_old_link(auth_client):
    old = auth_client.get("/api/calendar-feed-url").json()["url"]
    old_token = old.rsplit("/", 1)[-1].replace(".ics", "")

    auth_client.post("/api/calendar-feed-url/rotate")
    new_token = auth_client.get("/api/calendar-feed-url").json()["url"].rsplit("/", 1)[-1].replace(".ics", "")

    assert new_token != old_token
    assert auth_client.get(f"/api/calendar/{old_token}.ics").status_code == 404
    assert auth_client.get(f"/api/calendar/{new_token}.ics").status_code == 200


# --- Preferences + delivery -----------------------------------------------

def test_notification_preferences_persist(auth_client):
    resp = auth_client.put("/api/auth/me", json={"notify_email": False, "notify_push": False})
    assert resp.status_code == 200
    assert resp.json()["notify_email"] is False
    assert auth_client.get("/api/auth/me").json()["notify_push"] is False


def test_due_reminder_is_delivered_in_app_without_channels_configured(auth_client, db_session):
    """With no SMTP/VAPID configured, delivery must still succeed as in-app
    rather than getting stuck retrying."""
    auth_client.post("/api/reminders", json={
        "title": "Past due", "reminder_datetime": "2020-01-01T09:00:00Z", "reminder_type": "in_app"})

    result = auth_client.post("/api/cron/process-reminders").json()
    assert result["sent"] >= 1
    assert result["emails"] == 0 and result["pushes"] == 0

    notifs = auth_client.get("/api/reminders/notifications").json()
    assert any(n["title"] == "Past due" for n in notifs)


def test_reminder_delivery_is_idempotent(auth_client):
    auth_client.post("/api/reminders", json={
        "title": "Once only", "reminder_datetime": "2020-01-01T09:00:00Z", "reminder_type": "in_app"})

    first = auth_client.post("/api/cron/process-reminders").json()
    second = auth_client.post("/api/cron/process-reminders").json()
    assert first["sent"] >= 1
    assert second["processed"] == 0, "an already-delivered reminder must not be re-sent"


def test_test_notification_endpoint_reports_channel_state(auth_client):
    body = auth_client.post("/api/notifications/test").json()
    assert "email" in body and "push" in body
    assert body["devices"] == 0


# --- Reminders must be created automatically ------------------------------

def test_creating_an_event_creates_a_reminder_by_default(auth_client, db_session):
    """The regression behind 'I scheduled a meeting and got no notification':
    no form sends reminder_minutes, so nothing was ever scheduled to deliver."""
    future = datetime.now(timezone.utc) + timedelta(days=2)
    resp = auth_client.post("/api/events", json={
        "title": "Department Meeting",
        "start_datetime": future.isoformat(),
        "end_datetime": (future + timedelta(hours=1)).isoformat(),
    })
    assert resp.status_code == 201
    event_id = resp.json()[0]["id"]

    reminders = db_session.query(Reminder).filter(Reminder.event_id == event_id).all()
    assert len(reminders) == 1, "an event with no explicit reminder must still use the profile default"

    # Default lead time is 30 minutes before the event.
    delta = future - reminders[0].reminder_datetime.replace(tzinfo=timezone.utc)
    assert abs(delta - timedelta(minutes=30)) < timedelta(seconds=5)


def test_explicit_empty_list_means_no_reminder(auth_client, db_session):
    future = datetime.now(timezone.utc) + timedelta(days=2)
    resp = auth_client.post("/api/events", json={
        "title": "Quiet Event",
        "start_datetime": future.isoformat(),
        "end_datetime": (future + timedelta(hours=1)).isoformat(),
        "reminder_minutes": [],
    })
    event_id = resp.json()[0]["id"]
    assert db_session.query(Reminder).filter(Reminder.event_id == event_id).count() == 0


def test_past_occurrences_do_not_create_overdue_reminders(auth_client, db_session):
    """A recurring series starting in the past must not dump a pile of
    already-due reminders that all fire at once."""
    past = datetime.now(timezone.utc) - timedelta(days=14)
    resp = auth_client.post("/api/events?force=true", json={
        "title": "Long Running Class",
        "start_datetime": past.isoformat(),
        "end_datetime": (past + timedelta(hours=1)).isoformat(),
        "recurrence_rule": "weekly:MON",
    })
    assert resp.status_code == 201

    ids = [e["id"] for e in resp.json()]
    reminders = db_session.query(Reminder).filter(Reminder.event_id.in_(ids)).all()
    now = datetime.now(timezone.utc)
    overdue = [r for r in reminders if r.reminder_datetime.replace(tzinfo=timezone.utc) <= now]
    assert not overdue, f"{len(overdue)} reminders were created already overdue"


def test_user_default_lead_time_is_respected(auth_client, db_session):
    auth_client.put("/api/auth/me", json={"default_reminder_minutes": 60})
    future = datetime.now(timezone.utc) + timedelta(days=3)
    resp = auth_client.post("/api/events", json={
        "title": "Custom Lead",
        "start_datetime": future.isoformat(),
        "end_datetime": (future + timedelta(hours=1)).isoformat(),
    })
    r = db_session.query(Reminder).filter(Reminder.event_id == resp.json()[0]["id"]).first()
    delta = future - r.reminder_datetime.replace(tzinfo=timezone.utc)
    assert abs(delta - timedelta(minutes=60)) < timedelta(seconds=5)
