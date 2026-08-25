"""
CRUD for events, with recurrence materialization and conflict detection.
"""
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Event, User
from app.schemas import EventCreate, EventOut, EventReminderCreate, EventUpdate
from app.services.conflict_service import find_conflicts, suggest_resolution
from app.services.recurrence import generate_occurrence_starts
from app.models import PushSubscription
from app.services import activity, schedule_query as sq
from app.services.notifier import push_configured
from app.services.nlp_dates import ensure_aware_utc
from app.services.reminder_service import create_reminder_for_event, lead_minutes
from app.services.reminder_service import resync_event_reminders, schedule_event_reminders

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def list_events(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    event_type: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    subject: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Event).filter(Event.user_id == user.id, Event.is_cancelled.is_(False))
    if start:
        query = query.filter(Event.end_datetime >= start)
    if end:
        query = query.filter(Event.start_datetime <= end)
    if event_type:
        query = query.filter(Event.event_type == event_type)
    if priority:
        query = query.filter(Event.priority == priority)
    if subject:
        query = query.filter(Event.subject.ilike(f"%{subject}%"))
    return query.order_by(Event.start_datetime).all()


@router.delete("")
def clear_events(
    confirm: bool = Query(default=False, description="Must be true; guards against an accidental wipe"),
    scope: str = Query(default="all", pattern="^(all|future|past|week)$"),
    week_offset: int = Query(default=0, ge=-52, le=52, description="For scope=week: which week, relative to this one"),
    subject: str | None = Query(default=None, description="Limit the delete to one subject"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bulk-delete this user's events.

    Always scoped to the calling user. `confirm=true` is required so a stray
    DELETE on the collection URL cannot wipe a schedule by accident.
    """
    if not confirm:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Refusing to delete without confirm=true",
        )

    now = datetime.now(timezone.utc)
    query = db.query(Event).filter(Event.user_id == user.id)

    if scope == "future":
        query = query.filter(Event.start_datetime >= now)
    elif scope == "past":
        query = query.filter(Event.start_datetime < now)
    elif scope == "week":
        today = now.date()
        monday = datetime.combine(
            today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset), time.min, tzinfo=timezone.utc
        )
        query = query.filter(Event.start_datetime >= monday, Event.start_datetime < monday + timedelta(days=7))

    if subject:
        query = query.filter(Event.subject.ilike(f"%{subject}%"))

    deleted = query.delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted, "scope": scope, "subject": subject}


@router.get("/next")
def next_class(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The soonest upcoming event. Backs both the "next lecture" card and the
    spoken question, so they can never disagree."""
    nxt = sq.next_class(db, user)
    return {"event": sq.serialize(nxt, user.timezone) if nxt else None}


@router.get("/conflicts")
def conflicts(
    days: int = Query(default=7, ge=1, le=60),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Overlapping pairs in the coming days, labelled by what actually clashes
    -- the same room, the same person, or just the same slot."""
    return {"conflicts": sq.find_conflicts(db, user, days=days)}


@router.get("/search")
def search_events(
    q: str | None = Query(default=None, description="subject or title text"),
    faculty: str | None = Query(default=None),
    location: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    include_past: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Search the professor's own schedule. Filters combine."""
    rows = sq.find_events(
        db, user, text=q, faculty=faculty, location=location,
        event_type=event_type, upcoming_only=not include_past,
    )
    return {"count": len(rows), "events": [sq.serialize(e, user.timezone) for e in rows]}


@router.get("/{event_id}/location")
def event_location(event_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Everything needed to show or navigate to where a class is."""
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return sq.serialize(event, user.timezone)


@router.get("/{event_id}/reminders")
def list_event_reminders(event_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Pending reminders for one event, newest lead first."""
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    rows = []
    for r in event.reminders:
        if r.is_sent:
            continue
        lead = lead_minutes(r)
        rows.append({"id": r.id, "minutes_before": lead, "title": r.title})
    rows.sort(key=lambda x: (x["minutes_before"] is None, -(x["minutes_before"] or 0)))
    return {"reminders": rows, "push_ready": push_configured()}


@router.post("/{event_id}/reminders", status_code=status.HTTP_201_CREATED)
def add_event_reminder(
    event_id: str,
    payload: EventReminderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add one reminder at a chosen lead time.

    Adds rather than replaces: a professor may reasonably want an hour's
    warning to prepare and five minutes' warning to walk.
    """
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    minutes = payload.minutes_before
    when = ensure_aware_utc(event.start_datetime) - timedelta(minutes=minutes)
    if when <= datetime.now(timezone.utc):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"That moment has already passed \u2014 this event starts too soon for a "
            f"{minutes}-minute warning.",
        )

    existing = {lead_minutes(r) for r in event.reminders if not r.is_sent}
    if minutes in existing:
        return {"ok": True, "added": False, "message": f"You already have a {minutes}-minute reminder."}

    create_reminder_for_event(db, event, minutes)
    db.commit()

    devices = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
    return {
        "ok": True,
        "added": True,
        "minutes_before": minutes,
        "devices": devices,
        "message": (
            f"Reminder set for {minutes} minutes before."
            if devices
            else f"Reminder set for {minutes} minutes before. Turn on notifications "
                 f"on this device to get it as a push."
        ),
    }


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return event


@router.post("", response_model=list[EventOut], status_code=status.HTTP_201_CREATED)
def create_event(
    payload: EventCreate,
    force: bool = Query(default=False, description="Create despite conflicts"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conflicts = find_conflicts(db, user.id, payload.start_datetime, payload.end_datetime)
    if conflicts and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "message": "This event conflicts with existing events",
                "conflicts": [
                    {"id": c.id, "title": c.title, "start": c.start_datetime.isoformat(), "end": c.end_datetime.isoformat()}
                    for c in conflicts
                ],
            },
        )

    duration = payload.end_datetime - payload.start_datetime
    occurrence_starts = generate_occurrence_starts(payload.start_datetime, payload.recurrence_rule)
    group_id = str(uuid4()) if payload.recurrence_rule and len(occurrence_starts) > 1 else None

    # Passed through untouched: `None` lets schedule_event_reminders apply the
    # profile default, while an explicit empty list still means "no reminders".
    lead_times = payload.reminder_minutes

    created: list[Event] = []
    for occ_start in occurrence_starts:
        event = Event(
            user_id=user.id,
            title=payload.title,
            description=payload.description,
            event_type=payload.event_type,
            subject=payload.subject,
            start_datetime=occ_start,
            end_datetime=occ_start + duration,
            faculty=payload.faculty,
            location=payload.location,
            location_detail=payload.location_detail,
            location_url=payload.location_url,
            priority=payload.priority,
            recurrence_rule=payload.recurrence_rule,
            recurrence_group_id=group_id,
            is_all_day=payload.is_all_day,
        )
        db.add(event)
        db.flush()  # get event.id before creating reminders
        schedule_event_reminders(db, event, user, leads=lead_times)
        created.append(event)

    # One receipt for the whole action, not one per materialised occurrence:
    # a weekly lecture creates sixteen rows from a single decision.
    title, body = activity.summarise_series(len(created), f"Added {payload.title}")
    activity.record(db, user.id, activity.Activity.EVENT_CREATED, title, body)

    db.commit()
    for e in created:
        db.refresh(e)

    # Mirror into Google Calendar so Google delivers the phone notification.
    # Best-effort: a sync failure must never fail the event creation.
    if user.google_sync_enabled:
        from app.services.google import sync_event

        for e in created[:60]:  # cap the burst from a long recurring series
            sync_event(db, user, e)

    return created


@router.put("/{event_id}", response_model=EventOut)
def update_event(
    event_id: str,
    payload: EventUpdate,
    apply_to_series: bool = Query(default=False),
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    updates = payload.model_dump(exclude_unset=True)

    new_start = updates.get("start_datetime", event.start_datetime)
    new_end = updates.get("end_datetime", event.end_datetime)
    if new_start and new_end and not force:
        conflicts = find_conflicts(db, user.id, new_start, new_end, exclude_event_id=event.id)
        if conflicts:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "message": "This change conflicts with existing events",
                    "conflicts": [{"id": c.id, "title": c.title} for c in conflicts],
                },
            )

    targets = [event]
    if apply_to_series and event.recurrence_group_id:
        targets = db.query(Event).filter(Event.recurrence_group_id == event.recurrence_group_id).all()

    # For series updates, shift time-of-day but keep each occurrence's original date
    time_shift = None
    if apply_to_series and len(targets) > 1 and ("start_datetime" in updates or "end_datetime" in updates):
        time_shift = (
            (new_start - event.start_datetime) if new_start else None,
            (new_end - event.end_datetime) if new_end else None,
        )

    for target in targets:
        for field, value in updates.items():
            if field in ("start_datetime", "end_datetime") and time_shift and target is not event:
                continue
            setattr(target, field, value)
        if time_shift and target is not event:
            if time_shift[0]:
                target.start_datetime = target.start_datetime + time_shift[0]
            if time_shift[1]:
                target.end_datetime = target.end_datetime + time_shift[1]

    # A moved lecture whose reminder still points at the old time is worse
    # than no reminder: it fires when nothing is happening and stays silent
    # when the class actually starts. Re-anchor every pending one, each at the
    # lead it was created with.
    if "start_datetime" in updates or time_shift:
        for target in targets:
            resync_event_reminders(db, target)

    changed = ", ".join(k.replace("_", " ") for k in updates if k != "is_cancelled")
    activity.record(
        db, user.id, activity.Activity.EVENT_UPDATED,
        f"Updated {event.title}", changed or None,
    )

    db.commit()
    db.refresh(event)

    if user.google_sync_enabled:
        from app.services.google import sync_event

        for target in targets[:60]:
            sync_event(db, user, target)

    return event


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: str,
    apply_to_series: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    doomed = (
        db.query(Event).filter(Event.recurrence_group_id == event.recurrence_group_id).all()
        if apply_to_series and event.recurrence_group_id
        else [event]
    )

    if user.google_sync_enabled:
        from app.services.google import delete_event as google_delete

        for e in doomed[:60]:
            if e.google_event_id:
                google_delete(user, e.google_event_id)

    # Recorded before the rows go, while the title is still readable, and as
    # one receipt for the whole series rather than one per occurrence.
    title, body = activity.summarise_series(len(doomed), f"Deleted {event.title}")
    activity.record(db, user.id, activity.Activity.EVENT_DELETED, title, body)

    for e in doomed:
        db.delete(e)
    db.commit()
    return None


@router.post("/{event_id}/duplicate", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def duplicate_event(event_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    original = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not original:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    copy = Event(
        user_id=user.id,
        title=f"{original.title} (Copy)",
        description=original.description,
        event_type=original.event_type,
        subject=original.subject,
        start_datetime=original.start_datetime,
        end_datetime=original.end_datetime,
        location=original.location,
        priority=original.priority,
        is_all_day=original.is_all_day,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return copy


@router.get("/{event_id}/conflict-resolution")
def get_conflict_resolution(event_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    conflicts = find_conflicts(db, user.id, event.start_datetime, event.end_datetime, exclude_event_id=event.id)
    if not conflicts:
        return {"has_conflict": False}

    suggestion = suggest_resolution(db, user.id, conflicts[0], (event.start_datetime, event.end_datetime))
    return {
        "has_conflict": True,
        "conflicts": [{"id": c.id, "title": c.title} for c in conflicts],
        "suggestion": suggestion,
    }
