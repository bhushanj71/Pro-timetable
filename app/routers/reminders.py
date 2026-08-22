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


@router.get("/notifications", response_model=list[ReminderOut])
def notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Reminders that have fired (is_sent, meaning the cron job passed their due time) and are unread."""
    now = datetime.now(timezone.utc)
    return (
        db.query(Reminder)
        .filter(Reminder.user_id == user.id, Reminder.reminder_datetime <= now, Reminder.is_sent.is_(True))
        .order_by(Reminder.reminder_datetime.desc())
        .limit(20)
        .all()
    )


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
