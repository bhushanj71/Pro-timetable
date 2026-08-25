"""
Work-mode rules: membership, permissions, task acceptance and progress.

Every permission check lives here rather than in the router, so a new endpoint
cannot accidentally ship without one, and the UI's decisions about what to show
are never load-bearing.

The single most important rule in this module: an assignment does not become
someone's responsibility because it was sent to them. It starts PENDING and
only counts once they accept.
"""
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AssignmentStatus,
    Community,
    CommunityInvitation,
    CommunityMember,
    CommunityRole,
    InviteStatus,
    TaskAssignment,
    TaskProgressUpdate,
    User,
    WorkNotification,
    WorkTask,
    WorkTaskStatus,
)

# Ranked so a check can ask "at least admin" rather than listing roles.
_ROLE_RANK = {
    CommunityRole.MEMBER.value: 1,
    CommunityRole.ADMIN.value: 2,
    CommunityRole.OWNER.value: 3,
}


# ---------------------------------------------------------------------------
# Membership and permissions
# ---------------------------------------------------------------------------
def membership(db: Session, community_id: str, user_id: str) -> CommunityMember | None:
    return (
        db.query(CommunityMember)
        .filter(CommunityMember.community_id == community_id, CommunityMember.user_id == user_id)
        .first()
    )


def require_member(db: Session, community_id: str, user: User, min_role: str = CommunityRole.MEMBER.value) -> CommunityMember:
    """Authorise a request against one community.

    Returns 404 rather than 403 for a non-member: telling someone a community
    exists but is closed to them is itself a disclosure, and community names
    are not public.
    """
    member = membership(db, community_id, user.id)
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Community not found")
    if _ROLE_RANK.get(member.role, 0) < _ROLE_RANK.get(min_role, 99):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This needs {min_role} permission in this community.",
        )
    return member


def user_communities(db: Session, user: User) -> list[Community]:
    return (
        db.query(Community)
        .join(CommunityMember, CommunityMember.community_id == Community.id)
        .filter(CommunityMember.user_id == user.id)
        .options(selectinload(Community.members))
        .order_by(Community.created_at.desc())
        .all()
    )


def create_community(db: Session, user: User, name: str, description: str | None, icon: str | None) -> Community:
    community = Community(
        name=name.strip(),
        description=(description or "").strip() or None,
        icon=(icon or "").strip() or "\U0001F465",
        created_by=user.id,
    )
    db.add(community)
    db.flush()
    # The creator is a member from the start, as owner. A community whose
    # creator has to invite themselves is a bug waiting to happen.
    db.add(CommunityMember(community_id=community.id, user_id=user.id, role=CommunityRole.OWNER.value))
    return community


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def notify(db: Session, user_id: str, kind: str, title: str, body: str | None = None,
           community_id: str | None = None, task_id: str | None = None,
           actor: str | None = None, assignment_id: str | None = None) -> None:
    """Queue a work notification.

    A shim over work_notify.send so every call site gets self-suppression,
    preference gating and de-duplication without having to remember them.
    """
    from app.services import work_notify

    work_notify.send(
        db, to=user_id, kind=kind, title=title, body=body, actor=actor,
        community_id=community_id, task_id=task_id, assignment_id=assignment_id,
    )


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------
def invite(db: Session, community: Community, inviter: User, invitee: User, message: str | None) -> CommunityInvitation:
    if invitee.id == inviter.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You're already in this community.")
    if membership(db, community.id, invitee.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{invitee.name} is already a member.")

    existing = (
        db.query(CommunityInvitation)
        .filter(
            CommunityInvitation.community_id == community.id,
            CommunityInvitation.invitee_id == invitee.id,
            CommunityInvitation.status == InviteStatus.PENDING.value,
        )
        .first()
    )
    if existing:
        # Re-inviting is a no-op rather than an error: the sender's intent is
        # already recorded, and a second row would mean two inboxes entries.
        return existing

    inv = CommunityInvitation(
        community_id=community.id, inviter_id=inviter.id, invitee_id=invitee.id,
        message=(message or "").strip() or None,
    )
    db.add(inv)
    notify(
        db, invitee.id, "community_invite",
        f"{inviter.name} invited you to {community.name}",
        message, community_id=community.id,
    )
    return inv


def respond_to_invite(db: Session, invitation: CommunityInvitation, user: User, accept: bool) -> CommunityInvitation:
    if invitation.invitee_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That invitation isn't yours.")
    if invitation.status != InviteStatus.PENDING.value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You've already responded to this invitation.")

    invitation.status = InviteStatus.ACCEPTED.value if accept else InviteStatus.DECLINED.value
    invitation.responded_at = datetime.now(timezone.utc)

    if accept:
        db.add(CommunityMember(community_id=invitation.community_id, user_id=user.id,
                               role=CommunityRole.MEMBER.value))
        notify(db, invitation.inviter_id, "invite_accepted",
               f"{user.name} joined {invitation.community.name}",
               community_id=invitation.community_id)
    else:
        notify(db, invitation.inviter_id, "invite_declined",
               f"{user.name} declined to join {invitation.community.name}",
               community_id=invitation.community_id)
    return invitation


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
def create_task(db: Session, community: Community, creator: User, *, title: str,
                assignee_ids: list[str], **fields) -> WorkTask:
    """Create a task and send it to each assignee for acceptance.

    Assignees must already be members: a task is a claim on someone's time
    inside a community, so it cannot be used to reach a stranger.
    """
    member_ids = {m.user_id for m in community.members}
    unknown = [uid for uid in assignee_ids if uid not in member_ids]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can only assign work to members of this community.")

    task = WorkTask(community_id=community.id, created_by=creator.id, title=title.strip(), **fields)
    db.add(task)
    db.flush()

    for uid in dict.fromkeys(assignee_ids):   # de-duplicated, order preserved
        db.add(TaskAssignment(task_id=task.id, user_id=uid))
        notify(db, uid, "task_assigned",
               f"{creator.name} assigned you “{task.title}”",
               f"{community.name}"
               + (f" · due {fields.get('due_date').strftime('%d %b')}" if fields.get("due_date") else "")
               + (" · group task" if len(assignee_ids) > 1 else ""),
               community_id=community.id, task_id=task.id, actor=creator.id)

    task.status = (
        WorkTaskStatus.PENDING_ACCEPTANCE.value if assignee_ids else WorkTaskStatus.DRAFT.value
    )
    return task


def respond_to_assignment(db: Session, assignment: TaskAssignment, user: User,
                          accept: bool, reason: str | None = None) -> TaskAssignment:
    if assignment.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That assignment isn't yours.")
    if assignment.status != AssignmentStatus.PENDING.value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You've already responded to this task.")

    assignment.status = AssignmentStatus.ACCEPTED.value if accept else AssignmentStatus.DECLINED.value
    assignment.responded_at = datetime.now(timezone.utc)
    if not accept:
        assignment.decline_reason = (reason or "").strip() or None

    db.add(TaskProgressUpdate(
        assignment_id=assignment.id, kind="status",
        note="Task accepted" if accept else f"Task declined{f': {assignment.decline_reason}' if assignment.decline_reason else ''}",
    ))

    task = assignment.task
    group = len(task.assignments) > 1
    notify(db, task.created_by,
           "task_accepted" if accept else "task_declined",
           f"{user.name} {'accepted' if accept else 'declined'}"
           f"{' the group task' if group else ''} “{task.title}”",
           assignment.decline_reason, community_id=task.community_id, task_id=task.id,
           actor=user.id, assignment_id=assignment.id)

    _refresh_task_status(db, task)
    _announce_if_finished(db, task, user)
    return assignment


def update_progress(db: Session, assignment: TaskAssignment, user: User,
                    progress: int | None, note: str | None) -> TaskAssignment:
    """Only the assignee moves their own progress."""
    if assignment.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only update your own progress.")
    if assignment.status in (AssignmentStatus.PENDING.value, AssignmentStatus.DECLINED.value):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Accept the task before recording progress on it.",
        )

    before = assignment.progress
    if progress is not None:
        assignment.progress = max(0, min(100, progress))
        # Status follows the number, so the two can never disagree.
        if assignment.progress >= 100:
            assignment.status = AssignmentStatus.COMPLETED.value
            assignment.completed_at = datetime.now(timezone.utc)
        elif assignment.progress > 0:
            assignment.status = AssignmentStatus.IN_PROGRESS.value
            assignment.completed_at = None
        else:
            assignment.status = AssignmentStatus.ACCEPTED.value
            assignment.completed_at = None

    db.add(TaskProgressUpdate(
        assignment_id=assignment.id,
        from_progress=before if progress is not None else None,
        to_progress=assignment.progress if progress is not None else None,
        note=(note or "").strip() or None,
        kind="progress" if progress is not None else "note",
    ))

    task = assignment.task
    if progress is not None and progress != before:
        # Starting and finishing are different events from "moved a slider":
        # they are the two a task's owner actually wants to hear about.
        if assignment.progress >= 100:
            kind, headline = "task_completed", f"{user.name} completed “{task.title}”"
        elif before == 0:
            kind, headline = "task_started", f"{user.name} started “{task.title}”"
        else:
            kind, headline = "task_progress", f"{user.name} moved “{task.title}” {before}% \u2192 {assignment.progress}%"

        notify(db, task.created_by, kind, headline,
               (note or "").strip() or None, community_id=task.community_id, task_id=task.id,
               actor=user.id, assignment_id=assignment.id)

    _refresh_task_status(db, task)
    _announce_if_finished(db, task, user)
    return assignment


def _refresh_task_status(db: Session, task: WorkTask) -> None:
    """Keep the task's own status consistent with its assignments."""
    live = [a for a in task.assignments if a.status != AssignmentStatus.DECLINED.value]
    if not live:
        # Everyone said no. Not "completed" -- nothing was done.
        task.status = WorkTaskStatus.CANCELLED.value
    elif all(a.status == AssignmentStatus.COMPLETED.value for a in live):
        task.status = WorkTaskStatus.COMPLETED.value
    elif any(a.status != AssignmentStatus.PENDING.value for a in live):
        task.status = WorkTaskStatus.ACTIVE.value
    else:
        task.status = WorkTaskStatus.PENDING_ACCEPTANCE.value


def _announce_if_finished(db: Session, task: WorkTask, actor: User) -> None:
    """Tell the creator once when every participant is done.

    Separate from the per-person completion notice: on a group task the
    difference between "Priya finished her part" and "the task is finished"
    is the difference between progress and a result.
    """
    if task.status != WorkTaskStatus.COMPLETED.value:
        return
    from app.services import work_notify

    work_notify.send(
        db, to=task.created_by, kind=work_notify.Kind.TASK_ALL_COMPLETED,
        title=f"“{task.title}” is complete",
        body="Everyone assigned has finished their part."
             if len(task.assignments) > 1 else None,
        actor=actor.id, community_id=task.community_id, task_id=task.id,
        # Once per task, however many times the last person edits their 100%.
        dedupe_key=f"all-done:{task.id}",
    )


def task_progress(task: WorkTask) -> dict:
    """Overall progress, counting only the people who took the work on.

    Declined assignments are excluded: someone who said no is not 0% done,
    they are not participating, and averaging them in would make a task look
    permanently stalled. Pending assignments are reported separately for the
    same reason -- they are not yet anyone's work.
    """
    accepted = [
        a for a in task.assignments
        if a.status in (
            AssignmentStatus.ACCEPTED.value,
            AssignmentStatus.IN_PROGRESS.value,
            AssignmentStatus.COMPLETED.value,
        )
    ]
    pending = [a for a in task.assignments if a.status == AssignmentStatus.PENDING.value]
    declined = [a for a in task.assignments if a.status == AssignmentStatus.DECLINED.value]

    overall = round(sum(a.progress for a in accepted) / len(accepted)) if accepted else 0
    return {
        "overall": overall,
        "accepted": len(accepted),
        "pending": len(pending),
        "declined": len(declined),
        "total": len(task.assignments),
        # Stated in the payload so the UI explains the rule rather than
        # leaving the professor to guess why the number looks high.
        "basis": "Averaged across members who accepted; pending and declined are excluded.",
    }
