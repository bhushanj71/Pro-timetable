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
