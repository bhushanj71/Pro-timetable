"""
Tests for the AI prompt pipeline. AI_PROVIDER=none in the test environment,
so these exercise the rule-based fallback extractor and confirm that its
output always validates against AIExtractionResult before reaching the API.
"""
from app.schemas import AIExtractionResult
from app.services.ai_service import get_ai_service


def test_fallback_extraction_validates_against_schema():
    ai = get_ai_service()
    result = ai.process_prompt(
        "I have ANN lecture every Monday from 10 AM to 11 AM.",
        {"today": "2026-08-22", "weekday": "Saturday", "timezone": "Asia/Kolkata"},
    )
    assert isinstance(result, AIExtractionResult)
    assert result.intent in ("CREATE_EVENT", "CREATE_RECURRING_EVENT")
    assert len(result.events) == 1
    assert result.events[0].start_time == "10:00"


def test_fallback_reminder_intent():
    ai = get_ai_service()
    result = ai.process_prompt(
        "Remind me tomorrow at 9 AM to submit the internal marks.",
        {"today": "2026-08-22", "weekday": "Saturday", "timezone": "Asia/Kolkata"},
    )
    assert result.intent == "CREATE_REMINDER"
    assert len(result.reminders) == 1


def test_process_prompt_endpoint_requires_auth(client):
    resp = client.post("/api/ai/process-prompt", json={"prompt": "test"})
    assert resp.status_code == 401


def test_process_prompt_and_confirm_creates_event(auth_client):
    resp = auth_client.post(
        "/api/ai/process-prompt",
        json={"prompt": "I have ANN lecture every Monday from 10 AM to 11 AM."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["requires_confirmation"] is True
    assert len(body["extraction"]["events"]) == 1

    confirm_resp = auth_client.post("/api/ai/confirm", json={"extraction": body["extraction"]})
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["events_created"] >= 1

    events = auth_client.get("/api/events").json()
    assert any("ANN" in e["title"] or "Ann" in e["title"] for e in events)


def test_task_creation_and_completion(auth_client):
    resp = auth_client.post("/api/tasks", json={"title": "Submit internal marks", "priority": "high"})
    assert resp.status_code == 201
    task_id = resp.json()["id"]

    complete_resp = auth_client.post(f"/api/tasks/{task_id}/complete")
    assert complete_resp.status_code == 200
    assert complete_resp.json()["status"] == "completed"


def test_reminder_creation(auth_client):
    resp = auth_client.post(
        "/api/reminders",
        json={"title": "Submit marks", "reminder_datetime": "2026-09-01T09:00:00Z", "reminder_type": "in_app"},
    )
    assert resp.status_code == 201
    assert resp.json()["is_sent"] is False


def test_cron_processes_due_reminders(auth_client):
    auth_client.post(
        "/api/reminders",
        json={"title": "Past reminder", "reminder_datetime": "2020-01-01T09:00:00Z", "reminder_type": "in_app"},
    )
    resp = auth_client.post("/api/cron/process-reminders")
    assert resp.status_code == 200
    assert resp.json()["sent"] >= 1

    notifs = auth_client.get("/api/reminders/notifications").json()
    assert any(n["title"] == "Past reminder" for n in notifs)


def test_duplicate_event_objects_are_collapsed(auth_client):
    """Models often emit one event object per weekday, each already carrying
    every recurrence day. Naively expanding those yields N copies per day."""
    extraction = {
        "intent": "CREATE_RECURRING_EVENT",
        "events": [
            {
                "title": "DSV lecture",
                "event_type": "lecture",
                "start_time": "14:15",
                "end_time": "15:15",
                "recurrence": "weekly",
                "recurrence_days": ["Monday", "Thursday", "Friday"],
                "priority": "medium",
            }
        ]
        * 3,  # the same schedule repeated, as the model tends to emit it
        "reminders": [],
        "tasks": [],
    }
    resp = auth_client.post("/api/ai/confirm", json={"extraction": extraction})
    assert resp.status_code == 200

    events = auth_client.get("/api/events").json()
    # Exactly one event per occurrence, not three.
    starts = [e["start_datetime"] for e in events]
    assert len(starts) == len(set(starts)), "duplicate events were created for the same slot"


def test_confirming_twice_does_not_duplicate(auth_client):
    extraction = {
        "intent": "CREATE_EVENT",
        "events": [
            {
                "title": "One Off Meeting",
                "event_type": "meeting",
                "date": "2026-09-10",
                "start_time": "11:00",
                "end_time": "12:00",
                "priority": "medium",
            }
        ],
        "reminders": [],
        "tasks": [],
    }
    auth_client.post("/api/ai/confirm", json={"extraction": extraction})
    auth_client.post("/api/ai/confirm", json={"extraction": extraction})

    events = [e for e in auth_client.get("/api/events").json() if e["title"] == "One Off Meeting"]
    assert len(events) == 1, f"expected 1 event after double-confirm, got {len(events)}"


def test_timetable_week_offset_finds_next_week(auth_client):
    """A schedule created for next week must be reachable from the grid."""
    from datetime import date, timedelta

    today = date.today()
    next_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    auth_client.post(
        "/api/events",
        json={
            "title": "Next Week Lecture",
            "start_datetime": f"{next_monday.isoformat()}T09:00:00Z",
            "end_datetime": f"{next_monday.isoformat()}T10:00:00Z",
        },
    )

    this_week = auth_client.get("/api/timetable").json()
    assert not any(e["title"] == "Next Week Lecture" for e in this_week["events"])

    next_week = auth_client.get("/api/timetable?week_offset=1").json()
    assert any(e["title"] == "Next Week Lecture" for e in next_week["events"])
