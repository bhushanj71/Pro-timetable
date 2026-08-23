"""
Update and delete via the AI prompt.

The regression that motivated these: confirm_extraction ignored intent and
always created, so "cancel my meeting" added a meeting instead of removing
one — the exact opposite of the request.
"""
from datetime import datetime, timedelta, timezone

from app.models import Event


def _make_event(client, title, days_ahead=2, hour=10, recurrence=None):
    start = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    body = {
        "title": title,
        "event_type": "lecture",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat(),
    }
    if recurrence:
        body["recurrence_rule"] = recurrence
    return client.post("/api/events?force=true", json=body).json()


# --- Intent detection ------------------------------------------------------

def test_cancel_wording_is_detected_as_delete(auth_client):
    _make_event(auth_client, "ANN Lecture")
    resp = auth_client.post("/api/ai/process-prompt", json={"prompt": "Cancel my ANN Lecture"}).json()
    assert resp["intent"] == "DELETE_EVENT"
    assert resp["action"] == "delete"
    assert resp["matches"], "should resolve the existing event"
    assert resp["extraction"]["events"] == [], "a delete must not carry an event to create"


def test_move_wording_is_detected_as_update(auth_client):
    _make_event(auth_client, "Project Review")
    resp = auth_client.post("/api/ai/process-prompt", json={"prompt": "Move my Project Review to 4 PM"}).json()
    assert resp["intent"] == "UPDATE_EVENT"
    assert resp["action"] == "update"
    assert resp["matches"]


# --- The regression --------------------------------------------------------

def test_cancelling_deletes_instead_of_creating(auth_client):
    _make_event(auth_client, "Department Meeting")
    before = len(auth_client.get("/api/events").json())

    resp = auth_client.post("/api/ai/process-prompt", json={"prompt": "Cancel my Department Meeting"}).json()
    result = auth_client.post("/api/ai/confirm", json={"extraction": resp["extraction"]}).json()

    assert result["ok"] is True and result["deleted"] >= 1
    after = auth_client.get("/api/events").json()
    assert len(after) == before - 1, "confirming a cancel must remove an event, not add one"
    assert not any(e["title"] == "Department Meeting" for e in after)


def test_update_moves_the_existing_event(auth_client):
    created = _make_event(auth_client, "ANN Lecture", hour=10)
    event_id = created[0]["id"]
    before = len(auth_client.get("/api/events").json())

    resp = auth_client.post("/api/ai/process-prompt", json={"prompt": "Reschedule my ANN Lecture to 15:00"}).json()
    result = auth_client.post("/api/ai/confirm", json={"extraction": resp["extraction"]}).json()
    assert result["ok"] is True and result["updated"] >= 1

    events = auth_client.get("/api/events").json()
    assert len(events) == before, "an update must not create a duplicate"
    moved = [e for e in events if e["id"] == event_id][0]
    assert datetime.fromisoformat(moved["start_datetime"]).hour != 10


def test_delete_only_affects_the_named_event(auth_client):
    _make_event(auth_client, "ANN Lecture", days_ahead=2)
    _make_event(auth_client, "Data Science Lecture", days_ahead=3)

    resp = auth_client.post("/api/ai/process-prompt", json={"prompt": "Cancel my ANN Lecture"}).json()
    auth_client.post("/api/ai/confirm", json={"extraction": resp["extraction"]})

    titles = [e["title"] for e in auth_client.get("/api/events").json()]
    assert "Data Science Lecture" in titles
    assert "ANN Lecture" not in titles


def test_unknown_event_is_reported_not_created(auth_client):
    before = len(auth_client.get("/api/events").json())
    resp = auth_client.post("/api/ai/process-prompt", json={"prompt": "Cancel my Astrophysics Seminar"}).json()

    assert resp["matches"] == []
    assert resp["requires_confirmation"] is False, "nothing to confirm when nothing matched"

    result = auth_client.post("/api/ai/confirm", json={"extraction": resp["extraction"]}).json()
    assert result["ok"] is False
    assert len(auth_client.get("/api/events").json()) == before, "must not create a phantom event"


def test_delete_defaults_to_next_occurrence_of_a_series(auth_client, db_session):
    _make_event(auth_client, "Weekly Seminar", recurrence="weekly:MON")
    total = db_session.query(Event).filter(Event.title == "Weekly Seminar").count()
    assert total > 1

    resp = auth_client.post("/api/ai/process-prompt", json={"prompt": "Cancel my Weekly Seminar"}).json()
    auth_client.post("/api/ai/confirm", json={"extraction": resp["extraction"]})

    remaining = db_session.query(Event).filter(Event.title == "Weekly Seminar").count()
    assert remaining == total - 1, "without 'all', only the next occurrence should go"


def test_cancel_all_removes_the_whole_series(auth_client, db_session):
    _make_event(auth_client, "Lab Session", recurrence="weekly:TUE")
    assert db_session.query(Event).filter(Event.title == "Lab Session").count() > 1

    resp = auth_client.post("/api/ai/process-prompt", json={"prompt": "Cancel all my Lab Session classes"}).json()
    assert resp["extraction"]["apply_to_series"] is True
    auth_client.post("/api/ai/confirm", json={"extraction": resp["extraction"]})

    assert db_session.query(Event).filter(Event.title == "Lab Session").count() == 0
