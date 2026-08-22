"""
The AI natural-language endpoints: prompt processing, confirmation
(actual DB writes happen only here), timetable generation, free-time
finding, conflict resolution, and schedule queries.

Flow: prompt -> AIService -> AIExtractionResult -> conflict check ->
show the professor what was understood -> POST /confirm persists it.
"""
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import AIConversation, Event, Task, User
from app.schemas import (
    AIConfirmRequest,
    AIExtractionResult,
    AIPromptRequest,
    AIPromptResponse,
    FreeTimeRequest,
    ScheduleEvent,
    TimetableGenerateRequest,
)
from app.services.ai_service import get_ai_service
from app.services.conflict_service import find_conflicts
from app.services.nlp_dates import WEEKDAYS, combine, get_tz, resolve_date, resolve_time, weekday_code
from app.services.recurrence import generate_occurrence_starts
from app.services.reminder_service import create_reminder_for_event, create_reminder_for_task
from app.services.scheduler import find_free_slots, generate_timetable

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _schedule_event_to_datetimes(evt: ScheduleEvent, tz_name: str) -> tuple[datetime, datetime, str | None]:
    """Resolve a ScheduleEvent's day/date + start/end times into concrete datetimes and a recurrence_rule."""
    day_source = evt.date or evt.day
    recurrence_rule = None

    # Models often get relative-date arithmetic wrong ("next Friday" -> a
    # Wednesday's date) while naming the weekday correctly. When the explicit
    # date contradicts the stated weekday, trust the weekday name.
    if evt.date and evt.day:
        stated_day = evt.day.strip().lower()
        if stated_day in WEEKDAYS:
            resolved_from_date = resolve_date(evt.date, tz_name)
            if resolved_from_date.strftime("%A").lower() != stated_day:
                day_source = evt.day

    if evt.recurrence == "weekly" and evt.recurrence_days:
        first_day = evt.recurrence_days[0]
        base_date = resolve_date(first_day, tz_name)
        codes = []
        for d in evt.recurrence_days:
            resolved = resolve_date(d, tz_name)
            codes.append(weekday_code(resolved))
        recurrence_rule = f"weekly:{','.join(sorted(set(codes)))}"
    elif evt.recurrence == "weekly" and evt.day:
        base_date = resolve_date(evt.day, tz_name)
        recurrence_rule = f"weekly:{weekday_code(base_date)}"
    elif evt.recurrence == "daily":
        base_date = resolve_date(day_source, tz_name)
        recurrence_rule = "daily"
    else:
        base_date = resolve_date(day_source, tz_name)

    start_t = resolve_time(evt.start_time)
    end_t = resolve_time(evt.end_time, default=time((start_t.hour + 1) % 24, start_t.minute))

    start_dt = combine(base_date, start_t, tz_name)
    end_dt = combine(base_date, end_t, tz_name)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)

    return start_dt, end_dt, recurrence_rule


@router.post("/process-prompt", response_model=AIPromptResponse)
def process_prompt(payload: AIPromptRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ai = get_ai_service()
    now = datetime.now(timezone.utc)
    context = {
        "today": date.today().isoformat(),
        "weekday": date.today().strftime("%A"),
        "timezone": user.timezone,
    }
    extraction = ai.process_prompt(payload.prompt, context)

    conflicts_out = []
    for evt in extraction.events:
        start_dt, end_dt, _ = _schedule_event_to_datetimes(evt, user.timezone)
        clashes = find_conflicts(db, user.id, start_dt, end_dt)
        if clashes:
            conflicts_out.append(
                {
                    "event_title": evt.title,
                    "conflicts_with": [{"id": c.id, "title": c.title, "start": c.start_datetime.isoformat()} for c in clashes],
                }
            )

    summary_parts = []
    if extraction.notes:
        summary_parts.append(extraction.notes)
    for evt in extraction.events:
        start_dt, end_dt, _ = _schedule_event_to_datetimes(evt, user.timezone)
        local_start = start_dt.astimezone(get_tz(user.timezone))
        local_end = end_dt.astimezone(get_tz(user.timezone))
        summary_parts.append(f"{evt.title}: {local_start.strftime('%A %I:%M %p')} - {local_end.strftime('%I:%M %p')}")
    for r in extraction.reminders:
        summary_parts.append(f"Reminder: {r.title}")
    for t in extraction.tasks:
        summary_parts.append(f"Task: {t.title}")
    summary = " | ".join(summary_parts) if summary_parts else "I couldn't extract anything actionable from that."

    convo = AIConversation(
        user_id=user.id,
        prompt=payload.prompt,
        ai_response=extraction.model_dump_json(),
        intent=extraction.intent,
    )
    db.add(convo)
    db.commit()

    requires_confirmation = extraction.intent not in ("QUERY_SCHEDULE", "FIND_FREE_TIME")

    return AIPromptResponse(
        intent=extraction.intent,
        extraction=extraction,
        summary=summary,
        conflicts=conflicts_out,
        requires_confirmation=requires_confirmation,
    )


@router.post("/confirm")
def confirm_extraction(payload: AIConfirmRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    extraction = payload.extraction
    created_events, created_reminders, created_tasks = [], [], []
    series_count = 0  # user-facing count: one per distinct weekly slot, not per materialized occurrence

    for evt in extraction.events:
        start_dt, end_dt, recurrence_rule = _schedule_event_to_datetimes(evt, user.timezone)
        duration = end_dt - start_dt
        occurrence_starts = generate_occurrence_starts(start_dt, recurrence_rule)
        group_id = str(uuid4()) if recurrence_rule and len(occurrence_starts) > 1 else None

        if recurrence_rule and recurrence_rule.startswith("weekly:"):
            series_count += len(recurrence_rule.split(":", 1)[1].split(","))
        else:
            series_count += 1

        for occ_start in occurrence_starts:
            event = Event(
                user_id=user.id,
                title=evt.title,
                description=evt.description,
                event_type=evt.event_type,
                subject=evt.subject,
                start_datetime=occ_start,
                end_datetime=occ_start + duration,
                location=evt.location,
                priority=evt.priority,
                recurrence_rule=recurrence_rule,
                recurrence_group_id=group_id,
            )
            db.add(event)
            db.flush()
            if evt.reminder_minutes:
                create_reminder_for_event(db, event, evt.reminder_minutes)
            created_events.append(event)

    for r in extraction.reminders:
        target_date = resolve_date(r.date, user.timezone)
        target_time = resolve_time(r.time)
        reminder_dt = combine(target_date, target_time, user.timezone)

        related_event = None
        if r.related_event_title:
            related_event = (
                db.query(Event)
                .filter(Event.user_id == user.id, Event.title.ilike(f"%{r.related_event_title}%"))
                .order_by(Event.start_datetime)
                .first()
            )
        if related_event and r.minutes_before_event:
            reminder = create_reminder_for_event(db, related_event, r.minutes_before_event)
        else:
            from app.models import Reminder

            reminder = Reminder(user_id=user.id, title=r.title, reminder_datetime=reminder_dt)
            db.add(reminder)
        created_reminders.append(reminder)

    for t in extraction.tasks:
        due = resolve_date(t.due_date, user.timezone) if t.due_date else None
        task = Task(
            user_id=user.id,
            title=t.title,
            priority=t.priority,
            due_date=combine(due, time(23, 59), user.timezone) if due else None,
        )
        db.add(task)
        db.flush()
        if due:
            create_reminder_for_task(db, task, combine(due, time(9, 0), user.timezone) - timedelta(days=1))
        created_tasks.append(task)

    db.commit()

    return {
        "ok": True,
        "events_created": series_count,
        "reminders_created": len(created_reminders),
        "tasks_created": len(created_tasks),
    }


@router.post("/generate-timetable")
def ai_generate_timetable(payload: TimetableGenerateRequest, user: User = Depends(get_current_user)):
    return generate_timetable(payload)


@router.post("/find-free-time")
def ai_find_free_time(payload: FreeTimeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target_date = resolve_date(payload.date, user.timezone) if payload.date else date.today()
    day_start = resolve_time(user.working_hours_start)
    day_end = resolve_time(user.working_hours_end)
    slots = find_free_slots(db, user.id, target_date, payload.duration_minutes, user.timezone, day_start, day_end)
    return {
        "date": target_date.isoformat(),
        "duration_minutes": payload.duration_minutes,
        "free_slots": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in slots],
    }


@router.post("/resolve-conflict")
def ai_resolve_conflict(event_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from fastapi import HTTPException, status

    from app.services.conflict_service import suggest_resolution

    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    conflicts = find_conflicts(db, user.id, event.start_datetime, event.end_datetime, exclude_event_id=event.id)
    if not conflicts:
        return {"has_conflict": False}
    return {
        "has_conflict": True,
        "resolution": suggest_resolution(db, user.id, conflicts[0], (event.start_datetime, event.end_datetime)),
    }


@router.post("/query-schedule")
def ai_query_schedule(payload: AIPromptRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Lightweight schedule query: relies on process_prompt's target_date, falls back to 'this week'."""
    ai = get_ai_service()
    context = {"today": date.today().isoformat(), "weekday": date.today().strftime("%A"), "timezone": user.timezone}
    extraction = ai.process_prompt(payload.prompt, context)

    target_date = resolve_date(extraction.target_date, user.timezone) if extraction.target_date else date.today()
    start = combine(target_date, time.min, user.timezone)
    end = start + timedelta(days=7 if "week" in payload.prompt.lower() else 1)

    events = (
        db.query(Event)
        .filter(Event.user_id == user.id, Event.is_cancelled.is_(False), Event.start_datetime >= start, Event.start_datetime < end)
        .order_by(Event.start_datetime)
        .all()
    )
    return {
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "events": [
            {"id": e.id, "title": e.title, "start": e.start_datetime.isoformat(), "end": e.end_datetime.isoformat()}
            for e in events
        ],
    }
