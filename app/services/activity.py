"""
In-app activity notifications for the personal side of the app.

The bell already merges two feeds. The work one is an activity log; the
personal one was only ever *delivered reminders*, so creating a lecture,
moving it, or finishing a task left no trace in the bell at all -- the record
existed on the timetable and nowhere else.

These records share WorkNotification's table because it is already the bell's
backing store, with read state, dismissal and a preference switch. The name is
narrower than what it now carries; that is worth less than a second table and
a second feed to merge.

Deliberately not reminders. A Reminder is a promise to interrupt someone --
push, email, a lock-screen buzz. "You created a lecture" is a receipt. Putting
receipts in the reminder table would send them to people's phones.
"""
import logging

from sqlalchemy.orm import Session

from app.models import WorkNotification

logger = logging.getLogger(__name__)


class Activity:
    EVENT_CREATED = "event_created"
    EVENT_UPDATED = "event_updated"
    EVENT_DELETED = "event_deleted"
    EVENT_CANCELLED = "event_cancelled"
    TASK_CREATED = "personal_task_created"
    TASK_COMPLETED = "personal_task_completed"
    TASK_DELETED = "personal_task_deleted"
    REMINDER_SET = "personal_reminder_set"


def record(db: Session, user_id: str, kind: str, title: str, body: str | None = None) -> None:
    """Log one thing the professor did, for the bell.

    Never raises: a missing receipt must not fail the action it describes.
    """
    try:
        db.add(WorkNotification(user_id=user_id, kind=kind, title=title, body=body))
    except Exception:
        logger.exception("Could not record activity %s", kind)


def summarise_series(count: int, title: str) -> tuple[str, str | None]:
    """Wording for something that touched many rows at once.

    A weekly lecture materialises sixteen occurrences. Sixteen identical
    notifications would bury everything else in the bell for one action the
    professor experienced as a single decision.
    """
    if count <= 1:
        return title, None
    return title, f"{count} occurrences"
