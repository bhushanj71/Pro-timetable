"""
Work-mode API: communities, invitations, tasks, assignments, notifications.

Every endpoint authorises through work_service, which returns 404 rather than
403 for a non-member so community names are not discoverable by probing.
"""
import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status,
)
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import (
    AssignmentStatus,
    Community,
    CommunityInvitation,
    CommunityMember,
    CommunityRole,
    InviteStatus,
    TaskActivity,
    TaskAssignment,
    TaskAttachment,
    TaskComment,
    User,
    WorkNotification,
    WorkTask,
)
from app.schemas import UTCModel
from app.services.nlp_dates import ensure_aware_utc
from app.services import (
    attachments as att,
    org_service as og,
    work_notify,
    work_service as ws,
    work_tracking as wt,
)

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
# How long a deletion confirmation stays valid. Long enough to read the
# impact and type a name, short enough that a dialog left open in a tab
# cannot be used against a community that has changed since.
DELETION_TICKET_TTL = 300


def issue_deletion_ticket(community_id: str, user_id: str) -> str:
    """A signed, expiring note that this owner has seen the impact preview."""
    issued = str(int(time.time()))
    return f"{issued}.{_sign_deletion(community_id, user_id, issued)}"


def verify_deletion_ticket(ticket: str, community_id: str, user_id: str) -> bool:
    issued, _, sig = (ticket or "").partition(".")
    if not issued.isdigit() or not sig:
        return False
    if time.time() - int(issued) > DELETION_TICKET_TTL:
        return False
    # Constant-time: this is a signature check, and the community is gone if
    # it passes.
    return hmac.compare_digest(sig, _sign_deletion(community_id, user_id, issued))


def _sign_deletion(community_id: str, user_id: str, issued: str) -> str:
    """Bound to the community AND the owner, so a ticket for one community
    cannot be spent on another, or by a different account."""
    msg = f"delete-community:{community_id}:{user_id}:{issued}".encode()
    key = get_settings().SECRET_KEY.encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def _person(u: User) -> dict:
    """Name and department together, everywhere someone is shown.

    Still deliberately not the email: it is enough to invite by, so echoing it
    back to every member would turn any community into a mailing list. The
    department is the opposite case -- it is what tells two colleagues with
    the same name apart, which is exactly what an assignor needs to see.
    """
    return og.person_dict(u)


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


def _task(t: WorkTask, *, detail: bool = False, me: str | None = None,
          db_session: Session | None = None, viewer: User | None = None,
          member=None) -> dict:
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
        # One timeline out of three sources. Progress lives on the
        # assignment, comments on the task, and everything else -- uploads,
        # reassignment, completion -- in the activity log. Merging them at
        # read time is what makes the history read as one story rather than
        # three tabs the reader has to interleave themselves.
        events = [
            {
                "at": ensure_aware_utc(u.created_at).isoformat(), "kind": u.kind,
                "note": u.note, "from": u.from_progress, "to": u.to_progress,
                "user": _person(a.user),
            }
            for a in t.assignments for u in a.updates
        ]
        events += [
            {
                "at": ensure_aware_utc(c.created_at).isoformat(), "kind": "comment",
                "note": c.body, "from": None, "to": None, "user": _person(c.user),
            }
            for c in t.comments
        ]
        events += [
            {
                "at": ensure_aware_utc(act.created_at).isoformat(), "kind": act.action,
                "note": act.comment, "from": act.old_value, "to": act.new_value,
                "user": _person(act.user),
            }
            for act in t.activities
        ]
        data["timeline"] = sorted(events, key=lambda x: x["at"], reverse=True)
        data["attachments"] = (
            _attachments_of(db_session, t, viewer=viewer, member=member) if db_session else []
        )
        data["can_attach"] = (
            att.may_attach(t, viewer, member) if viewer is not None else False
        )
    return data


def _load_community(db: Session, community_id: str) -> Community:
    community = (
        db.query(Community)
        .options(selectinload(Community.members).selectinload(CommunityMember.user))
        .filter(Community.id == community_id)
        .first()
    )
    if not community:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Community not found")
    return community


def _load_task(db: Session, task_id: str) -> WorkTask:
    task = (
        db.query(WorkTask)
        .options(
            selectinload(WorkTask.assignments).selectinload(TaskAssignment.user),
            selectinload(WorkTask.assignments).selectinload(TaskAssignment.updates),
            selectinload(WorkTask.comments).selectinload(TaskComment.user),
            selectinload(WorkTask.activities).selectinload(TaskActivity.user),
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
    og.require_work_profile(user)
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
def invite_member(community_id: str, payload: InviteCreate, background: BackgroundTasks,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Invite someone. Admins and owners only."""
    og.require_work_profile(user)
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
    background.add_task(work_notify.deliver_in_background)
    return {"ok": True, "invitation_id": inv.id, "invited": _person(invitee)}


@router.get("/communities/{community_id}/directory")
def community_directory(
    community_id: str,
    q: str | None = Query(default=None, max_length=120),
    department_id: str | None = Query(default=None, max_length=36),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Colleagues at the caller's own college, to fill a community from.

    Deliberately narrower than a general people search. The existing
    /people/search needs a near-exact email precisely so that nobody can
    enumerate the user base; browsing by name is only opened up here because
    the result set is bounded by a college the caller already belongs to, and
    only for someone who can invite into this community anyway.
    """
    ws.require_member(db, community_id, user, CommunityRole.ADMIN.value)
    community = (
        db.query(Community)
        .options(selectinload(Community.members), selectinload(Community.invitations))
        .filter(Community.id == community_id)
        .first()
    )
    return ws.college_directory(db, user, community, q, department_id)


# ---------------------------------------------------------------------------
# Deleting a community: two steps, because one is not enough for this
# ---------------------------------------------------------------------------
@router.get("/communities/{community_id}/deletion-preview")
def deletion_preview(community_id: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Step one: say exactly what will be destroyed, and issue a ticket.

    The ticket is what makes this two steps rather than one form with two
    fields. A delete is refused without one, so the client cannot skip
    showing this and go straight to the destructive call, and it expires so a
    confirmation cannot be replayed against a community that has since grown.
    """
    ws.require_member(db, community_id, user, CommunityRole.OWNER.value)
    community = (
        db.query(Community)
        .options(selectinload(Community.members), selectinload(Community.tasks))
        .filter(Community.id == community_id)
        .first()
    )
    return {
        "name": community.name,
        "icon": community.icon,
        "impact": ws.deletion_impact(db, community),
        "ticket": issue_deletion_ticket(community.id, user.id),
        "expires_in": DELETION_TICKET_TTL,
        # Echoed so the client can compare what the owner types against the
        # server's idea of the name, not its own possibly stale copy.
        "confirm_phrase": community.name,
    }


class CommunityDelete(BaseModel):
    confirm: str = Field(max_length=120)
    ticket: str = Field(max_length=200)


@router.delete("/communities/{community_id}", status_code=status.HTTP_200_OK)
def delete_community(community_id: str, payload: CommunityDelete, background: BackgroundTasks,
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Step two: the typed name and the ticket from step one.

    Owner only -- an admin can invite and assign, but destroying everyone
    else's work is not an administrative act.
    """
    ws.require_member(db, community_id, user, CommunityRole.OWNER.value)
    community = (
        db.query(Community)
        .options(
            selectinload(Community.members),
            selectinload(Community.tasks),
            selectinload(Community.invitations),
        )
        .filter(Community.id == community_id)
        .first()
    )

    if not verify_deletion_ticket(payload.ticket, community_id, user.id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That confirmation has expired. Open the delete dialog again to see what will be removed.",
        )
    # Case and surrounding space are forgiven; the letters are not. Typing the
    # name is the point -- it is what stops a reflexive click on a dialog.
    if payload.confirm.strip().casefold() != community.name.strip().casefold():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Type the community name exactly to confirm: {community.name}",
        )

    impact = ws.delete_community(db, community, user)
    db.commit()
    background.add_task(work_notify.deliver_in_background)
    return {"ok": True, "deleted": impact}


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
def respond_invitation(invitation_id: str, payload: RespondBody, background: BackgroundTasks,
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
    background.add_task(work_notify.deliver_in_background)
    return {"ok": True, "status": inv.status}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
@router.post("/communities/{community_id}/tasks", status_code=status.HTTP_201_CREATED)
def create_task(community_id: str, payload: TaskCreate, background: BackgroundTasks,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    og.require_work_profile(user)
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
    background.add_task(work_notify.deliver_in_background)
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
    member = ws.require_member(db, task.community_id, user)
    data = _task(task, detail=True, me=user.id, db_session=db, viewer=user, member=member)
    # Decided here rather than in the serializer, which has no session and so
    # could only compare creator ids -- missing the community admins who may
    # also manage the task. The UI reads this instead of guessing.
    data["can_manage"] = (
        task.created_by == user.id or member.role != CommunityRole.MEMBER.value
    )
    return data


@router.post("/tasks/{task_id}/respond")
def respond_task(task_id: str, payload: RespondBody, background: BackgroundTasks,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Accept or decline a task assigned to you. Until this, it is not yours."""
    task = _load_task(db, task_id)
    assignment = next((a for a in task.assignments if a.user_id == user.id), None)
    if not assignment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You don't have an assignment on this task.")
    ws.respond_to_assignment(db, assignment, user, payload.accept, payload.reason)
    db.commit()
    background.add_task(work_notify.deliver_in_background)
    return _task(_load_task(db, task_id), me=user.id)


@router.put("/tasks/{task_id}/progress")
def set_progress(task_id: str, payload: ProgressBody, background: BackgroundTasks,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = _load_task(db, task_id)
    assignment = next((a for a in task.assignments if a.user_id == user.id), None)
    if not assignment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You don't have an assignment on this task.")
    ws.update_progress(db, assignment, user, payload.progress, payload.note)
    db.commit()
    background.add_task(work_notify.deliver_in_background)
    return _task(_load_task(db, task_id), me=user.id)


@router.post("/tasks/{task_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(task_id: str, payload: CommentBody, background: BackgroundTasks,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = _load_task(db, task_id)
    ws.require_member(db, task.community_id, user)

    db.add(TaskComment(task_id=task.id, user_id=user.id, body=payload.body.strip()))
    for uid in {a.user_id for a in task.assignments} | {task.created_by}:
        if uid != user.id:
            ws.notify(db, uid, "task_comment", f"{user.name} commented on “{task.title}”",
                      payload.body.strip()[:160], community_id=task.community_id, task_id=task.id)
    db.commit()
    background.add_task(work_notify.deliver_in_background)
    return _task(_load_task(db, task_id), detail=True, me=user.id)


class AssigneesIn(BaseModel):
    user_ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/tasks/{task_id}/assignees", status_code=status.HTTP_201_CREATED)
def add_task_assignees(task_id: str, payload: AssigneesIn, background: BackgroundTasks,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Put more people on an existing task. Each one starts pending."""
    task = _load_task(db, task_id)
    added = ws.add_assignees(db, task, user, payload.user_ids)
    db.commit()
    db.refresh(task)
    background.add_task(work_notify.deliver_in_background)
    return {"added": len(added), "task": _task(task, detail=True, me=user.id)}


@router.get("/tasks/{task_id}/assignees/{member_id}/removal-cost")
def assignee_removal_cost(task_id: str, member_id: str,
                          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """What is lost by taking this person off, asked before doing it.

    An assignment is not just a name: it carries a status, a percentage and a
    history of updates, none of which survives. Saying so first is the
    difference between a decision and a surprise.
    """
    task = _load_task(db, task_id)
    ws.require_task_manager(db, task, user)
    assignment = next((a for a in task.assignments if a.user_id == member_id), None)
    if not assignment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person isn't on this task.")
    return ws.removal_cost(assignment)


@router.delete("/tasks/{task_id}/assignees/{member_id}")
def remove_task_assignee(task_id: str, member_id: str, background: BackgroundTasks,
                         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = _load_task(db, task_id)
    cost = ws.remove_assignee(db, task, user, member_id)
    db.commit()
    db.refresh(task)
    background.add_task(work_notify.deliver_in_background)
    return {"removed": cost, "task": _task(task, detail=True, me=user.id)}


class ReassignIn(BaseModel):
    from_user_id: str = Field(min_length=1, max_length=36)
    to_user_id: str = Field(min_length=1, max_length=36)


@router.post("/tasks/{task_id}/reassign")
def reassign_task(task_id: str, payload: ReassignIn, background: BackgroundTasks,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Hand one person's place on a task to somebody else, in one step."""
    task = _load_task(db, task_id)
    result = ws.reassign(db, task, user, payload.from_user_id, payload.to_user_id)
    db.commit()
    db.refresh(task)
    background.add_task(work_notify.deliver_in_background)
    return {"reassigned": result, "task": _task(task, detail=True, me=user.id)}


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    priority: str | None = None
    due_date: datetime | None = None
    estimated_minutes: int | None = Field(default=None, ge=0, le=100_000)


@router.put("/tasks/{task_id}")
def update_task(task_id: str, payload: TaskUpdate, background: BackgroundTasks,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Edit a task, and tell the people carrying it what moved."""
    task = _load_task(db, task_id)

    fields = payload.model_dump(exclude_unset=True)
    if "priority" in fields and fields["priority"] not in ("low", "medium", "high", "urgent"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown priority.")

    changed = ws.update_task(db, task, user, fields)
    db.commit()
    db.refresh(task)
    background.add_task(work_notify.deliver_in_background)
    return {"changed": changed, "task": _task(task, detail=True, me=user.id)}


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
        "trends": _trends(mine),
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


def _trends(assignments: list[TaskAssignment], days: int = 7) -> dict:
    """Seven daily readings behind each headline count.

    A sparkline that is drawn rather than measured is a lie the size of a
    card, so each series is reconstructed from the timestamps already on the
    assignment: when it arrived, when it was answered, when it finished. Each
    series is a running total, so its last point is exactly the number printed
    beside it.
    """
    today = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=0)
    edges = [today - timedelta(days=d) for d in range(days - 1, -1, -1)]
    series = {"active": [], "pending": [], "completed": []}

    for edge in edges:
        active = pending = completed = 0
        for a in assignments:
            arrived = ensure_aware_utc(a.assigned_at)
            if arrived > edge:
                continue                     # did not exist yet on this day
            answered = ensure_aware_utc(a.responded_at) if a.responded_at else None
            finished = ensure_aware_utc(a.completed_at) if a.completed_at else None

            if finished and finished <= edge:
                completed += 1
            elif answered and answered <= edge:
                # Answered, but a decline never became work anyone was doing.
                if a.status != AssignmentStatus.DECLINED.value:
                    active += 1
            else:
                pending += 1
        series["active"].append(active)
        series["pending"].append(pending)
        series["completed"].append(completed)

    return series


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


# ===========================================================================
# Attachments
#
# The bytes live in the database. There is no object storage in this project
# and the host's disk is ephemeral, so a file written to it disappears on the
# next deploy -- silently, which for evidence of work is the worst way to lose
# something. See TaskAttachment for the trade and the way out of it.
# ===========================================================================
def _attachments_of(db: Session, task: WorkTask, *, viewer: User | None = None,
                    member=None) -> list[dict]:
    """The task's files, each carrying whether this viewer may remove it.

    Decided here rather than in the browser, for the same reason can_manage is:
    the page cannot see a membership role, and a rule it has to guess at is a
    rule that will eventually be guessed wrong.
    """
    rows = (
        db.query(TaskAttachment)
        .options(selectinload(TaskAttachment.uploader))
        .filter(TaskAttachment.task_id == task.id)
        .order_by(TaskAttachment.uploaded_at.desc())
        .all()
    )
    out = []
    for a in rows:
        data = att.attachment_dict(a, person=_person)
        data["can_remove"] = (
            att.may_delete(a, task, viewer, member) if viewer is not None else False
        )
        out.append(data)
    return out


@router.get("/tasks/{task_id}/attachments")
def list_attachments(task_id: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    task = _load_task(db, task_id)
    member = ws.require_member(db, task.community_id, user)
    return {"attachments": _attachments_of(db, task, viewer=user, member=member)}


@router.post("/tasks/{task_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(task_id: str, file: UploadFile = File(...),
                            db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    """Attach a file to a task.

    Read first, then measure. Content-Length on a multipart part is a claim,
    not a fact, so the cap is enforced on what actually arrived. One byte over
    is read deliberately: it is how a file exactly at the limit is told apart
    from one above it.
    """
    task = _load_task(db, task_id)
    member = ws.require_member(db, task.community_id, user)
    if not att.may_attach(task, user, member):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the people assigned to this task, the person who assigned it, "
            "or a community admin can attach files to it.",
        )

    body = await file.read(att.MAX_BYTES + 1)
    name, content_type = att.validate(file.filename or "", file.content_type or "", len(body))

    row = TaskAttachment(
        task_id=task.id, uploaded_by=user.id, file_name=name,
        content_type=content_type, size_bytes=len(body), data=body,
    )
    db.add(row)

    # The upload is an event in the task's story, not a silent side effect --
    # the timeline is how an owner sees that evidence arrived, and when.
    ws.log_activity(db, task, user, kind="attachment", note="uploaded " + name)
    db.commit()
    db.refresh(row)
    return att.attachment_dict(row, person=_person)


@router.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: str, download: bool = False,
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    from fastapi.responses import Response

    row = db.get(TaskAttachment, attachment_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That file no longer exists.")
    task = _load_task(db, row.task_id)
    ws.require_member(db, task.community_id, user)

    # inline only for types a browser renders without running anything. An
    # open-in-tab for arbitrary content is a way to get script onto this
    # origin, so everything else is sent as a download whatever was asked for.
    inline = (not download) and row.content_type in att.INLINE_SAFE
    disposition = "inline" if inline else "attachment"
    return Response(
        content=row.data,
        media_type=row.content_type,
        headers={
            "Content-Disposition": disposition + '; filename="' + row.file_name + '"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(attachment_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    row = db.get(TaskAttachment, attachment_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That file no longer exists.")
    task = _load_task(db, row.task_id)
    member = ws.require_member(db, task.community_id, user)
    if not att.may_delete(row, task, user, member):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only remove files you uploaded yourself.",
        )
    name = row.file_name
    db.delete(row)
    ws.log_activity(db, task, user, kind="attachment", note="removed " + name)
    db.commit()
    return None


# ===========================================================================
# Tracking: the owner's dashboard, and one member's history
# ===========================================================================
def _tracked(assignment: TaskAssignment, task: WorkTask, counts: dict) -> dict:
    """One row of a member's history: the task, and their share of it."""
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "priority": task.priority,
        "bucket": wt.bucket_of(assignment, task),
        "status": assignment.status,
        "progress": assignment.progress,
        "assigned_by": _person(task.creator),
        "assigned_at": ensure_aware_utc(assignment.assigned_at).isoformat(),
        "due_date": ensure_aware_utc(task.due_date).isoformat() if task.due_date else None,
        "completed_at": (
            ensure_aware_utc(assignment.completed_at).isoformat()
            if assignment.completed_at else None
        ),
        "updated_at": ensure_aware_utc(task.updated_at).isoformat(),
        "decline_reason": assignment.decline_reason,
        "attachment_count": counts.get(task.id, 0),
        "member": _person(assignment.user),
        "member_id": assignment.user_id,
    }


@router.get("/communities/{community_id}/overview")
def community_overview(community_id: str, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """The community's workload, summarised, with a row per member.

    Open to every member rather than the owner alone. A team where only one
    person can see who is carrying what is a team where nobody else can offer
    to help -- and these numbers say nothing a member could not already work
    out by opening the tasks one at a time.
    """
    community = _load_community(db, community_id)
    ws.require_member(db, community.id, user)
    data = wt.community_overview(db, community.id)
    return {
        "community": {"id": community.id, "name": community.name, "icon": community.icon},
        "totals": {
            "members": data["members_total"],
            "tasks": data["tasks_total"],
            "active": data["active"],
            "pending": data["pending"],
            "in_progress": data["in_progress"],
            "completed": data["completed"],
            "incomplete": data["incomplete"],
            "overdue": data["overdue"],
        },
        "performance": [
            {
                "user": _person(p["user"]),
                "user_id": p["user"].id,
                "role": p["role"],
                "assigned": p["assigned"],
                "completed": p["completed"],
                "in_progress": p["in_progress"],
                "pending": p["pending"],
                "incomplete": p["incomplete"],
                "overdue": p["overdue"],
                "completion": p["completion"],
            }
            for p in data["performance"]
        ],
    }


@router.get("/communities/{community_id}/members/{member_id}/tasks")
def member_task_history(
    community_id: str,
    member_id: str,
    period: str | None = Query(None, pattern="^(today|week|month|custom)$"),
    start: str | None = None,
    end: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Everything one member has been given in this community.

    Anyone may read their own history. Reading someone else's needs a managing
    role: "what has Rahul been working on" is a supervisor's question, and the
    answer is not a colleague's to take.
    """
    community = _load_community(db, community_id)
    member = ws.require_member(db, community.id, user)
    if member_id != user.id and member.role == CommunityRole.MEMBER.value:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the community's owner or an admin can look at another member's work.",
        )

    subject = db.get(User, member_id)
    if not subject or not ws.membership(db, community.id, member_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person is not in this community.")

    window = wt.resolve_period(period, user.timezone, start, end)
    wanted = {s for s in (status_filter or "").split(",") if s in wt.BUCKETS}
    result = wt.member_overview(db, community.id, member_id, period=window, statuses=wanted or None)

    return {
        "member": _person(subject),
        "member_id": subject.id,
        "period": window.label,
        "tally": result["tally"],
        "tasks": [_tracked(a, t, result["attachment_counts"]) for a, t in result["pairs"]],
    }


@router.get("/communities/{community_id}/search")
def search_work(community_id: str, q: str = Query(min_length=1, max_length=120),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """By member name, task title, or the start of a task id."""
    community = _load_community(db, community_id)
    member = ws.require_member(db, community.id, user)

    rows = wt.search(db, community.id, q)
    # A plain member sees only their own work in results, exactly as they do
    # everywhere else. Search must not become the way around that.
    if member.role == CommunityRole.MEMBER.value:
        rows = [a for a in rows if a.user_id == user.id]

    counts = wt.attachment_counts(db, [a.task_id for a in rows])
    return {"results": [_tracked(a, a.task, counts) for a in rows]}
