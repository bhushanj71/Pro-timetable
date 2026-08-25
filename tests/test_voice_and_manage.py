"""
The command surface shared by typing and speech: look-up intents, reminder
management, faculty and location, and the endpoints behind Manage.

Voice is not tested here because it has no server side. Speech becomes text
in the browser and then travels the identical path a typed sentence does --
that is the whole design -- so these tests cover both inputs at once.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Event, Reminder
from app.services.ai_service import AIService


def _mk(client, title, hours_ahead=24, **extra):
    start = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    payload = {
        "title": title,
        "event_type": extra.pop("event_type", "lecture"),
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat(),
        **extra,
    }
    return client.post("/api/events?force=true", json=payload).json()[0]


def _ask(client, prompt):
    return client.post("/api/ai/process-prompt", json={"prompt": prompt}).json()


def _run(client, prompt):
    body = _ask(client, prompt)
    if body.get("requires_confirmation"):
        return client.post("/api/ai/confirm", json={"extraction": body["extraction"]}).json()
    return body


# --- Intent routing, with no AI provider configured ------------------------

@pytest.mark.parametrize("prompt,intent", [
    ("What is my next lecture?", "GET_NEXT_CLASS"),
    ("Where do I need to go next?", "SHOW_LOCATION"),
    ("Where is my next lecture?", "SHOW_LOCATION"),
    ("Show the location of my DBMS lab", "SHOW_LOCATION"),
    ("Do I have any schedule conflicts?", "CHECK_CONFLICTS"),
    ("Show all my reminders", "VIEW_REMINDERS"),
    ("Turn off reminders for AI labs", "DELETE_REMINDER"),
    ("Change my DBMS reminder to 30 minutes before", "UPDATE_REMINDER"),
    ("Remind me 15 minutes before every lecture", "UPDATE_REMINDER"),
    # Writes must not be swallowed by the new look-up branches.
    ("Add a DBMS lecture tomorrow at 10 AM in Room 302", "CREATE_EVENT"),
    ("Move my DBMS lecture from 10 AM to 11 AM", "UPDATE_EVENT"),
    ("Delete my Mathematics lecture on Monday", "DELETE_EVENT"),
])
def test_rule_based_parser_routes_the_spec_commands(prompt, intent):
    svc = AIService.__new__(AIService)
    ctx = {"today": "2026-08-25", "weekday": "Tuesday", "timezone": "Asia/Kolkata"}
    assert svc._fallback_rule_based(prompt, ctx)["intent"] == intent


def test_moving_from_one_time_to_another_uses_the_second(auth_client):
    """'from 10 AM to 11 AM' names the current time first. Reading the pair as
    start and end moved the lecture to 10-11, i.e. nowhere at all."""
    _mk(auth_client, "DBMS lecture")
    before = auth_client.get("/api/events").json()[0]["start_datetime"]

    _run(auth_client, "Move my DBMS lecture from 10 AM to 11 AM")

    after = auth_client.get("/api/events").json()[0]["start_datetime"]
    assert after != before


def test_a_literal_null_never_reaches_the_database(auth_client, db_session):
    """Models emit the string "null", which is truthy; it used to be written
    into faculty and location as though the professor had typed it."""
    from app.schemas import AIExtractionResult

    ev = _mk(auth_client, "DBMS lecture", location="Room 302")
    extraction = AIExtractionResult.model_validate({
        "intent": "UPDATE_EVENT",
        "target_event_title": "DBMS lecture",
        "new_faculty": "null",
        "new_location": "null",
        "new_start_time": "11:00",
    })
    auth_client.post("/api/ai/confirm", json={"extraction": extraction.model_dump(mode="json")})

    row = db_session.query(Event).filter(Event.id == ev["id"]).one()
    assert row.faculty is None
    assert row.location == "Room 302", "an absent field must not wipe a real one"


def test_a_delete_still_requires_confirmation(auth_client):
    """Voice runs creates and updates straight through, but a misheard word is
    exactly how a lecture gets deleted by accident, so deletes always ask.
    The browser decides that from these two fields."""
    _mk(auth_client, "DBMS lecture")
    body = _ask(auth_client, "Delete my DBMS lecture")

    assert body["action"] == "delete"
    assert body["requires_confirmation"] is True
    assert body["matches"], "the professor must see exactly what would go"


def test_a_create_is_confirmable_but_carries_everything_needed(auth_client):
    """Voice auto-runs this, so the extraction has to be complete on the first
    pass -- there is no second round trip to fill anything in."""
    body = _ask(auth_client, "Add a DBMS lecture tomorrow at 10 AM in Room 302")
    assert body["requires_confirmation"] is True
    assert body["action"] != "delete"

    result = auth_client.post("/api/ai/confirm", json={"extraction": body["extraction"]}).json()
    assert result["ok"] is True and result["events_created"] == 1

    ev = auth_client.get("/api/events").json()[0]
    assert ev["location"] == "Room 302"


def test_an_unmatched_target_is_reported_not_invented(auth_client):
    """Voice mishears. A command naming something that does not exist has to
    come back as "not found", never as a newly created event."""
    before = len(auth_client.get("/api/events").json())
    body = _ask(auth_client, "Move my Astrophysics seminar to 4 PM")

    assert body["matches"] == []
    assert body["requires_confirmation"] is False
    assert "couldn't find" in body["summary"].lower()
    assert len(auth_client.get("/api/events").json()) == before


# --- Look-ups --------------------------------------------------------------

def test_next_class_and_the_spoken_question_agree(auth_client):
    _mk(auth_client, "Later lecture", hours_ahead=48)
    _mk(auth_client, "Sooner lecture", hours_ahead=3)

    endpoint = auth_client.get("/api/events/next").json()["event"]
    spoken = _ask(auth_client, "What is my next lecture?")

    assert endpoint["title"] == "Sooner lecture"
    assert spoken["matches"][0]["id"] == endpoint["id"], "button and voice must not disagree"


def test_show_location_falls_back_to_the_next_class(auth_client):
    """'Where is my class?' names nothing, so it has to mean the next one."""
    _mk(auth_client, "DBMS lecture", hours_ahead=2, location="Room 302")
    body = _ask(auth_client, "Where is my next lecture?")
    assert body["intent"] == "SHOW_LOCATION"
    assert "Room 302" in body["summary"]


def test_location_without_a_saved_place_says_so(auth_client):
    _mk(auth_client, "Nowhere lecture", hours_ahead=2)
    body = _ask(auth_client, "Where is my next lecture?")
    assert "no location" in body["summary"].lower()


def test_map_url_is_built_from_the_room_when_no_link_was_given(auth_client):
    ev = _mk(auth_client, "DBMS lecture", location="Room 302",
             location_detail="Main Building, DYPCOE")
    d = auth_client.get(f"/api/events/{ev['id']}/location").json()
    assert d["map_url"].startswith("https://www.google.com/maps/")
    assert "Room+302" in d["map_url"]


def test_an_explicit_map_link_wins(auth_client):
    ev = _mk(auth_client, "AI lab", location="Lab 5", location_url="https://maps.example/lab5")
    d = auth_client.get(f"/api/events/{ev['id']}/location").json()
    assert d["map_url"] == "https://maps.example/lab5"


def test_conflicts_report_pairs_not_single_events(auth_client):
    start = datetime.now(timezone.utc) + timedelta(hours=5)
    for title in ("Lecture A", "Lecture B"):
        auth_client.post("/api/events?force=true", json={
            "title": title, "event_type": "lecture",
            "start_datetime": start.isoformat(),
            "end_datetime": (start + timedelta(hours=1)).isoformat()})

    clashes = auth_client.get("/api/events/conflicts").json()["conflicts"]
    assert len(clashes) == 1
    assert {clashes[0]["a"]["title"], clashes[0]["b"]["title"]} == {"Lecture A", "Lecture B"}


def test_search_combines_its_filters(auth_client):
    _mk(auth_client, "DBMS lecture", faculty="Prof. Sharma", location="Room 302")
    _mk(auth_client, "DBMS lab", event_type="lab", faculty="Prof. Patil", location="Lab 4")

    by_faculty = auth_client.get("/api/events/search?faculty=Patil").json()
    assert [e["title"] for e in by_faculty["events"]] == ["DBMS lab"]

    by_room = auth_client.get("/api/events/search?location=Room 302").json()
    assert [e["title"] for e in by_room["events"]] == ["DBMS lecture"]

    both = auth_client.get("/api/events/search?q=DBMS&event_type=lab").json()
    assert both["count"] == 1


def test_search_is_scoped_to_the_professors_own_schedule(client):
    client.post("/api/auth/register", json={"name": "A", "email": "s_a@example.com", "password": "password123"})
    _mk(client, "Private lecture", faculty="Prof. Secret")
    client.post("/api/auth/logout")

    client.post("/api/auth/register", json={"name": "B", "email": "s_b@example.com", "password": "password123"})
    assert client.get("/api/events/search?faculty=Secret").json()["count"] == 0


# --- Reminder synchronisation ----------------------------------------------

def test_moving_an_event_keeps_each_reminder_at_its_own_lead(auth_client, db_session):
    """A professor with a 30-minute and a 5-minute alert must still have both,
    at those distances, after the lecture moves. The old code recomputed from
    one default and silently collapsed them onto the same time."""
    ev = _mk(auth_client, "DBMS lecture", hours_ahead=48)
    event_id = ev["id"]

    before = sorted(
        int((datetime.fromisoformat(ev["start_datetime"]) - datetime.fromisoformat(r["reminder_datetime"])).total_seconds() // 60)
        for r in auth_client.get("/api/reminders").json() if r["event_id"] == event_id
    )
    assert before == [5, 30]

    new_start = datetime.now(timezone.utc) + timedelta(hours=72)
    auth_client.put(f"/api/events/{event_id}?force=true", json={
        "start_datetime": new_start.isoformat(),
        "end_datetime": (new_start + timedelta(hours=1)).isoformat()})

    row = db_session.query(Event).filter(Event.id == event_id).one()
    leads = sorted(
        int((row.start_datetime - r.reminder_datetime).total_seconds() // 60)
        for r in row.reminders if not r.is_sent
    )
    assert leads == [5, 30], "leads must survive the move"


def test_setting_a_reminder_lead_by_command(auth_client, db_session):
    ev = _mk(auth_client, "DBMS lecture", hours_ahead=48)
    _run(auth_client, "Change my DBMS reminder to 30 minutes before")

    row = db_session.query(Event).filter(Event.id == ev["id"]).one()
    leads = sorted(int((row.start_datetime - r.reminder_datetime).total_seconds() // 60)
                   for r in row.reminders if not r.is_sent)
    assert 30 in leads


def test_turning_reminders_off_by_command(auth_client, db_session):
    ev = _mk(auth_client, "AI lab", event_type="lab", hours_ahead=48)
    assert db_session.query(Reminder).filter(Reminder.event_id == ev["id"]).count() > 0

    _run(auth_client, "Turn off reminders for AI")

    remaining = db_session.query(Reminder).filter(
        Reminder.event_id == ev["id"], Reminder.is_sent.is_(False)).count()
    assert remaining == 0


def test_deleting_an_event_takes_its_reminders_with_it(auth_client, db_session):
    ev = _mk(auth_client, "Doomed lecture", hours_ahead=48)
    assert db_session.query(Reminder).filter(Reminder.event_id == ev["id"]).count() > 0

    auth_client.delete(f"/api/events/{ev['id']}")
    assert db_session.query(Reminder).filter(Reminder.event_id == ev["id"]).count() == 0


# --- Notification content ---------------------------------------------------

def test_a_lecture_notification_names_faculty_and_room(auth_client, db_session):
    """A notification that only says "starts in 15 minutes" makes the professor
    open the app to find out where to walk."""
    from app.services.reminder_service import _describe_when, notification_title

    _mk(auth_client, "DBMS lecture", hours_ahead=2, faculty="Prof. Sharma", location="Room 302")
    reminder = db_session.query(Reminder).filter(Reminder.event_id.isnot(None)).first()

    title = notification_title(reminder)
    body, location = _describe_when(reminder, "Asia/Kolkata")

    assert "Lecture" in title
    assert "Prof. Sharma" in body
    assert "Room 302" in body
    assert location == "Room 302"


def test_a_lab_is_labelled_differently_from_a_lecture(auth_client, db_session):
    from app.services.reminder_service import notification_title

    _mk(auth_client, "AI lab", event_type="lab", hours_ahead=3, location="AI Lab 2")
    reminder = (
        db_session.query(Reminder)
        .join(Event, Reminder.event_id == Event.id)
        .filter(Event.event_type == "lab")
        .first()
    )
    assert "Lab" in notification_title(reminder)
