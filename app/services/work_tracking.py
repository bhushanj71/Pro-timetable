"""What each member is carrying, what they finished, and what is late.

Everything here reads the existing Work tables. A task's shape -- WorkTask for
the thing itself, TaskAssignment for one person's share of it -- already
carries progress, status and dates, so nothing is duplicated: this module is
the questions asked of that data, not a second copy of it.

Two ideas do the work.

First, a member's state on a task is their *assignment*, not the task. Three
people can share one task and be in three different states, and an overview
that reported the task's status would tell the owner nothing about the person
they clicked on.

Second, "overdue" and "incomplete" are derived, never stored. A stored flag is
wrong the moment a clock ticks past a due date with nobody looking, and would
need a sweep to keep true. Computing them at read time means they cannot drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AssignmentStatus,
    CommunityMember,
    CommunityRole,
    TaskAssignment,
    TaskAttachment,
    User,
    WorkTask,
)
from app.services.nlp_dates import ensure_aware_utc, get_tz

# The five buckets the owner asked to see. They are not the same as
# AssignmentStatus: two of them are conditions rather than states.
#
#   pending      assigned, not yet answered
#   in_progress  accepted and under way
#   completed    finished
#   overdue      past its due date and not finished -- cuts across the others
#   incomplete   declined, or accepted and abandoned past its date
BUCKETS = ("pending", "in_progress", "completed", "incomplete", "overdue")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def bucket_of(assignment: TaskAssignment, task: WorkTask, *, now: datetime | None = None) -> str:
    """Which of the five an assignment falls in, right now.

    Order matters. Completed wins over overdue, because work finished late is
    finished -- flagging it red forever helps nobody. Declined is incomplete
    regardless of the date: it was never going to be done.
    """
    now = now or _now()
    if assignment.status == AssignmentStatus.COMPLETED.value:
        return "completed"
    if assignment.status == AssignmentStatus.DECLINED.value:
        return "incomplete"
    if task.due_date and ensure_aware_utc(task.due_date) < now:
        return "overdue"
    if assignment.status == AssignmentStatus.PENDING.value:
        return "pending"
    return "in_progress"


def is_overdue(assignment: TaskAssignment, task: WorkTask, *, now: datetime | None = None) -> bool:
    return bucket_of(assignment, task, now=now) == "overdue"


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def _rows(db: Session, community_id: str, *, user_id: str | None = None):
    """Assignments in a community, with everything a caller needs eagerly.

    One query with the task, its assignee and its attachment count joined --
    the alternative is a query per row, which on a busy community is what
    turns a dashboard into a spinner.
    """
    q = (
        db.query(TaskAssignment)
        .join(WorkTask, TaskAssignment.task_id == WorkTask.id)
        .options(
            selectinload(TaskAssignment.task).selectinload(WorkTask.creator),
            selectinload(TaskAssignment.user),
        )
        .filter(WorkTask.community_id == community_id)
    )
    if user_id:
        q = q.filter(TaskAssignment.user_id == user_id)
    return q.order_by(WorkTask.due_date.is_(None), WorkTask.due_date, WorkTask.created_at.desc()).all()


def attachment_counts(db: Session, task_ids: list[str]) -> dict[str, int]:
    """How many files each task carries, without loading any of them."""
    if not task_ids:
        return {}
    rows = (
        db.query(TaskAttachment.task_id, func.count(TaskAttachment.id))
        .filter(TaskAttachment.task_id.in_(task_ids))
        .group_by(TaskAttachment.task_id)
        .all()
    )
    return {task_id: count for task_id, count in rows}


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Period:
    start: datetime | None
    end: datetime | None
    label: str


def resolve_period(name: str | None, tz_name: str,
                   start: str | None = None, end: str | None = None) -> Period:
    """A named window, in the viewer's own timezone.

    "Today" has to mean their today. Computing it in UTC puts an Indian
    professor's morning in yesterday's bucket for five and a half hours of
    every day.
    """
    tz = get_tz(tz_name or "UTC")
    today = datetime.now(tz).date()

    def at(d: date, t: time) -> datetime:
        return datetime.combine(d, t, tzinfo=tz).astimezone(timezone.utc)

    if name == "today":
        return Period(at(today, time.min), at(today, time.max), "Today")
    if name == "week":
        monday = today - timedelta(days=today.weekday())
        return Period(at(monday, time.min), at(monday + timedelta(days=6), time.max), "This week")
    if name == "month":
        first = today.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return Period(at(first, time.min), at(nxt - timedelta(days=1), time.max), "This month")
    if name == "custom" and (start or end):
        s = date.fromisoformat(start) if start else None
        e = date.fromisoformat(end) if end else None
        return Period(
            at(s, time.min) if s else None,
            at(e, time.max) if e else None,
            f"{s.strftime('%d %b %Y') if s else 'the beginning'} – {e.strftime('%d %b %Y') if e else 'now'}",
        )
    return Period(None, None, "All time")


def _in_period(assignment: TaskAssignment, task: WorkTask, period: Period) -> bool:
    """Judged on when the work happened, not when the row was written.

    A task completed inside the window counts even if it was assigned months
    before, which is what "what did they do this week" actually means.
    """
    if period.start is None and period.end is None:
        return True
    stamp = assignment.completed_at or assignment.responded_at or assignment.assigned_at
    stamp = ensure_aware_utc(stamp)
    if period.start and stamp < period.start:
        return False
    if period.end and stamp > period.end:
        return False
    return True


# --------------------------------------------------------------------------
# One member
# --------------------------------------------------------------------------
def member_tally(assignments: list[tuple[TaskAssignment, WorkTask]], *, now=None) -> dict:
    counts = {b: 0 for b in BUCKETS}
    for assignment, task in assignments:
        counts[bucket_of(assignment, task, now=now)] += 1

    assigned = len(assignments)
    # Completion is out of everything they hold, declined work included. A
    # figure that quietly dropped declines would let someone reach 100% by
    # turning work down.
    done = counts["completed"]
    return {
        "assigned": assigned,
        **counts,
        "completion": round(done / assigned * 100) if assigned else 0,
    }


def member_overview(db: Session, community_id: str, user_id: str, *,
                    period: Period | None = None, statuses: set[str] | None = None,
                    now=None) -> dict:
    """Everything one person is carrying, bucketed, with their tally."""
    rows = _rows(db, community_id, user_id=user_id)
    pairs = [(a, a.task) for a in rows]

    # The tally describes the whole picture; the filters describe what is on
    # screen. Recomputing the tally from the filtered list would make the
    # completion figure change every time a filter moved, which is nonsense.
    tally = member_tally(pairs, now=now)

    if period:
        pairs = [(a, t) for a, t in pairs if _in_period(a, t, period)]
    if statuses:
        pairs = [(a, t) for a, t in pairs if bucket_of(a, t, now=now) in statuses]

    counts = attachment_counts(db, [t.id for _, t in pairs])
    return {"tally": tally, "pairs": pairs, "attachment_counts": counts}


# --------------------------------------------------------------------------
# The whole community
# --------------------------------------------------------------------------
def community_overview(db: Session, community_id: str, *, now=None) -> dict:
    """The owner's headline numbers, and a row per member beneath them."""
    now = now or _now()
    rows = _rows(db, community_id)

    members = (
        db.query(CommunityMember)
        .options(selectinload(CommunityMember.user))
        .filter(CommunityMember.community_id == community_id)
        .all()
    )

    by_member: dict[str, list[tuple[TaskAssignment, WorkTask]]] = {m.user_id: [] for m in members}
    for a in rows:
        # A former member's assignments still count in the totals -- the work
        # happened -- but they have no row of their own to appear in.
        by_member.setdefault(a.user_id, []).append((a, a.task))

    totals = {b: 0 for b in BUCKETS}
    for a in rows:
        totals[bucket_of(a, a.task, now=now)] += 1

    performance = []
    for m in members:
        tally = member_tally(by_member.get(m.user_id, []), now=now)
        performance.append({
            "user": m.user,
            "role": m.role,
            **tally,
        })
    # Busiest first: the owner is looking for who is loaded, not for the
    # alphabet. People with nothing assigned sort last rather than leading.
    performance.sort(key=lambda p: (-p["assigned"], p["user"].name.lower()))

    distinct_tasks = {a.task_id for a in rows}
    return {
        "members_total": len(members),
        "tasks_total": len(distinct_tasks),
        "active": totals["pending"] + totals["in_progress"],
        **totals,
        "performance": performance,
    }


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
def search(db: Session, community_id: str, query: str, *, limit: int = 40):
    """By member name, task title, or task id.

    The id match is a prefix rather than equality: nobody types a whole uuid,
    they paste the first chunk of one out of a URL or a notification.
    """
    text = (query or "").strip()
    if not text:
        return []
    like = f"%{text.lower()}%"
    return (
        db.query(TaskAssignment)
        .join(WorkTask, TaskAssignment.task_id == WorkTask.id)
        .join(User, TaskAssignment.user_id == User.id)
        .options(
            selectinload(TaskAssignment.task).selectinload(WorkTask.creator),
            selectinload(TaskAssignment.user),
        )
        .filter(
            WorkTask.community_id == community_id,
            or_(
                func.lower(WorkTask.title).like(like),
                func.lower(User.name).like(like),
                WorkTask.id.like(f"{text}%"),
            ),
        )
        .order_by(WorkTask.created_at.desc())
        .limit(limit)
        .all()
    )
