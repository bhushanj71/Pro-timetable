"""
Overlap / conflict detection between events, plus AI-assisted resolution
suggestions.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Event


def find_conflicts(
    db: Session,
    user_id: str,
    start: datetime,
    end: datetime,
    exclude_event_id: str | None = None,
) -> list[Event]:
    """Return events for this user that overlap [start, end)."""
    query = db.query(Event).filter(
        Event.user_id == user_id,
        Event.is_cancelled.is_(False),
        Event.start_datetime < end,
        Event.end_datetime > start,
    )
    if exclude_event_id:
        query = query.filter(Event.id != exclude_event_id)
    return query.all()


def suggest_resolution(db: Session, user_id: str, conflicting_event: Event, new_event_window: tuple[datetime, datetime]) -> dict:
    """
    Suggest the next free slot of the same duration, searching forward in
    30-minute increments across the same day, then subsequent days (up to 7).
    """
    duration = new_event_window[1] - new_event_window[0]
    cursor = new_event_window[0]
    for _ in range(7 * 48):  # up to 7 days, 30-min steps
        candidate_end = cursor + duration
        conflicts = find_conflicts(db, user_id, cursor, candidate_end, exclude_event_id=conflicting_event.id)
        if not conflicts:
            return {
                "suggested_start": cursor.isoformat(),
                "suggested_end": candidate_end.isoformat(),
            }
        cursor += timedelta(minutes=30)
    return {"suggested_start": None, "suggested_end": None, "message": "No free slot found in the next 7 days"}
