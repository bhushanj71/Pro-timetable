"""
Reminder CRUD plus the notification-center feed (unsent, due reminders).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Reminder, User
from app.schemas import ReminderCreate, ReminderOut

router = APIRouter(prefix="/api/reminders", tags=["reminders"])

# A recurring lecture materializes one reminder per occurrence (see
# generate_occurrence_starts' 16-week horizon), so "all reminders" for even a
# few weekly classes can run into the hundreds. Default the list to a sane
# upcoming window; callers that need full history can pass include_past=true.
DEFAULT_LIST_LIMIT = 100


@router.get("", response_model=list[ReminderOut])
def list_reminders(
    include_past: bool = Query(default=False),
    limit: int = Query(default=DEFAULT_LIST_LIMIT, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Reminder).filter(Reminder.user_id == user.id)
    if not include_past:
        query = query.filter(Reminder.reminder_datetime >= datetime.now(timezone.utc))
    return query.order_by(Reminder.reminder_datetime).limit(limit).all()


@router.get("/notifications")
def notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Delivered reminders for the bell, newest first, with an unread count.

    "Delivered" (is_sent) and "seen" (read_at) are deliberately different: the
    badge tracks the latter, so it clears once the panel has been opened.
    """
    now = datetime.now(timezone.utc)
    items = (
        db.query(Reminder)
        .filter(Reminder.user_id == user.id, Reminder.reminder_datetime <= now, Reminder.is_sent.is_(True))
        .order_by(Reminder.reminder_datetime.desc())
        .limit(20)
        .all()
    )
    unread = (
        db.query(Reminder)
        .filter(
            Reminder.user_id == user.id,
            Reminder.reminder_datetime <= now,
            Reminder.is_sent.is_(True),
            Reminder.read_at.is_(None),
        )
        .count()
    )
    return {
        "unread": unread,
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "reminder_datetime": (
                    r.reminder_datetime if r.reminder_datetime.tzinfo else r.reminder_datetime.replace(tzinfo=timezone.utc)
                ).isoformat(),
                "reminder_type": r.reminder_type,
                "is_read": r.read_at is not None,
            }
            for r in items
        ],
    }


@router.post("/notifications/read")
def mark_notifications_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Mark every delivered reminder as seen — called when the bell is opened."""
    now = datetime.now(timezone.utc)
    updated = (
        db.query(Reminder)
        .filter(
            Reminder.user_id == user.id,
            Reminder.reminder_datetime <= now,
            Reminder.is_sent.is_(True),
            Reminder.read_at.is_(None),
        )
        .update({Reminder.read_at: now}, synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "marked_read": updated}


@router.post("", response_model=ReminderOut, status_code=status.HTTP_201_CREATED)
def create_reminder(payload: ReminderCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    reminder = Reminder(user_id=user.id, **payload.model_dump())
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reminder(reminder_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    reminder = db.query(Reminder).filter(Reminder.id == reminder_id, Reminder.user_id == user.id).first()
    if not reminder:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reminder not found")
    db.delete(reminder)
    db.commit()
    return None
