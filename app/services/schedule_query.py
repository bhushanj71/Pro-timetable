"""
Read-only answers about a professor's schedule: what is next, where it is,
and what clashes.

These back both the AI intents (GET_NEXT_CLASS, SHOW_LOCATION,
CHECK_CONFLICTS, VIEW_REMINDERS) and the plain REST endpoints the UI calls,
so a spoken question and a tapped button return the same thing.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from sqlalchemy.orm import Session

from app.models import Event, Reminder, User
from app.services.nlp_dates import ensure_aware_utc, get_tz

logger = logging.getLogger(__name__)

# Anything longer than this and "your next class" stops being useful.
NEXT_HORIZON_DAYS = 14


def _local(dt: datetime, tz_name: str) -> datetime:
    return ensure_aware_utc(dt).astimezone(get_tz(tz_name))


def describe_when(event: Event, tz_name: str) -> str:
    start = _local(event.start_datetime, tz_name)
    end = _local(event.end_datetime, tz_name)
    today = datetime.now(get_tz(tz_name)).date()

    if start.date() == today:
        day = "Today"
    elif start.date() == today + timedelta(days=1):
        day = "Tomorrow"
    else:
        day = start.strftime("%A, %d %b")
    return f"{day}, {start.strftime('%I:%M %p').lstrip('0')} – {end.strftime('%I:%M %p').lstrip('0')}"


def map_url(event: Event) -> str | None:
    """A link that opens the room on a map.

    Uses the professor's own link when they gave one, and otherwise builds a
    search from whatever location text exists. Guessing a search URL is better
    than showing nothing: a room name plus a campus is usually enough for a
    map to land in the right place, and the alternative is a dead button.
    """
    if event.location_url:
        # An href is a script sink: `javascript:` in this field would run on
        # click with the professor's session. Only navigable schemes pass.
        scheme = event.location_url.split(":", 1)[0].strip().lower()
        if scheme in ("http", "https", "geo"):
            return event.location_url
        logger.warning("Ignoring map link with unsupported scheme %r", scheme)
    parts = [p for p in (event.location, event.location_detail) if p]
    if not parts:
        return None
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(", ".join(parts))


def serialize(event: Event, tz_name: str) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "event_type": event.event_type,
        "subject": event.subject,
        "faculty": event.faculty,
        "location": event.location,
        "location_detail": event.location_detail,
        "location_url": event.location_url,
        "map_url": map_url(event),
        "start": ensure_aware_utc(event.start_datetime).isoformat(),
        "end": ensure_aware_utc(event.end_datetime).isoformat(),
        "when": describe_when(event, tz_name),
        "has_location": bool(event.location or event.location_detail or event.location_url),
    }


def next_class(db: Session, user: User, types: tuple[str, ...] | None = None) -> Event | None:
    """The soonest upcoming event, optionally restricted to teaching."""
    now = datetime.now(timezone.utc)
    query = (
        db.query(Event)
        .filter(
            Event.user_id == user.id,
            Event.is_cancelled.is_(False),
            Event.start_datetime >= now,
            Event.start_datetime <= now + timedelta(days=NEXT_HORIZON_DAYS),
        )
        .order_by(Event.start_datetime)
    )
    if types:
        query = query.filter(Event.event_type.in_(types))
    return query.first()


def find_events(
    db: Session,
    user: User,
    *,
    text: str | None = None,
    faculty: str | None = None,
    location: str | None = None,
    event_type: str | None = None,
    upcoming_only: bool = True,
    limit: int = 50,
) -> list[Event]:
    """Search a professor's own schedule. Every filter is optional and they
    combine, so "DBMS labs in Lab 4" narrows on all three."""
    query = db.query(Event).filter(Event.user_id == user.id, Event.is_cancelled.is_(False))

    if upcoming_only:
        query = query.filter(Event.start_datetime >= datetime.now(timezone.utc))
    if text:
        like = f"%{text.strip()}%"
        query = query.filter(Event.title.ilike(like) | Event.subject.ilike(like))
    if faculty:
        query = query.filter(Event.faculty.ilike(f"%{faculty.strip()}%"))
    if location:
        like = f"%{location.strip()}%"
        query = query.filter(Event.location.ilike(like) | Event.location_detail.ilike(like))
    if event_type:
        query = query.filter(Event.event_type == event_type)

    return query.order_by(Event.start_datetime).limit(limit).all()


def find_conflicts(db: Session, user: User, days: int = 7) -> list[dict]:
    """Overlapping pairs in the next `days`.

    Reports pairs rather than individual events: "these two clash" is what the
    professor has to resolve, and listing each event separately would say the
    same thing twice without saying what it collides with.
    """
    now = datetime.now(timezone.utc)
    events = (
        db.query(Event)
        .filter(
            Event.user_id == user.id,
            Event.is_cancelled.is_(False),
            Event.start_datetime >= now,
            Event.start_datetime <= now + timedelta(days=days),
        )
        .order_by(Event.start_datetime)
        .all()
    )

    clashes = []
    for i, a in enumerate(events):
        a_start, a_end = ensure_aware_utc(a.start_datetime), ensure_aware_utc(a.end_datetime)
        for b in events[i + 1:]:
            b_start = ensure_aware_utc(b.start_datetime)
            # Sorted by start, so once b begins after a ends nothing later can
            # overlap a either.
            if b_start >= a_end:
                break
            b_end = ensure_aware_utc(b.end_datetime)
            if b_start < a_end and a_start < b_end:
                same_room = bool(a.location and b.location and a.location.lower() == b.location.lower())
                same_faculty = bool(a.faculty and b.faculty and a.faculty.lower() == b.faculty.lower())
                clashes.append(
                    {
                        "a": serialize(a, user.timezone),
                        "b": serialize(b, user.timezone),
                        "kind": "room" if same_room else ("faculty" if same_faculty else "time"),
                    }
                )
    return clashes


# A holiday clears teaching. A deadline does not move because the campus is
# shut, and a personal appointment is the professor's own business, so
# neither is touched.
TEACHING_TYPES = ("lecture", "lab", "practical", "project_review", "examination")


def events_on_day(db: Session, user: User, day, teaching_only: bool = True) -> list[Event]:
    """Every event a professor has on one local calendar day."""
    tz = get_tz(user.timezone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    end = start + timedelta(days=1)

    query = db.query(Event).filter(
        Event.user_id == user.id,
        Event.is_cancelled.is_(False),
        Event.start_datetime >= start,
        Event.start_datetime < end,
    )
    if teaching_only:
        query = query.filter(Event.event_type.in_(TEACHING_TYPES))
    return query.order_by(Event.start_datetime).all()


def active_reminders(db: Session, user: User, limit: int = 100) -> list[dict]:
    """Pending reminders with the event each belongs to."""
    now = datetime.now(timezone.utc)
    rows = (
        db.query(Reminder)
        .filter(
            Reminder.user_id == user.id,
            Reminder.is_sent.is_(False),
            Reminder.reminder_datetime >= now,
        )
        .order_by(Reminder.reminder_datetime)
        .limit(limit)
        .all()
    )

    out = []
    for r in rows:
        lead = None
        if r.event:
            delta = ensure_aware_utc(r.event.start_datetime) - ensure_aware_utc(r.reminder_datetime)
            lead = int(delta.total_seconds() // 60)
        out.append(
            {
                "id": r.id,
                "title": r.title,
                "at": ensure_aware_utc(r.reminder_datetime).isoformat(),
                "local": _local(r.reminder_datetime, user.timezone).strftime("%a %d %b, %I:%M %p").replace(" 0", " "),
                "event_id": r.event_id,
                "event_title": r.event.title if r.event else None,
                "minutes_before": lead,
            }
        )
    return out
