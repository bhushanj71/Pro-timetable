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
    # TASK_ASSIGNED and REMOVED are deliberately unlisted: being given work,
    # or losing access to a community, is not optional information.
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
