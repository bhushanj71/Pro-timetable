"""
Carrying out a parsed Work command.

Every action here goes through the same work_service functions the REST
endpoints use, so the permission rules are enforced once and a spoken command
cannot do anything a tapped button could not.

Read-only answers come back immediately. Anything that writes to another
person's workload -- an invitation, an assignment -- is returned for
confirmation instead, because a misheard name should not add someone to a
team or put work on their plate.
"""
from sqlalchemy.orm import selectinload

from app.models import (
    AssignmentStatus,
    Community,
    CommunityMember,
    TaskAssignment,
    User,
    WorkTask,
)
from app.schemas import AIExtractionResult, AIPromptResponse
from app.services import work_service as ws


def _reply(intent: str, summary: str, *, matches=None, action="view", confirm=False):
    return AIPromptResponse(
        intent=intent,
        extraction=AIExtractionResult(intent=intent),
        summary=summary,
        requires_confirmation=confirm,
        matches=matches or [],
        action=action,
    )


def _my_communities(db, user):
    return ws.user_communities(db, user)


def _find_community(db, user, name: str | None):
    """Match a community by name among the ones the caller is in.

    Scoped to their own memberships: a name is not a lookup key into other
    people's teams.
    """
    mine = _my_communities(db, user)
    if not name:
        return mine[0] if len(mine) == 1 else None
    needle = name.lower().strip()
    exact = [c for c in mine if c.name.lower() == needle]
    if exact:
        return exact[0]
    partial = [c for c in mine if needle in c.name.lower()]
    return partial[0] if len(partial) == 1 else None


def _find_member(db, user, community, name: str | None):
    if not name:
        return None
    needle = name.lower().strip()
    people = [m.user for m in community.members]
    exact = [u for u in people if u.name.lower() == needle]
    if exact:
        return exact[0]
    partial = [u for u in people if needle in u.name.lower()]
    return partial[0] if len(partial) == 1 else None


def _my_assignments(db, user):
    return (
        db.query(TaskAssignment)
        .options(
            selectinload(TaskAssignment.task).selectinload(WorkTask.community),
            selectinload(TaskAssignment.task).selectinload(WorkTask.assignments),
        )
        .filter(TaskAssignment.user_id == user.id)
        .all()
    )


def _match_task(assignments, name: str | None):
    if not assignments:
        return None
    if not name:
        return assignments[0]
    needle = name.lower().strip()
    hits = [a for a in assignments if needle in a.task.title.lower()]
    return hits[0] if len(hits) == 1 else None


def execute(db, user: User, cmd: dict):
    intent = cmd["intent"]

    # ---------------- Read-only ----------------
    if intent == "VIEW_COMMUNITIES":
        mine = _my_communities(db, user)
        if not mine:
            return _reply(intent, "You're not in any communities yet.")
        return _reply(
            intent,
            f"You're in {len(mine)}: " + ", ".join(c.name for c in mine),
            matches=[{"id": c.id, "title": c.name, "when": f"{len(c.members)} members"} for c in mine],
        )

    if intent == "VIEW_REQUESTS":
        pending = [a for a in _my_assignments(db, user) if a.status == AssignmentStatus.PENDING.value]
        if not pending:
            return _reply(intent, "Nothing is waiting on your answer.")
        return _reply(
            intent,
            f"{len(pending)} task{'s' if len(pending) != 1 else ''} waiting for you to accept or decline.",
            matches=[{"id": a.task.id, "title": a.task.title, "when": a.task.community.name} for a in pending],
        )

    if intent == "VIEW_MY_TASKS":
        active = [
            a for a in _my_assignments(db, user)
            if a.status in (AssignmentStatus.ACCEPTED.value, AssignmentStatus.IN_PROGRESS.value)
        ]
        if not active:
            return _reply(intent, "You have no active tasks. Accepted work shows up here.")
        return _reply(
            intent,
            f"{len(active)} active: " + ", ".join(f"{a.task.title} ({a.progress}%)" for a in active[:4]),
            matches=[{"id": a.task.id, "title": a.task.title, "when": f"{a.progress}%"} for a in active],
        )

    if intent == "VIEW_ASSIGNED_BY_ME":
        tasks = (
            db.query(WorkTask)
            .options(selectinload(WorkTask.assignments), selectinload(WorkTask.community))
            .filter(WorkTask.created_by == user.id)
            .all()
        )
        if not tasks:
            return _reply(intent, "You haven't assigned any tasks yet.")
        return _reply(
            intent,
            f"You've assigned {len(tasks)} task{'s' if len(tasks) != 1 else ''}.",
            matches=[
                {"id": t.id, "title": t.title, "when": f"{ws.task_progress(t)['overall']}% overall"}
                for t in tasks
            ],
        )

    if intent == "TASK_PROGRESS_QUERY":
        tasks = (
            db.query(WorkTask)
            .options(selectinload(WorkTask.assignments).selectinload(TaskAssignment.user))
            .filter(WorkTask.created_by == user.id)
            .all()
        )
        mine = [a.task for a in _my_assignments(db, user)]
        pool = {t.id: t for t in tasks + mine}.values()

        needle = (cmd.get("task") or "").lower().strip()
        hits = [t for t in pool if not needle or needle in t.title.lower()]
        if not hits:
            return _reply(intent, f"I couldn't find a task matching “{cmd.get('task')}”.")
        t = hits[0]
        p = ws.task_progress(t)
        who = ", ".join(
            f"{a.user.name} {a.progress}%" for a in t.assignments
            if a.status != AssignmentStatus.DECLINED.value
        )
        return _reply(intent, f"{t.title} is at {p['overall']}% overall. {who}".strip())

    # ---------------- Writes on my own work ----------------
    if intent == "SET_TASK_PROGRESS":
        assignments = [
            a for a in _my_assignments(db, user)
            if a.status in (AssignmentStatus.ACCEPTED.value, AssignmentStatus.IN_PROGRESS.value,
                            AssignmentStatus.COMPLETED.value)
        ]
        a = _match_task(assignments, cmd.get("task"))
        if not a:
            return _reply(intent, "I couldn't tell which of your tasks you meant. Open it and set the progress there.")
        ws.update_progress(db, a, user, cmd["progress"], None)
        db.commit()
        return _reply(intent, f"{a.task.title} is now at {a.progress}%.", action="update")

    if intent == "RESPOND_TASK":
        pending = [a for a in _my_assignments(db, user) if a.status == AssignmentStatus.PENDING.value]
        a = _match_task(pending, cmd.get("task"))
        if not a:
            return _reply(intent, "I couldn't find a pending task matching that.")
        ws.respond_to_assignment(db, a, user, cmd["accept"])
        db.commit()
        verb = "accepted" if cmd["accept"] else "declined"
        return _reply(intent, f"You {verb} “{a.task.title}”.", action="update")

    # ---------------- Writes that affect other people ----------------
    # These are returned for confirmation rather than executed. A misheard name
    # should not add someone to a team or put work on their plate.
    if intent == "CREATE_COMMUNITY":
        name = cmd.get("name")
        if not name:
            return _reply(intent, "What should the community be called?")
        community = ws.create_community(db, user, name, None, None)
        db.commit()
        return _reply(intent, f"Created “{community.name}”. You're the owner — invite people from the Work page.",
                      action="create")

    if intent == "INVITE_MEMBER":
        community = _find_community(db, user, cmd.get("community"))
        if not community:
            return _reply(intent, "I couldn't tell which community you meant. Open it and invite from there.")
        return _reply(
            intent,
            f"Invite {cmd.get('person')} to {community.name}? Open the community to send it — "
            "invitations need an email address so the right person gets it.",
        )

    if intent == "ASSIGN_TASK":
        community = _find_community(db, user, None)
        people = cmd.get("people") or []
        if not community:
            return _reply(intent, "Which community is this task for? Open it and assign from there.")

        resolved = [(p, _find_member(db, user, community, p)) for p in people]
        missing = [p for p, u in resolved if not u]
        if missing:
            return _reply(
                intent,
                f"I couldn't find {', '.join(missing)} in {community.name}. "
                "They need to be a member before work can be assigned to them.",
            )

        names = ", ".join(u.name for _, u in resolved)
        return _reply(
            intent,
            f"Assign “{cmd.get('title')}” to {names} in {community.name}? "
            "Open the community to send it — each person is asked to accept first.",
        )

    return None
