"""
Timetable generation and the weekly-grid read endpoint. Generation returns
a preview grid; POST with commit=true materializes it as recurring Events.
"""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Event, User
from app.schemas import TimetableGenerateRequest
from app.services.nlp_dates import combine, resolve_time
from app.services.recurrence import DAY_CODES, generate_occurrence_starts
from app.services.scheduler import generate_timetable

router = APIRouter(prefix="/api/timetable", tags=["timetable"])

DAY_FULL = {"Mon": "MON", "Tue": "TUE", "Wed": "WED", "Thu": "THU", "Fri": "FRI", "Sat": "SAT", "Sun": "SUN"}


@router.post("/generate")
def generate(
    payload: TimetableGenerateRequest,
    commit: bool = Query(default=False, description="Persist the generated timetable as recurring events"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = generate_timetable(payload)

    if not commit:
        return result

    # Materialize: for each (day, slot, subject) cell, create/extend a weekly recurring event.
    today = date.today()
    days_until_monday = -today.weekday()
    monday = today + timedelta(days=days_until_monday)
    weekday_offset = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}

    # Group consecutive slots for the same subject on the same day into one event
    created_count = 0
    for day, slots in result["grid"].items():
        sorted_slots = sorted(slots.items())
        i = 0
        while i < len(sorted_slots):
            slot_time, subject = sorted_slots[i]
            if not subject:
                i += 1
                continue
            start_time = resolve_time(slot_time)
            j = i
            while j + 1 < len(sorted_slots) and sorted_slots[j + 1][1] == subject:
                j += 1
            end_time = resolve_time(sorted_slots[j][0])
            end_dt_time = (datetime.combine(today, end_time) + timedelta(minutes=payload.slot_minutes)).time()

            first_date = monday + timedelta(days=weekday_offset.get(day, 0))
            start_dt = combine(first_date, start_time, user.timezone)
            end_dt = combine(first_date, end_dt_time, user.timezone)
            recurrence_rule = f"weekly:{DAY_FULL.get(day, 'MON')}"

            duration = end_dt - start_dt
            group_id = None
            occurrences = generate_occurrence_starts(start_dt, recurrence_rule)
            if len(occurrences) > 1:
                import uuid

                group_id = str(uuid.uuid4())

            for occ_start in occurrences:
                event = Event(
                    user_id=user.id,
                    title=f"{subject} Lecture",
                    event_type="lecture",
                    subject=subject,
                    start_datetime=occ_start,
                    end_datetime=occ_start + duration,
                    recurrence_rule=recurrence_rule,
                    recurrence_group_id=group_id,
                )
                db.add(event)
                created_count += 1

            i = j + 1

    db.commit()
    return {**result, "committed": True, "events_created": created_count}


@router.get("")
def get_weekly_timetable(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Return this week's events shaped for the weekly grid view."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday_end = monday + timedelta(days=7)

    start_dt = combine(monday, resolve_time("00:00"), user.timezone)
    end_dt = combine(sunday_end, resolve_time("00:00"), user.timezone)

    events = (
        db.query(Event)
        .filter(
            Event.user_id == user.id,
            Event.is_cancelled.is_(False),
            Event.start_datetime >= start_dt,
            Event.start_datetime < end_dt,
        )
        .order_by(Event.start_datetime)
        .all()
    )

    return {
        "week_start": monday.isoformat(),
        "events": [
            {
                "id": e.id,
                "title": e.title,
                "event_type": e.event_type,
                "subject": e.subject,
                "day": DAY_CODES[e.start_datetime.weekday()],
                "start": e.start_datetime.isoformat(),
                "end": e.end_datetime.isoformat(),
                "location": e.location,
                "priority": e.priority,
            }
            for e in events
        ],
    }
