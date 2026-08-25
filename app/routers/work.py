"""
Work-mode API: communities, invitations, tasks, assignments, notifications.

Every endpoint authorises through work_service, which returns 404 rather than
403 for a non-member so community names are not discoverable by probing.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    AssignmentStatus,
    Community,
    CommunityInvitation,
    CommunityMember,
    CommunityRole,
    InviteStatus,
    TaskAssignment,
    TaskComment,
    User,
    WorkNotification,
    WorkTask,
)
from app.schemas import UTCModel
from app.services.nlp_dates import ensure_aware_utc
from app.services import work_service as ws

router = APIRouter(prefix="/api/work", tags=["work"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------
class CommunityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    icon: str | None = Field(default=None, max_length=16)


class InviteCreate(BaseModel):
    # Either works, so the sender can use whichever they know.
    email: str | None = None
    user_id: str | None = None
    message: str | None = Field(default=None, max_length=500)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    assignee_ids: list[str] = Field(default_factory=list, max_length=50)
    priority: str = "medium"
    start_date: datetime | None = None
    due_date: datetime | None = None
    estimated_minutes: int | None = Field(default=None, ge=0, le=100_000)


class RespondBody(BaseModel):
    accept: bool
    reason: str | None = Field(default=None, max_length=500)


class ProgressBody(BaseModel):
    progress: int | None = Field(default=None, ge=0, le=100)
    note: str | None = Field(default=None, max_length=2000)


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class ProfileBody(BaseModel):
    profile: str = Field(pattern="^(personal|work)$")


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def _person(u: User) -> dict:
    """The minimum needed to show someone in a member list.

    Deliberately not the email: it is enough to invite by, so echoing it back
    to every member would turn any community into a mailing list.
    """
    return {"id": u.id, "name": u.name, "initial": (u.name or "?")[:1].upper()}


def _community(c: Community, me: str) -> dict:
    mine = next((m for m in c.members if m.user_id == me), None)
    return {
        "id": c.id, "name": c.name, "description": c.description, "icon": c.icon,
        "member_count": len(c.members),
        "my_role": mine.role if mine else None,
        "created_at": ensure_aware_utc(c.created_at).isoformat(),
    }


def _assignment(a: TaskAssignment, me: str | None = None) -> dict:
    return {
        "id": a.id, "user": _person(a.user), "status": a.status,
        # Whose row this is, decided here rather than by comparing display
        # names in the browser -- two members can share a name.
        "is_me": a.user_id == me if me else False,
        "progress": a.progress, "decline_reason": a.decline_reason,
        "responded_at": ensure_aware_utc(a.responded_at).isoformat() if a.responded_at else None,
    }


def _task(t: WorkTask, *, detail: bool = False, me: str | None = None) -> dict:
    data = {
        "id": t.id, "title": t.title, "description": t.description,
        "priority": t.priority, "status": t.status,
        "community": {"id": t.community_id, "name": t.community.name, "icon": t.community.icon},
        "creator": _person(t.creator),
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "start_date": t.start_date.isoformat() if t.start_date else None,
        "estimated_minutes": t.estimated_minutes,
        "progress": ws.task_progress(t),
        "assignments": [_assignment(a, me) for a in t.assignments],
    }
    if detail:
        data["comments"] = [
            {"id": c.id, "user": _person(c.user), "body": c.body, "at": ensure_aware_utc(c.created_at).isoformat()}
            for c in sorted(t.comments, key=lambda c: c.created_at)
        ]
        data["timeline"] = sorted(
            (
                {
                    "at": u.created_at.isoformat(), "kind": u.kind, "note": u.note,
                    "from": u.from_progress, "to": u.to_progress,
                    "user": _person(a.user),
                }
                for a in t.assignments for u in a.updates
            ),
            key=lambda x: x["at"], reverse=True,
        )
    return data


def _load_task(db: Session, task_id: str) -> WorkTask:
    task = (
        db.query(WorkTask)
        .options(
            selectinload(WorkTask.assignments).selectinload(TaskAssignment.user),
            selectinload(WorkTask.assignments).selectinload(TaskAssignment.updates),
            selectinload(WorkTask.comments).selectinload(TaskComment.user),
            selectinload(WorkTask.community),
            selectinload(WorkTask.creator),
        )
        .filter(WorkTask.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return task


# ---------------------------------------------------------------------------
# Profile mode
# ---------------------------------------------------------------------------
@router.put("/profile")
def set_profile(payload: ProfileBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Remember which mode to open in. One account, two views."""
    user.active_profile = payload.profile
    db.commit()
    return {"ok": True, "profile": user.active_profile}


# ---------------------------------------------------------------------------
# Communities
# ---------------------------------------------------------------------------
@router.get("/communities")
def list_communities(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"communities": [_community(c, user.id) for c in ws.user_communities(db, user)]}


@router.post("/communities", status_code=status.HTTP_201_CREATED)
def create_community(payload: CommunityCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    community = ws.create_community(db, user, payload.name, payload.description, payload.icon)
    db.commit()
    db.refresh(community)
    return _community(community, user.id)


@router.get("/communities/{community_id}")
def get_community(community_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ws.require_member(db, community_id, user)
    community = (
        db.query(Community)
        .options(selectinload(Community.members).selectinload(CommunityMember.user))
        .filter(Community.id == community_id)
        .first()
    )
    pending = (
        db.query(CommunityInvitation)
        .options(selectinload(CommunityInvitation.invitee))
        .filter(
            CommunityInvitation.community_id == community_id,
            CommunityInvitation.status == InviteStatus.PENDING.value,
        )
        .all()
    )
    return {
        **_community(community, user.id),
        "members": [
            {**_person(m.user), "role": m.role, "joined_at": ensure_aware_utc(m.joined_at).isoformat()}
            for m in sorted(community.members, key=lambda m: m.joined_at)
        ],
        "pending_invites": [{**_person(i.invitee), "invited_at": ensure_aware_utc(i.created_at).isoformat()} for i in pending],
    }


@router.post("/communities/{community_id}/invite", status_code=status.HTTP_201_CREATED)
def invite_member(community_id: str, payload: InviteCreate,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Invite someone. Admins and owners only."""
    ws.require_member(db, community_id, user, CommunityRole.ADMIN.value)
    community = db.query(Community).options(selectinload(Community.members)).filter(
        Community.id == community_id).first()

    if not payload.email and not payload.user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give an email address or a user id.")

    q = db.query(User)
    invitee = (
        q.filter(User.id == payload.user_id).first() if payload.user_id
        else q.filter(User.email == payload.email.strip().lower()).first()
    )
    if not invitee:
        # Same message either way: a different one would turn this into a way
        # to test whether an address has an account here.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No account found for that person.")

    inv = ws.invite(db, community, user, invitee, payload.message)
    db.commit()
    return {"ok": True, "invitation_id": inv.id, "invited": _person(invitee)}


@router.delete("/communities/{community_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(community_id: str, member_user_id: str,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Remove someone, or leave yourself."""
    me = ws.require_member(db, community_id, user)
    leaving_self = member_user_id == user.id

    if not leaving_self:
        ws.require_member(db, community_id, user, CommunityRole.ADMIN.value)

    target = ws.membership(db, community_id, member_user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person isn't in this community.")
    if target.role == CommunityRole.OWNER.value:
        # Otherwise a community can be left with no one able to administer it.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The owner can't be removed. Transfer ownership first.")
    if me.role == CommunityRole.ADMIN.value and target.role == CommunityRole.ADMIN.value and not leaving_self:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can remove another admin.")

    db.delete(target)
    if not leaving_self:
        ws.notify(db, member_user_id, "removed_from_community",
                  f"You were removed from {target.community.name}", community_id=community_id)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Invitations (the invitee's side)
# ---------------------------------------------------------------------------
@router.get("/invitations")
def my_invitations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(CommunityInvitation)
        .options(selectinload(CommunityInvitation.community), selectinload(CommunityInvitation.inviter))
        .filter(CommunityInvitation.invitee_id == user.id,
                CommunityInvitation.status == InviteStatus.PENDING.value)
        .order_by(CommunityInvitation.created_at.desc())
        .all()
    )
    return {
        "invitations": [
            {
                "id": i.id, "message": i.message,
                "community": {"id": i.community.id, "name": i.community.name, "icon": i.community.icon},
                "from": _person(i.inviter), "at": ensure_aware_utc(i.created_at).isoformat(),
            }
            for i in rows
        ]
    }


@router.post("/invitations/{invitation_id}/respond")
def respond_invitation(invitation_id: str, payload: RespondBody,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    inv = (
        db.query(CommunityInvitation)
        .options(selectinload(CommunityInvitation.community))
        .filter(CommunityInvitation.id == invitation_id)
        .first()
    )
    if not inv or inv.invitee_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    ws.respond_to_invite(db, inv, user, payload.accept)
    db.commit()
    return {"ok": True, "status": inv.status}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
@router.post("/communities/{community_id}/tasks", status_code=status.HTTP_201_CREATED)
def create_task(community_id: str, payload: TaskCreate,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ws.require_member(db, community_id, user)
    community = db.query(Community).options(selectinload(Community.members)).filter(
        Community.id == community_id).first()

    task = ws.create_task(
        db, community, user,
        title=payload.title,
        assignee_ids=payload.assignee_ids,
        description=payload.description,
        priority=payload.priority,
        start_date=payload.start_date,
        due_date=payload.due_date,
        estimated_minutes=payload.estimated_minutes,
    )
    db.commit()
    return _task(_load_task(db, task.id), me=user.id)


@router.get("/tasks")
def list_tasks(
    scope: str = Query(default="assigned", pattern="^(assigned|created|community)$"),
    community_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Tasks assigned to me, created by me, or belonging to one community."""
    base = db.query(WorkTask).options(
        selectinload(WorkTask.assignments).selectinload(TaskAssignment.user),
        selectinload(WorkTask.community),
        selectinload(WorkTask.creator),
    )

    if scope == "assigned":
        base = base.join(TaskAssignment).filter(TaskAssignment.user_id == user.id)
    elif scope == "created":
        base = base.filter(WorkTask.created_by == user.id)
    else:
        if not community_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "community_id is required for this scope.")
        ws.require_member(db, community_id, user)
        base = base.filter(WorkTask.community_id == community_id)

    tasks = base.order_by(WorkTask.created_at.desc()).limit(200).all()

    # Never leak a task from a community the caller left.
    my_communities = {m.community_id for m in db.query(CommunityMember).filter(
        CommunityMember.user_id == user.id).all()}
    tasks = [t for t in tasks if t.community_id in my_communities]

    return {"tasks": [_task(t, me=user.id) for t in tasks]}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = _load_task(db, task_id)
    ws.require_member(db, task.community_id, user)
    return _task(task, detail=True, me=user.id)


@router.post("/tasks/{task_id}/respond")
def respond_task(task_id: str, payload: RespondBody,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Accept or decline a task assigned to you. Until this, it is not yours."""
    task = _load_task(db, task_id)
    assignment = next((a for a in task.assignments if a.user_id == user.id), None)
    if not assignment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You don't have an assignment on this task.")
    ws.respond_to_assignment(db, assignment, user, payload.accept, payload.reason)
    db.commit()
    return _task(_load_task(db, task_id), me=user.id)


@router.put("/tasks/{task_id}/progress")
def set_progress(task_id: str, payload: ProgressBody,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = _load_task(db, task_id)
    assignment = next((a for a in task.assignments if a.user_id == user.id), None)
    if not assignment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You don't have an assignment on this task.")
    ws.update_progress(db, assignment, user, payload.progress, payload.note)
    db.commit()
    return _task(_load_task(db, task_id), me=user.id)


@router.post("/tasks/{task_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(task_id: str, payload: CommentBody,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = _load_task(db, task_id)
    ws.require_member(db, task.community_id, user)

    db.add(TaskComment(task_id=task.id, user_id=user.id, body=payload.body.strip()))
    for uid in {a.user_id for a in task.assignments} | {task.created_by}:
        if uid != user.id:
            ws.notify(db, uid, "task_comment", f"{user.name} commented on “{task.title}”",
                      payload.body.strip()[:160], community_id=task.community_id, task_id=task.id)
    db.commit()
    return _task(_load_task(db, task_id), detail=True, me=user.id)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Only the creator, or a community admin, can remove a task."""
    task = _load_task(db, task_id)
    member = ws.require_member(db, task.community_id, user)
    if task.created_by != user.id and member.role == CommunityRole.MEMBER.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the task's creator or an admin can delete it.")
    db.delete(task)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Dashboard and notifications
# ---------------------------------------------------------------------------
@router.get("/dashboard")
def work_dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """One request for the whole Work home, rather than five round trips."""
    mine = (
        db.query(TaskAssignment)
        .options(
            selectinload(TaskAssignment.task).selectinload(WorkTask.community),
            selectinload(TaskAssignment.task).selectinload(WorkTask.creator),
            selectinload(TaskAssignment.task).selectinload(WorkTask.assignments).selectinload(TaskAssignment.user),
        )
        .filter(TaskAssignment.user_id == user.id)
        .all()
    )

    pending = [a for a in mine if a.status == AssignmentStatus.PENDING.value]
    active = [a for a in mine if a.status in (AssignmentStatus.ACCEPTED.value, AssignmentStatus.IN_PROGRESS.value)]
    done = [a for a in mine if a.status == AssignmentStatus.COMPLETED.value]

    soon = datetime.now(timezone.utc) + timedelta(days=2)
    due_soon = [
        a for a in active
        if a.task.due_date and a.task.due_date.replace(tzinfo=timezone.utc) <= soon
    ]

    return {
        "counts": {"active": len(active), "pending": len(pending), "completed": len(done)},
        "requests": [
            {**_task(a.task, me=user.id), "assignment_id": a.id}
            for a in sorted(pending, key=lambda a: a.assigned_at, reverse=True)
        ],
        "active_tasks": [
            {**_task(a.task, me=user.id), "my_progress": a.progress, "my_status": a.status}
            for a in sorted(active, key=lambda a: (a.task.due_date is None, a.task.due_date))
        ],
        "due_soon": [_task(a.task, me=user.id) for a in due_soon],
        "communities": [_community(c, user.id) for c in ws.user_communities(db, user)],
        "unread_notifications": db.query(WorkNotification).filter(
            WorkNotification.user_id == user.id, WorkNotification.read_at.is_(None)).count(),
    }


@router.get("/notifications")
def work_notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(WorkNotification)
        .filter(WorkNotification.user_id == user.id)
        .order_by(WorkNotification.created_at.desc())
        .limit(40)
        .all()
    )
    return {
        "unread": sum(1 for r in rows if r.read_at is None),
        "items": [
            {
                "id": r.id, "kind": r.kind, "title": r.title, "body": r.body,
                "at": ensure_aware_utc(r.created_at).isoformat(), "read": r.read_at is not None,
                "community_id": r.community_id, "task_id": r.task_id,
                "assignment_id": r.assignment_id,
            }
            for r in rows
        ],
    }


@router.post("/notifications/read")
def mark_work_notifications_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    updated = (
        db.query(WorkNotification)
        .filter(WorkNotification.user_id == user.id, WorkNotification.read_at.is_(None))
        .update({WorkNotification.read_at: now}, synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "marked_read": updated}


class WorkPrefs(BaseModel):
    notify_work_responses: bool | None = None
    notify_work_progress: bool | None = None
    notify_work_completion: bool | None = None
    notify_work_deadlines: bool | None = None
    notify_work_community: bool | None = None


@router.get("/preferences")
def get_prefs(user: User = Depends(get_current_user)):
    return {
        "notify_work_responses": user.notify_work_responses,
        "notify_work_progress": user.notify_work_progress,
        "notify_work_completion": user.notify_work_completion,
        "notify_work_deadlines": user.notify_work_deadlines,
        "notify_work_community": user.notify_work_community,
        # Stated so the settings screen can explain the omission rather than
        # leaving someone hunting for a switch that does not exist.
        "always_on": ["task_assigned"],
    }


@router.put("/preferences")
def set_prefs(payload: WorkPrefs, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(user, field, value)
    db.commit()
    return get_prefs(user)


@router.delete("/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_notification(notification_id: str, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    n = (
        db.query(WorkNotification)
        .filter(WorkNotification.id == notification_id, WorkNotification.user_id == user.id)
        .first()
    )
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    db.delete(n)
    db.commit()
    return None


@router.post("/notifications/clear", status_code=status.HTTP_204_NO_CONTENT)
def clear_notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(WorkNotification).filter(WorkNotification.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    return None


@router.get("/people/search")
def search_people(
    q: str = Query(min_length=3, max_length=120),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Find someone to invite.

    Requires a near-complete address rather than a substring: a loose search
    over every account would let anyone enumerate the user base. Matching is
    on the full email, or on a name only among people already sharing a
    community with the caller.
    """
    needle = q.strip().lower()

    by_email = db.query(User).filter(User.email == needle, User.id != user.id).all()

    my_communities = [m.community_id for m in db.query(CommunityMember).filter(
        CommunityMember.user_id == user.id).all()]
    by_name = []
    if my_communities:
        by_name = (
            db.query(User)
            .join(CommunityMember, CommunityMember.user_id == User.id)
            .filter(
                CommunityMember.community_id.in_(my_communities),
                User.id != user.id,
                or_(User.name.ilike(f"%{needle}%"), User.email == needle),
            )
            .limit(10)
            .all()
        )

    seen, people = set(), []
    for u in by_email + by_name:
        if u.id in seen:
            continue
        seen.add(u.id)
        people.append(_person(u))
    return {"people": people}
