"""
Work notifications: what gets sent, to whom, and what does not.

Three rules shape everything here.

Never notify someone about their own action. Accepting your own task should
not put "you accepted your task" in your inbox.

Never send the same thing twice. Deadline and chase-up notices come from a
sweep that runs every few minutes; without a de-duplication key each pass
would resend "due tomorrow" until the deadline arrived.

Never let a preference hide work someone is waiting on. Assignments are not a
switchable category: the pending list is an obligation, not a newsletter.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, selectinload

from app.models import (
    AssignmentStatus,
    TaskAssignment,
    User,
    WorkNotification,
    WorkTask,
    WorkTaskStatus,
)

logger = logging.getLogger(__name__)


class Kind:
    TASK_ASSIGNED = "task_assigned"
    TASK_ACCEPTED = "task_accepted"
    TASK_DECLINED = "task_declined"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_ALL_COMPLETED = "task_all_completed"
    TASK_UPDATED = "task_updated"
    TASK_DUE_SOON = "task_due_soon"
    TASK_DUE = "task_due"
    TASK_OVERDUE = "task_overdue"
    ASSIGNMENT_REMINDER = "assignment_reminder"
    TASK_COMMENT = "task_comment"
    COMMUNITY_INVITE = "community_invite"
    INVITE_ACCEPTED = "invite_accepted"
    INVITE_DECLINED = "invite_declined"
    REMOVED = "removed_from_community"
    COMMUNITY_DELETED = "community_deleted"
    TASK_UNASSIGNED = "task_unassigned"


# Which preference switch governs each kind. Anything absent is unswitchable.
_PREFERENCE = {
    Kind.TASK_ACCEPTED: "notify_work_responses",
    Kind.TASK_DECLINED: "notify_work_responses",
    Kind.TASK_STARTED: "notify_work_progress",
    Kind.TASK_PROGRESS: "notify_work_progress",
    Kind.TASK_COMPLETED: "notify_work_completion",
    Kind.TASK_ALL_COMPLETED: "notify_work_completion",
    Kind.TASK_DUE_SOON: "notify_work_deadlines",
    Kind.TASK_DUE: "notify_work_deadlines",
    Kind.TASK_OVERDUE: "notify_work_deadlines",
    Kind.ASSIGNMENT_REMINDER: "notify_work_deadlines",
    Kind.TASK_COMMENT: "notify_work_community",
    Kind.COMMUNITY_INVITE: "notify_work_community",
    Kind.INVITE_ACCEPTED: "notify_work_community",
    Kind.INVITE_DECLINED: "notify_work_community",
    # TASK_ASSIGNED, REMOVED and COMMUNITY_DELETED are deliberately unlisted:
    # being given work, or losing a community and everything in it, is not
    # optional information.
}


def wants(db: Session, user_id: str, kind: str) -> bool:
    pref = _PREFERENCE.get(kind)
    if not pref:
        return True
    user = db.get(User, user_id)
    return bool(getattr(user, pref, True)) if user else False


def send(
    db: Session,
    *,
    to: str,
    kind: str,
    title: str,
    body: str | None = None,
    actor: str | None = None,
    community_id: str | None = None,
    task_id: str | None = None,
    assignment_id: str | None = None,
    dedupe_key: str | None = None,
) -> WorkNotification | None:
    """Queue one notification, or return None if it should not be sent."""
    if actor and actor == to:
        return None                      # never notify someone about themselves
    if not wants(db, to, kind):
        return None

    if dedupe_key:
        already = (
            db.query(WorkNotification)
            .filter(WorkNotification.user_id == to, WorkNotification.dedupe_key == dedupe_key)
            .first()
        )
        if already:
            return None

    n = WorkNotification(
        user_id=to, actor_id=actor, kind=kind, title=title, body=body,
        community_id=community_id, task_id=task_id, assignment_id=assignment_id,
        dedupe_key=dedupe_key,
    )
    db.add(n)
    return n


# ---------------------------------------------------------------------------
# The deadline / chase-up sweep
# ---------------------------------------------------------------------------
# How close a deadline has to be, and the label each window gets. Ordered
# tightest-first so a task an hour away is announced as "in 1 hour" rather
# than "tomorrow".
_DUE_WINDOWS = [
    (timedelta(hours=1), Kind.TASK_DUE, "is due within the hour"),
    (timedelta(hours=6), Kind.TASK_DUE_SOON, "is due in a few hours"),
    (timedelta(days=1), Kind.TASK_DUE_SOON, "is due tomorrow"),
]

# How long an unanswered assignment sits before its owner is nudged, and how
# many times. Two nudges, then silence: a third is nagging, and the request is
# still sitting in their Work dashboard regardless.
ASSIGNMENT_NUDGE_AFTER = timedelta(hours=24)
ASSIGNMENT_NUDGE_LIMIT = 2


def sweep_work_deadlines(db: Session, now: datetime | None = None) -> dict:
    """Generate deadline, overdue and unanswered-assignment notifications.

    Idempotent: every notification carries a dedupe key naming the task and
    the window, so running this every few minutes sends each one once.
    """
    now = now or datetime.now(timezone.utc)
    counts = {"due_soon": 0, "overdue": 0, "nudges": 0}

    live = (
        db.query(TaskAssignment)
        .options(
            selectinload(TaskAssignment.task).selectinload(WorkTask.community),
            selectinload(TaskAssignment.user),
        )
        .join(WorkTask, TaskAssignment.task_id == WorkTask.id)
        .filter(
            WorkTask.status.notin_([WorkTaskStatus.COMPLETED.value, WorkTaskStatus.CANCELLED.value]),
            TaskAssignment.status.notin_([AssignmentStatus.DECLINED.value, AssignmentStatus.COMPLETED.value]),
        )
        .all()
    )

    for a in live:
        task = a.task

        # --- Unanswered assignments ---
        if a.status == AssignmentStatus.PENDING.value:
            waited = now - _aware(a.assigned_at)
            if waited >= ASSIGNMENT_NUDGE_AFTER:
                nudge_no = min(int(waited / ASSIGNMENT_NUDGE_AFTER), ASSIGNMENT_NUDGE_LIMIT)
                if nudge_no >= 1 and send(
                    db, to=a.user_id, kind=Kind.ASSIGNMENT_REMINDER,
                    title=f"Still waiting on your answer: “{task.title}”",
                    body=f"{task.community.name} · assigned {waited.days} day(s) ago",
                    task_id=task.id, assignment_id=a.id, community_id=task.community_id,
                    dedupe_key=f"nudge:{a.id}:{nudge_no}",
                ):
                    counts["nudges"] += 1
            # A task nobody has accepted has no meaningful deadline yet.
            continue

        if not task.due_date:
            continue

        due = _aware(task.due_date)
        remaining = due - now

        # --- Overdue ---
        if remaining < timedelta(0):
            if send(
                db, to=a.user_id, kind=Kind.TASK_OVERDUE,
                title=f"“{task.title}” is overdue",
                body=f"Was due {due.strftime('%d %b, %I:%M %p').replace(' 0', ' ')}",
                task_id=task.id, assignment_id=a.id, community_id=task.community_id,
                dedupe_key=f"overdue:{a.id}",
            ):
                counts["overdue"] += 1
            continue

        # --- Approaching ---
        for window, kind, phrase in _DUE_WINDOWS:
            if remaining <= window:
                if send(
                    db, to=a.user_id, kind=kind,
                    title=f"“{task.title}” {phrase}",
                    body=f"{task.community.name} · you're at {a.progress}%",
                    task_id=task.id, assignment_id=a.id, community_id=task.community_id,
                    dedupe_key=f"due:{a.id}:{int(window.total_seconds())}",
                ):
                    counts["due_soon"] += 1
                break   # tightest window only

    db.commit()
    return counts


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Push delivery
# ---------------------------------------------------------------------------
# Notifications that describe something the professor just did themselves.
# They belong in the bell as a record, but pushing them buzzes the phone that
# is already in their hand for an action they just took.
_SELF_AUTHORED = {
    "event_created", "event_updated", "event_deleted", "event_cancelled",
    "personal_task_created", "personal_task_completed", "personal_task_deleted",
    "personal_reminder_set",
}

PUSH_ICON = {
    Kind.TASK_ASSIGNED: "\U0001F4E5", Kind.TASK_ACCEPTED: "✅",
    Kind.TASK_DECLINED: "❌", Kind.TASK_STARTED: "\U0001F680",
    Kind.TASK_PROGRESS: "\U0001F4CA", Kind.TASK_COMPLETED: "\U0001F389",
    Kind.TASK_ALL_COMPLETED: "\U0001F3C1", Kind.TASK_DUE_SOON: "⏰",
    Kind.TASK_DUE: "⏰", Kind.TASK_OVERDUE: "\U0001F6A8",
    Kind.ASSIGNMENT_REMINDER: "\U0001F4E5", Kind.TASK_COMMENT: "\U0001F4AC",
    Kind.COMMUNITY_INVITE: "\U0001F465",
    Kind.COMMUNITY_DELETED: "\U0001F5D1",
}


def deliver_pending_pushes(db: Session, limit: int = 200) -> dict:
    """Push every bell notification that hasn't reached a device yet.

    Claims rows by stamping pushed_at, so this is safe to run from the request
    path and the cron sweep at once: whichever gets there first wins and the
    other finds nothing to do.
    """
    from app.models import PushSubscription
    from app.services.notifier import push_configured, send_push_to_user

    if not push_configured():
        return {"pushed": 0, "skipped": "push is not configured on this server"}

    pending = (
        db.query(WorkNotification)
        .filter(WorkNotification.pushed_at.is_(None))
        .order_by(WorkNotification.created_at)
        .limit(limit)
        .all()
    )
    if not pending:
        return {"pushed": 0}

    now = datetime.now(timezone.utc)
    devices: dict[str, int] = {}
    pushed = 0

    for n in pending:
        # Stamped whatever happens next: a notification that cannot be pushed
        # is not one to retry forever, and it is already in the bell.
        n.pushed_at = now

        if n.kind in _SELF_AUTHORED:
            continue

        user = db.get(User, n.user_id)
        if not user or not user.notify_push:
            continue

        if user.id not in devices:
            devices[user.id] = (
                db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
            )
        if not devices[user.id]:
            continue

        icon = PUSH_ICON.get(n.kind, "\U0001F514")
        # Deep-links to the task so the notification lands somewhere useful
        # rather than on a dashboard the professor has to search.
        #
        # This used to be /work?task=<id>, and nothing ever read that query
        # parameter -- the link opened the dashboard and dropped the id on the
        # floor. Now the task is a real page, so the URL is the task.
        url = f"/work/task/{n.task_id}" if n.task_id else "/work"
        try:
            pushed += send_push_to_user(db, user, f"{icon} {n.title}", n.body or "", url=url)
        except Exception:
            logger.exception("Could not push notification %s", n.id)

    db.commit()
    return {"pushed": pushed, "considered": len(pending)}


def deliver_in_background() -> None:
    """Push whatever is pending, on its own session, after the response.

    Handed to FastAPI's BackgroundTasks so a web-push round trip -- which can
    take seconds per device -- never sits inside the request the professor is
    waiting on.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        deliver_pending_pushes(db)
    except Exception:
        logger.exception("Background push delivery failed")
    finally:
        db.close()
