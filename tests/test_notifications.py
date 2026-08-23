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
    assert any(n["title"] == "Past due" for n in notifs["items"])


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


# --- Onboarding ------------------------------------------------------------

def test_onboarding_status_reports_what_is_missing(auth_client):
    body = auth_client.get("/api/onboarding/status").json()
    assert body["completed"] is False
    assert body["needs_setup"] is True, "a fresh account has no devices and no calendar"
    assert body["push"]["devices"] == 0
    assert body["email"]["address"] == "test@example.com"
    assert body["calendar_feed_url"].endswith(".ics")


def test_completing_onboarding_stops_the_prompt(auth_client):
    assert auth_client.post("/api/onboarding/complete").json()["ok"] is True
    body = auth_client.get("/api/onboarding/status").json()
    assert body["completed"] is True
    assert body["needs_setup"] is False, "the prompt must not reappear after being dismissed"


def test_registering_a_device_satisfies_the_push_step(auth_client):
    auth_client.post("/api/push/subscribe", json=_sub_payload("https://fcm.googleapis.com/fcm/send/onb1"))
    assert auth_client.get("/api/onboarding/status").json()["push"]["devices"] == 1


def test_onboarding_status_requires_auth(client):
    assert client.get("/api/onboarding/status").status_code == 401


# --- PWA installability ----------------------------------------------------

def test_service_worker_is_served_from_root_with_full_scope(client):
    """A worker under /static could only control /static, so it must be at /
    with the Service-Worker-Allowed header."""
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert resp.headers.get("service-worker-allowed") == "/"


def test_service_worker_has_a_fetch_handler(client):
    """Chrome will not fire beforeinstallprompt without one, so there would be
    no way to add the app to the home screen."""
    body = client.get("/sw.js").text
    assert 'addEventListener("fetch"' in body
    assert 'addEventListener("push"' in body


def test_service_worker_never_caches_api_responses(client):
    """A cached timetable would show stale classes."""
    body = client.get("/sw.js").text
    assert '/api/' in body and "return;" in body


def test_manifest_meets_install_criteria(client):
    resp = client.get("/manifest.json")
    assert resp.status_code == 200
    m = resp.json()
    assert m["display"] == "standalone"
    assert m["name"] and m["short_name"] and m["start_url"]
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes, "install prompts require 192 and 512 icons"


def test_onboarding_leads_with_install_not_calendar(auth_client):
    body = auth_client.get("/dashboard").text
    assert 'id="onb-install"' in body
    assert 'id="onb-ios-help"' in body
    assert "onb-cal-btn" not in body, "external calendar step should be gone"


# --- Notification read state ----------------------------------------------

def _deliver_a_reminder(client, title="Bell test"):
    client.post("/api/reminders", json={
        "title": title, "reminder_datetime": "2020-01-01T09:00:00Z", "reminder_type": "in_app"})
    client.post("/api/cron/process-reminders")


def test_bell_badge_counts_unread_and_clears_once_read(auth_client):
    """The badge previously counted delivered reminders forever, so it could
    never be reset by reading them."""
    _deliver_a_reminder(auth_client)

    before = auth_client.get("/api/reminders/notifications").json()
    assert before["unread"] >= 1
    assert all(not n["is_read"] for n in before["items"])

    auth_client.post("/api/reminders/notifications/read")

    after = auth_client.get("/api/reminders/notifications").json()
    assert after["unread"] == 0, "badge must clear after the bell is opened"
    assert all(n["is_read"] for n in after["items"])
    assert len(after["items"]) == len(before["items"]), "history should remain visible"


def test_a_new_reminder_makes_the_badge_reappear(auth_client):
    _deliver_a_reminder(auth_client, "First")
    auth_client.post("/api/reminders/notifications/read")
    assert auth_client.get("/api/reminders/notifications").json()["unread"] == 0

    _deliver_a_reminder(auth_client, "Second")
    assert auth_client.get("/api/reminders/notifications").json()["unread"] == 1


def test_marking_read_is_scoped_to_the_user(client):
    client.post("/api/auth/register", json={"name": "A", "email": "bell_a@example.com", "password": "password123"})
    _deliver_a_reminder(client, "A's reminder")
    client.post("/api/auth/logout")

    client.post("/api/auth/register", json={"name": "B", "email": "bell_b@example.com", "password": "password123"})
    client.post("/api/reminders/notifications/read")
    client.post("/api/auth/logout")

    client.post("/api/auth/login", json={"email": "bell_a@example.com", "password": "password123"})
    assert client.get("/api/reminders/notifications").json()["unread"] == 1, \
        "another user marking read must not clear this account's badge"


def test_reminder_still_delivers_when_push_is_configured_but_no_device(auth_client, monkeypatch):
    """Zero registered devices is not a failure. Treating it as one meant the
    reminder retried and expired instead of arriving in-app."""
    from app.services import notifier

    monkeypatch.setattr(notifier, "push_configured", lambda: True)

    auth_client.post("/api/reminders", json={
        "title": "No device", "reminder_datetime": "2020-01-01T09:00:00Z", "reminder_type": "in_app"})

    result = auth_client.post("/api/cron/process-reminders").json()
    assert result["sent"] >= 1, "should deliver in-app rather than retry forever"
    assert result["pushes"] == 0

    items = auth_client.get("/api/reminders/notifications").json()["items"]
    assert any(n["title"] == "No device" for n in items)


def test_polling_delivers_overdue_reminders_without_cron(auth_client, db_session):
    """On a host that sleeps, no cron may have run. Opening the bell should
    still surface reminders that are already overdue."""
    from app.models import Reminder

    auth_client.post("/api/reminders", json={
        "title": "Overdue, never cronned", "reminder_datetime": "2020-03-01T09:00:00Z", "reminder_type": "in_app"})

    pending = db_session.query(Reminder).filter(Reminder.is_sent.is_(False)).count()
    assert pending >= 1, "precondition: reminder is undelivered"

    # No cron call — just read the feed.
    body = auth_client.get("/api/reminders/notifications").json()
    assert any(n["title"] == "Overdue, never cronned" for n in body["items"])
    assert body["unread"] >= 1


def test_polling_flush_is_scoped_to_the_requesting_user(client, db_session):
    from app.models import Reminder

    client.post("/api/auth/register", json={"name": "A", "email": "flush_a@example.com", "password": "password123"})
    client.post("/api/reminders", json={
        "title": "A overdue", "reminder_datetime": "2020-03-01T09:00:00Z", "reminder_type": "in_app"})
    client.post("/api/auth/logout")

    client.post("/api/auth/register", json={"name": "B", "email": "flush_b@example.com", "password": "password123"})
    client.get("/api/reminders/notifications")  # B polls

    a_reminder = db_session.query(Reminder).filter(Reminder.title == "A overdue").first()
    assert a_reminder.is_sent is False, "one user's poll must not deliver another's reminders"
