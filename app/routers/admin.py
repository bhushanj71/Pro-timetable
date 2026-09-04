"""
Admin endpoints: user management and statistics.

Two kinds of administrator reach this router. A super admin runs the platform
and sees all of it. A college admin runs one college and sees that.

Which one is asking is never decided here. Routes open to both take
`get_panel_admin` and then narrow themselves through app.services.admin_scope,
which owns every rule about who may be seen and who may be changed; routes
that are platform-level acts keep `get_current_admin` and stay super-admin
only. Regular professors are unaffected — their own routes remain scoped to
their own user_id.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.database import get_db
from app.deps import get_current_admin, get_panel_admin
from app.models import AIConversation, College, Event, OrgStatus, Reminder, Task, User
from app.schemas import (
    AdminCreateUser,
    AdminPasswordReset,
    AdminStats,
    AdminUserOut,
    AdminUserUpdate,
)
from app.security import hash_password
from app.services import admin_scope as scope

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _to_admin_out(db: Session, user: User, admin: User | None = None) -> AdminUserOut:
    college_name = None
    if user.admin_college_id:
        row = db.get(College, user.admin_college_id)
        college_name = row.name if row else None
    return AdminUserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        department=user.department,
        designation=user.designation,
        college=user.college,
        timezone=user.timezone,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        event_count=db.query(Event).filter(Event.user_id == user.id).count(),
        task_count=db.query(Task).filter(Task.user_id == user.id).count(),
        college_id=user.college_id,
        admin_college_id=user.admin_college_id,
        admin_college=college_name,
        # So the panel can grey out a row instead of offering buttons that
        # will 403. The server still checks; this only stops the UI lying.
        manageable=scope.may_manage(admin, user) if admin else True,
    )


@router.get("/stats", response_model=AdminStats)
def system_stats(db: Session = Depends(get_db), admin: User = Depends(get_panel_admin)):
    """Platform-wide for a super admin; the college's own figures otherwise.

    A college admin seeing the platform's totals would be told how many
    accounts and events exist outside their college, which is exactly the
    information the scoping is there to withhold.
    """
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    users = scope.scope_users(db.query(User), admin)

    if admin.is_admin:
        events = db.query(Event)
        tasks = db.query(Task)
        reminders = db.query(Reminder)
        conversations = db.query(AIConversation)
    else:
        # One subquery, reused: the alternative is loading every id in the
        # college into Python to build four IN clauses out of it.
        mine = scope.scope_users(db.query(User.id), admin).subquery()
        ids = db.query(mine.c.id)
        events = db.query(Event).filter(Event.user_id.in_(ids))
        tasks = db.query(Task).filter(Task.user_id.in_(ids))
        reminders = db.query(Reminder).filter(Reminder.user_id.in_(ids))
        conversations = db.query(AIConversation).filter(AIConversation.user_id.in_(ids))

    return AdminStats(
        total_users=users.count(),
        active_users=users.filter(User.is_active.is_(True)).count(),
        # "Administrators" has to mean the same thing as the Role column
        # beside it. Counting only is_admin told a college admin their college
        # had none while the list below showed them one -- themselves.
        admin_users=(
            users.filter(User.is_admin.is_(True)).count()
            if admin.is_admin
            else users.filter(
                (User.is_admin.is_(True)) | (User.admin_college_id.isnot(None))
            ).count()
        ),
        total_events=events.count(),
        total_tasks=tasks.count(),
        total_reminders=reminders.count(),
        pending_reminders=reminders.filter(Reminder.is_sent.is_(False)).count(),
        ai_conversations=conversations.count(),
        new_users_this_week=users.filter(User.created_at >= week_ago).count(),
    )


# The sentinel for "has not joined a college". A plain empty college_id has to
# keep meaning "no filter", or the page could never ask for everybody, and on
# this deployment the accounts with no college are most of them -- so they need
# a way to be asked for rather than being unreachable.
NO_COLLEGE = "none"


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    q: str | None = Query(default=None, description="Search name or email"),
    college_id: str | None = Query(default=None, description=f"A college id, or '{NO_COLLEGE}'"),
    department_id: str | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_panel_admin),
):
    # Scope first, filter second. These narrow what the caller may already see;
    # nothing here can widen it, which is why a college admin asking for
    # another college's id gets an empty list rather than its members.
    query = scope.scope_users(db.query(User), admin)
    if college_id == NO_COLLEGE:
        query = query.filter(User.college_id.is_(None))
    elif college_id:
        query = query.filter(User.college_id == college_id)
    if department_id:
        query = query.filter(User.department_id == department_id)
    if q:
        like = f"%{q}%"
        query = query.filter((User.name.ilike(like)) | (User.email.ilike(like)))
    users = query.order_by(User.created_at.desc()).all()
    return [_to_admin_out(db, u, admin) for u in users]


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: AdminCreateUser, db: Session = Depends(get_db), admin: User = Depends(get_panel_admin)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An account with this email already exists")

    if not admin.is_admin and payload.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a super admin can create an administrator.")

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin if admin.is_admin else False,
        department=payload.department,
        designation=payload.designation,
    )
    if not admin.is_admin:
        # Created inside the college, so it lands inside the college. Without
        # this the new account has no college and immediately falls outside
        # the list of the very administrator who just made it.
        user.college_id = admin.admin_college_id
        college = db.get(College, admin.admin_college_id)
        if college:
            user.college = college.name
    db.add(user)
    db.commit()
    db.refresh(user)
    return _to_admin_out(db, user)


@router.get("/users/{user_id}", response_model=AdminUserOut)
def get_user(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_panel_admin)):
    return _to_admin_out(db, scope.load_visible(db, admin, user_id), admin)


@router.put("/users/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_panel_admin),
):
    user = scope.load_manageable(db, admin, user_id)

    updates = payload.model_dump(exclude_unset=True)
    scope.reject_privileged(admin, updates)

    # Guard against an admin locking everyone out of the admin panel.
    if user.id == admin.id:
        if updates.get("is_admin") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove your own admin rights")
        if updates.get("is_active") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")

    if updates.get("is_admin") is False:
        remaining = db.query(User).filter(User.is_admin.is_(True), User.id != user.id).count()
        if remaining == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot remove the last administrator")

    if "email" in updates:
        clash = db.query(User).filter(User.email == updates["email"], User.id != user.id).first()
        if clash:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That email is already in use")

    for field, value in updates.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return _to_admin_out(db, user)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    payload: AdminPasswordReset,
    db: Session = Depends(get_db),
    admin: User = Depends(get_panel_admin),
):
    user = scope.load_manageable(db, admin, user_id)
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True, "message": f"Password reset for {user.email}"}


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_panel_admin)):
    user = scope.load_manageable(db, admin, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if user.is_admin:
        remaining = db.query(User).filter(User.is_admin.is_(True), User.id != user.id).count()
        if remaining == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete the last administrator")

    # Cascade deletes remove the user's events, tasks, reminders, and AI history.
    db.delete(user)
    db.commit()
    return None


@router.get("/users/{user_id}/events")
def user_events(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_panel_admin)):
    """Read-only window into a professor's schedule, for support purposes."""
    scope.load_visible(db, admin, user_id)
    events = (
        db.query(Event)
        .filter(Event.user_id == user_id)
        .order_by(Event.start_datetime.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": e.id,
            "title": e.title,
            "event_type": e.event_type,
            "start": e.start_datetime.isoformat(),
            "end": e.end_datetime.isoformat(),
        }
        for e in events
    ]


# ---------------------------------------------------------------------------
# Appointing a college administrator
#
# Super admin only, and deliberately not part of the general user update: this
# is the one call in the application that hands out administrative power, so it
# is its own endpoint with its own gate rather than a field on a form that
# already accepts six harmless ones.
# ---------------------------------------------------------------------------
class CollegeAdminIn(BaseModel):
    college_id: str


@router.post("/users/{user_id}/college-admin", response_model=AdminUserOut)
def grant_college_admin(
    user_id: str,
    payload: CollegeAdminIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Put someone in charge of the college they belong to."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    college = db.get(College, payload.college_id)
    if not college:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "College not found")
    if college.status != OrgStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That college has been archived.")

    if user.is_admin:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{user.name} is already a super admin and administers every college.",
        )

    # The rule the whole feature rests on: you administer the college you are
    # in. Appointing an outsider would create an administrator whose own
    # profile sits outside the scope they were just given -- they would not
    # appear in their own member list, and moving their profile later would
    # silently move their authority with it.
    if not user.college_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{user.name} has not chosen a college yet, so there is nothing to put them in charge of.",
        )
    if user.college_id != college.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{user.name} belongs to a different college. "
            "An administrator has to be a member of the college they run.",
        )

    user.admin_college_id = college.id
    db.commit()
    db.refresh(user)
    return _to_admin_out(db, user, admin)


@router.delete("/users/{user_id}/college-admin", response_model=AdminUserOut)
def revoke_college_admin(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Stand someone down. Their account and their membership are untouched;
    only the panel goes away."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.admin_college_id = None
    db.commit()
    db.refresh(user)
    return _to_admin_out(db, user, admin)
