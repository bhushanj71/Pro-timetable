"""
Productivity analytics and global search across events/tasks.
"""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Event, Task, User
from app.services.nlp_dates import combine, resolve_time

router = APIRouter(prefix="/api", tags=["analytics"])

TEACHING_TYPES = {"lecture", "lab"}


@router.get("/analytics")
def get_analytics(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    start = combine(monday, resolve_time("00:00"), user.timezone)
    end = start + timedelta(days=7)

    events = (
        db.query(Event)
        .filter(Event.user_id == user.id, Event.is_cancelled.is_(False), Event.start_datetime >= start, Event.start_datetime < end)
        .all()
    )

    def hours(evts):
        return round(sum((e.end_datetime - e.start_datetime).total_seconds() for e in evts) / 3600, 1)

    teaching = [e for e in events if e.event_type in TEACHING_TYPES]
    meetings = [e for e in events if e.event_type == "meeting"]
    research = [e for e in events if e.event_type == "research"]

    day_start = resolve_time(user.working_hours_start)
    day_end = resolve_time(user.working_hours_end)
    working_days = len([d for d in user.working_days.split(",") if d.strip()])
    total_working_hours = working_days * ((datetime.combine(today, day_end) - datetime.combine(today, day_start)).seconds / 3600)
    busy_hours = hours(events)
    free_hours = max(0, round(total_working_hours - busy_hours, 1))

    pending_tasks = db.query(Task).filter(Task.user_id == user.id, Task.status == "pending").count()

    by_category: dict[str, float] = {}
    for e in events:
        by_category[e.event_type] = by_category.get(e.event_type, 0) + (e.end_datetime - e.start_datetime).total_seconds() / 3600
    by_category = {k: round(v, 1) for k, v in by_category.items()}

    return {
        "week_start": monday.isoformat(),
        "teaching_hours": hours(teaching),
        "meetings_count": len(meetings),
        "research_hours": hours(research),
        "free_hours": free_hours,
        "pending_tasks": pending_tasks,
        "workload_by_category": by_category,
    }


@router.get("/search")
def search(q: str = Query(min_length=1), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    like = f"%{q}%"
    events = (
        db.query(Event)
        .filter(Event.user_id == user.id, or_(Event.title.ilike(like), Event.subject.ilike(like), Event.description.ilike(like)))
        .order_by(Event.start_datetime.desc())
        .limit(20)
        .all()
    )
    tasks = (
        db.query(Task)
        .filter(Task.user_id == user.id, or_(Task.title.ilike(like), Task.description.ilike(like)))
        .order_by(Task.due_date)
        .limit(20)
        .all()
    )
    return {
        "events": [{"id": e.id, "title": e.title, "start": e.start_datetime.isoformat(), "type": "event"} for e in events],
        "tasks": [{"id": t.id, "title": t.title, "due_date": t.due_date.isoformat() if t.due_date else None, "type": "task"} for t in tasks],
    }
