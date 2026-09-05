"""
Reminder creation and idempotent delivery processing.

Delivery is driven by a periodic invocation (Vercel Cron -> /api/cron/process-reminders)
rather than an always-running background worker, since serverless functions
don't support long-lived processes. Idempotency is enforced via is_sent.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

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


# Every scheduled item also gets a short "starting now" nudge, on top of the
# professor's own lead time.
LAST_CALL_MINUTES = 5


def schedule_event_reminders(db: Session, event: Event, user: User, leads: list[int] | None = None) -> int:
    """Create this event's reminders: the professor's lead time plus a 5-minute
    final nudge.

    Deduplicated (a 5-minute default would otherwise produce two identical
    reminders) and skips any whose time has already passed, so backfilling a
    long recurring series cannot dump a pile of overdue reminders.
    """
    if leads is None:
        leads = [user.default_reminder_minutes or 30]
    elif not leads:
        return 0  # explicit opt-out: not even the last-call nudge
    wanted = sorted({m for m in list(leads) + [LAST_CALL_MINUTES] if m and m > 0}, reverse=True)

    now = datetime.now(timezone.utc)
    start = event.start_datetime
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    created = 0
    for minutes in wanted:
        if start - timedelta(minutes=minutes) <= now:
            continue
        create_reminder_for_event(db, event, minutes)
        created += 1
    return created


def schedule_task_reminders(db: Session, task: Task, user: User) -> int:
    """A day-ahead heads-up plus a 5-minute nudge before the deadline."""
    if not task.due_date:
        return 0

    due = task.due_date if task.due_date.tzinfo else task.due_date.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    created = 0
    for when, label in (
        (due - timedelta(days=1), f"Due tomorrow: {task.title}"),
        (due - timedelta(minutes=LAST_CALL_MINUTES), f"Due in {LAST_CALL_MINUTES} minutes: {task.title}"),
    ):
        if when <= now:
            continue
        db.add(
            Reminder(
                task_id=task.id,
                user_id=task.user_id,
                title=label,
                reminder_datetime=when,
            )
        )
        created += 1
    return created


def lead_minutes(reminder: Reminder) -> int | None:
    """How far ahead of its event a reminder is set, in minutes."""
    if not reminder.event:
        return None
    delta = reminder.event.start_datetime - reminder.reminder_datetime
    if delta.total_seconds() < 0:
        return None
    return int(delta.total_seconds() // 60)


def resync_event_reminders(db: Session, event: Event) -> int:
    """Re-anchor an event's pending reminders after its time moved.

    Each reminder keeps its own lead: a professor who set one alert a day
    ahead and another five minutes before must still have both, at the same
    distances, once the lecture shifts. Recomputing from a single default --
    which is what the update path used to do -- silently collapsed them onto
    the same time.

    Reminders already delivered are left alone; rewriting history would make
    the notification feed disagree with what actually arrived.
    """
    moved = 0
    for rem in event.reminders:
        if rem.is_sent:
            continue
        lead = _stored_lead(rem)
        if lead is None:
            continue
        rem.reminder_datetime = event.start_datetime - timedelta(minutes=lead)
        rem.title = f"{event.title} starts in {lead} minutes"
        moved += 1
    return moved


def _stored_lead(reminder: Reminder) -> int | None:
    """The lead a reminder was created with.

    Read from the title rather than the timestamps, because by the time an
    event has moved the timestamps describe the *new* gap, not the intended
    one.
    """
    import re

    m = re.search(r"in (\d+) minutes?$", reminder.title or "")
    if m:
        return int(m.group(1))
    return lead_minutes(reminder)


def set_reminder_lead(db: Session, event: Event, user: User, minutes: int) -> int:
    """Replace an event's pending reminders with a single one at `minutes`."""
    for rem in list(event.reminders):
        if not rem.is_sent:
            db.delete(rem)
    db.flush()
    return schedule_event_reminders(db, event, user, leads=[minutes])


def clear_event_reminders(db: Session, event: Event) -> int:
    """Turn reminders off for an event, without touching delivered history."""
    removed = 0
    for rem in list(event.reminders):
        if not rem.is_sent:
            db.delete(rem)
            removed += 1
    return removed


# A lecture and a lab want different framing on a lock screen: the icon is
# what the professor reads first when the phone is face-up on a desk.
_TYPE_ICON = {
    "lecture": "📚",         # books
    "lab": "💻",             # laptop
    "meeting": "👥",         # people
    "examination": "📝",     # memo
    "project_review": "🔍",  # magnifier
    "deadline": "⏰",            # alarm clock
}
_TYPE_LABEL = {
    "lecture": "Upcoming Lecture",
    "lab": "Upcoming Lab",
    "meeting": "Upcoming Meeting",
    "examination": "Upcoming Exam",
    "project_review": "Upcoming Review",
    "deadline": "Deadline",
}


def notification_title(reminder: Reminder) -> str:
    """Headline for the push/email, e.g. "📚 Upcoming Lecture"."""
    event = reminder.event
    if not event:
        # A standalone reminder ("submit the internal marks") already carries
        # everything it means in its own title. Replacing that with a generic
        # word would throw away the only content it has.
        return reminder.title or "🔔 Reminder"
    icon = _TYPE_ICON.get(event.event_type, "🔔")
    return f"{icon} {_TYPE_LABEL.get(event.event_type, 'Upcoming')}"


def _describe_when(reminder: Reminder, tz_name: str) -> tuple[str, str | None]:
    """Human-readable body and location for the reminder's target event.

    The body carries subject, time span, faculty and room, because a
    notification that only says "starts in 15 minutes" makes the professor
    open the app to find out where to walk.
    """
    from app.services.nlp_dates import ensure_aware_utc, get_tz

    event = reminder.event
    if not event:
        local = ensure_aware_utc(reminder.reminder_datetime).astimezone(get_tz(tz_name))
        return local.strftime("%A, %d %b at %I:%M %p"), None

    tz = get_tz(tz_name)
    start = ensure_aware_utc(event.start_datetime).astimezone(tz)
    end = ensure_aware_utc(event.end_datetime).astimezone(tz)

    lead = _stored_lead(reminder)
    fmt = lambda d: d.strftime("%I:%M %p").lstrip("0")

    lines = [event.subject or event.title, f"{fmt(start)} - {fmt(end)}"]
    if event.faculty:
        lines.append(event.faculty)
    if event.location:
        lines.append(event.location)
    if lead:
        lines.append(f"Starts in {lead} minutes")

    return " · ".join(lines), event.location


# How long one worker's claim on a reminder is honoured before another worker
# may take it. Long enough that a slow mail server does not cause a double
# send; short enough that a worker killed mid-delivery does not strand the
# reminder for the rest of the day.
CLAIM_LEASE_SECONDS = 300


def _claim(db: Session, due: list[Reminder], now: datetime) -> list[Reminder]:
    """Take exclusive ownership of the reminders this worker will deliver.

    The claim is a conditional UPDATE, so the database decides the winner: two
    workers issuing it for the same row cannot both match, and the loser is
    told by a row count of zero rather than by finding out afterwards.

    This is not only about running more than one instance. process_due_reminders
    is also called from the notification endpoint to flush a professor's overdue
    reminders, so two open tabs belonging to one person were already enough to
    deliver the same reminder twice.
    """
    stale = now - timedelta(seconds=CLAIM_LEASE_SECONDS)
    mine: list[Reminder] = []
    for reminder in due:
        won = (
            db.query(Reminder)
            .filter(
                Reminder.id == reminder.id,
                Reminder.is_sent.is_(False),
                or_(Reminder.claimed_at.is_(None), Reminder.claimed_at <= stale),
            )
            .update({"claimed_at": now}, synchronize_session=False)
        )
        if won:
            mine.append(reminder)

    # Committed before a single message goes out. An uncommitted claim is not
    # a claim: the whole point is that the other worker can see it.
    db.commit()
    return mine


def process_due_reminders(db: Session, now: datetime | None = None, user_id: str | None = None) -> dict:
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
    # Each reminder's event and user are read while composing the message, so
    # load them with the batch rather than one query per reminder.
    query = (
        db.query(Reminder)
        .options(selectinload(Reminder.event), selectinload(Reminder.user))
        .filter(Reminder.is_sent.is_(False), Reminder.reminder_datetime <= now)
    )
    # Scoped when called from a user's own request; unscoped for the cron run.
    if user_id:
        query = query.filter(Reminder.user_id == user_id)
    # Everything that looks due, then only the part this worker won.
    due = _claim(db, query.all(), now)

    sent = failed = emails = pushes = 0
    # user_id -> registered device count, resolved lazily and reused.
    device_counts: dict[str, int] = {}

    for reminder in due:
        try:
            user = db.get(User, reminder.user_id)
            if not user:
                reminder.is_sent = True
                reminder.sent_at = now
                reminder.delivery_status = "failed"
                failed += 1
                continue

            # The lock-screen headline says what kind of thing this is; the
            # body carries subject, time, faculty and room.
            title = notification_title(reminder)
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
            # Having no registered device is not a delivery failure — there is
            # simply nothing to push to. Treating it as one meant a professor
            # who never enabled push got no notifications at all, because the
            # reminder retried and then failed instead of falling through to
            # in-app delivery.
            if user.notify_push and push_configured():
                # Counted once per user, not once per reminder. A sweep
                # delivering 60 reminders was running 60 identical COUNT
                # queries against push_subscriptions -- the same answer, sixty
                # times, for one professor.
                if user.id not in device_counts:
                    from app.models import PushSubscription

                    device_counts[user.id] = (
                        db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
                    )
                if device_counts[user.id]:
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
                    # Hand the claim back rather than making the next attempt
                    # wait out the lease.
                    reminder.claimed_at = None
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
            reminder.claimed_at = None
            failed += 1

    db.commit()
    return {"processed": len(due), "sent": sent, "failed": failed, "emails": emails, "pushes": pushes}
