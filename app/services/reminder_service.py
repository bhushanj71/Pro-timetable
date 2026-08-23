"""
Reminder creation and idempotent delivery processing.

Delivery is driven by a periodic invocation (Vercel Cron -> /api/cron/process-reminders)
rather than an always-running background worker, since serverless functions
don't support long-lived processes. Idempotency is enforced via is_sent.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Event, Reminder, Task, User

settings = get_settings()
logger = logging.getLogger(__name__)


def create_reminder_for_event(db: Session, event: Event, minutes_before: int, reminder_type: str = "in_app") -> Reminder:
    reminder_time = event.start_datetime - timedelta(minutes=minutes_before)
    reminder = Reminder(
        event_id=event.id,
        user_id=event.user_id,
        title=f"{event.title} starts in {minutes_before} minutes",
        reminder_datetime=reminder_time,
        reminder_type=reminder_type,
    )
    db.add(reminder)
    return reminder


def create_reminder_for_task(db: Session, task: Task, reminder_time: datetime, reminder_type: str = "in_app") -> Reminder:
    reminder = Reminder(
        task_id=task.id,
        user_id=task.user_id,
        title=f"Task due: {task.title}",
        reminder_datetime=reminder_time,
        reminder_type=reminder_type,
    )
    db.add(reminder)
    return reminder


def _describe_when(reminder: Reminder, tz_name: str) -> tuple[str, str | None]:
    """Human-readable time and location for the reminder's target event."""
    from app.services.nlp_dates import ensure_aware_utc, get_tz

    event = reminder.event
    if not event:
        local = ensure_aware_utc(reminder.reminder_datetime).astimezone(get_tz(tz_name))
        return local.strftime("%A, %d %b at %I:%M %p"), None

    local = ensure_aware_utc(event.start_datetime).astimezone(get_tz(tz_name))
    return local.strftime("%A, %d %b at %I:%M %p"), event.location


def process_due_reminders(db: Session, now: datetime | None = None) -> dict:
    """
    Deliver every reminder that has come due, across all enabled channels.

    Idempotent: a reminder is only ever marked is_sent once, so running this
    from both the in-process scheduler and an external cron ping is safe.
    Email/push failures are retried on later ticks (up to 3 attempts) before
    the reminder is retired as failed — a delivery problem must not wedge the
    queue or spam the professor on every tick.
    """
    from app.services.notifier import (
        email_configured,
        push_configured,
        reminder_email_html,
        send_email,
        send_push_to_user,
    )

    now = now or datetime.now(timezone.utc)
    due = (
        db.query(Reminder)
        .filter(Reminder.is_sent.is_(False), Reminder.reminder_datetime <= now)
        .all()
    )

    sent = failed = emails = pushes = 0

    for reminder in due:
        try:
            user = db.get(User, reminder.user_id)
            if not user:
                reminder.is_sent = True
                reminder.sent_at = now
                reminder.delivery_status = "failed"
                failed += 1
                continue

            title = reminder.title or "Reminder"
            when_text, location = _describe_when(reminder, user.timezone)
            channel_attempted = channel_ok = False

            # --- Email ---
            if user.notify_email and email_configured():
                channel_attempted = True
                body = f"{title}\n\n{when_text}" + (f"\n{location}" if location else "")
                if send_email(user.email, f"🔔 {title}", body, reminder_email_html(title, when_text, location)):
                    channel_ok = True
                    emails += 1

            # --- Web Push ---
            if user.notify_push and push_configured():
                channel_attempted = True
                n = send_push_to_user(db, user, title, when_text)
                if n:
                    channel_ok = True
                    pushes += n

            # If an external channel was attempted but every one failed, retry
            # on a later tick rather than silently marking it delivered.
            if channel_attempted and not channel_ok:
                reminder.retry_count += 1
                if reminder.retry_count < 3:
                    continue
                reminder.delivery_status = "failed"
                failed += 1
            else:
                reminder.delivery_status = "sent"
                sent += 1

            # In-app delivery is implicit: the notification centre lists
            # reminders once they are marked sent.
            reminder.is_sent = True
            reminder.sent_at = now

        except Exception:
            logger.exception("Reminder %s failed to deliver", reminder.id)
            reminder.retry_count += 1
            failed += 1

    db.commit()
    return {"processed": len(due), "sent": sent, "failed": failed, "emails": emails, "pushes": pushes}
