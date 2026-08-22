"""
Reminder creation and idempotent delivery processing.

Delivery is driven by a periodic invocation (Vercel Cron -> /api/cron/process-reminders)
rather than an always-running background worker, since serverless functions
don't support long-lived processes. Idempotency is enforced via is_sent.
"""
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Event, Reminder, Task, User

settings = get_settings()


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


def _send_email(to_email: str, subject: str, body: str) -> bool:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
        msg["To"] = to_email
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
            server.sendmail(msg["From"], [to_email], msg.as_string())
        return True
    except Exception:
        return False


def process_due_reminders(db: Session, now: datetime | None = None) -> dict:
    """
    Find reminders due for delivery, mark them sent (idempotent), and
    dispatch email reminders where applicable. In-app/browser reminders are
    surfaced by the notifications API polling is_sent=False AND due<=now,
    so marking is_sent here is what "delivers" them to the UI.
    """
    now = now or datetime.now(timezone.utc)
    due = (
        db.query(Reminder)
        .filter(Reminder.is_sent.is_(False), Reminder.reminder_datetime <= now)
        .all()
    )

    sent, failed = 0, 0
    for reminder in due:
        try:
            if reminder.reminder_type == "email":
                user = db.get(User, reminder.user_id)
                ok = _send_email(user.email, reminder.title or "Reminder", reminder.title or "")
                if not ok:
                    reminder.retry_count += 1
                    if reminder.retry_count < 3:
                        continue  # leave is_sent False, retry next cron tick
                    reminder.delivery_status = "failed"
                    failed += 1
                    reminder.is_sent = True
                    reminder.sent_at = now
                    continue

            reminder.is_sent = True
            reminder.sent_at = now
            reminder.delivery_status = "sent"
            sent += 1
        except Exception:
            reminder.retry_count += 1
            failed += 1

    db.commit()
    return {"processed": len(due), "sent": sent, "failed": failed}
